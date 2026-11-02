"""D1 HTTP 适配器：让现有同步 sqlite3 代码在 Cloudflare 上原样运行。

Cloudflare D1 没有交互式事务，只有 batch() 原子提交。本模块把
sqlite3.Connection 的常用子集映射到 D1 HTTP API：

    读 (SELECT / PRAGMA / WITH)  → 立即发起 HTTP 请求，返回结果
    写 (INSERT / UPDATE / ...)   → 排队，commit() 时一次 batch 提交

因此事务内「写完立刻读回」拿不到数据，调用方需避免该模式
（backend 中仅 commands.apply() 末尾一处，已改为直接返回内存 state）。

未配置 D1 环境变量时 connect_db() 仍走本地 sqlite3，便于离线开发与测试。

环境变量：
    ZHICAI_D1_ACCOUNT_ID     Cloudflare 账号 ID
    ZHICAI_D1_DATABASE_ID    D1 数据库 ID
    ZHICAI_D1_API_TOKEN      API Token（需 D1 Edit 权限）
    ZHICAI_D1_TIMEOUT        单次请求超时秒数，默认 30
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from typing import Any, Iterable, Iterator, Sequence

API_ROOT = "https://api.cloudflare.com/client/v4"

# 这些首关键字视为读操作，立即执行；其余归为写操作，进入事务缓冲。
_READ_KEYWORDS = ("SELECT", "PRAGMA", "WITH", "EXPLAIN")
# 事务控制语句由适配器自身处理，不发送给 D1。
_TX_KEYWORDS = ("BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE")


class D1Error(RuntimeError):
    """D1 请求失败。"""


def d1_configured() -> bool:
    return all(
        os.getenv(name, "").strip()
        for name in ("ZHICAI_D1_ACCOUNT_ID", "ZHICAI_D1_DATABASE_ID", "ZHICAI_D1_API_TOKEN")
    )


def _leading_keyword(sql: str) -> str:
    """跳过注释与空白，取出首个关键字（大写）。"""
    text = sql.lstrip()
    while text.startswith("--") or text.startswith("/*"):
        if text.startswith("--"):
            newline = text.find("\n")
            if newline == -1:
                return ""
            text = text[newline + 1 :].lstrip()
        else:
            close = text.find("*/")
            if close == -1:
                return ""
            text = text[close + 2 :].lstrip()
    return text.split(None, 1)[0].upper() if text else ""


class Row:
    """对齐 sqlite3.Row：支持 row["col"]、row[0]、dict(row)、row.keys()。"""

    __slots__ = ("_keys", "_values", "_index")

    def __init__(self, keys: Sequence[str], values: Sequence[Any]) -> None:
        self._keys = tuple(keys)
        self._values = tuple(values)
        self._index = {key: i for i, key in enumerate(self._keys)}

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        try:
            return self._values[self._index[key]]
        except KeyError:
            raise IndexError(f"没有名为 {key!r} 的列") from None

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> tuple[str, ...]:
        return self._keys

    def __repr__(self) -> str:
        pairs = ", ".join(f"{k}={v!r}" for k, v in zip(self._keys, self._values))
        return f"Row({pairs})"


def _make_row(payload: dict[str, Any], factory: Any) -> Any:
    keys = tuple(payload.keys())
    values = tuple(payload.values())
    if factory is None:
        return payload
    if factory is Row:
        return Row(keys, values)
    # 兼容自定义 row_factory（如 dict）——本项目只用到 sqlite3.Row。
    return factory(None, values) if callable(factory) else Row(keys, values)


class Cursor:
    """只实现代码实际用到的部分：fetchone / fetchall / 迭代 / rowcount。"""

    __slots__ = ("_rows", "_pos", "rowcount", "lastrowid", "description")

    def __init__(self, rows: list[Any], rowcount: int = -1, description: Any = None) -> None:
        self._rows = rows
        self._pos = 0
        self.rowcount = rowcount
        self.lastrowid = None
        self.description = description

    def fetchone(self) -> Any | None:
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self) -> list[Any]:
        remaining = self._rows[self._pos :]
        self._pos = len(self._rows)
        return remaining

    def __iter__(self) -> Iterator[Any]:
        while self._pos < len(self._rows):
            row = self._rows[self._pos]
            self._pos += 1
            yield row

    def __len__(self) -> int:
        return len(self._rows)

    def close(self) -> None:
        self._rows = []


class D1Connection:
    """sqlite3.Connection 的子集，写操作在事务内缓冲、commit 时批量提交。"""

    def __init__(
        self,
        account_id: str,
        database_id: str,
        api_token: str,
        timeout: float = 30.0,
    ) -> None:
        self._url = f"{API_ROOT}/accounts/{account_id}/d1/database/{database_id}/query"
        self._token = api_token
        self._timeout = timeout
        self._pending: list[tuple[str, Sequence[Any]]] = []
        self._in_transaction = False
        self._closed = False
        self.row_factory: Any = Row
        self.total_changes = 0

    # ---------- 内部：HTTP ----------

    def _post(self, statements: list[tuple[str, Sequence[Any]]]) -> list[dict[str, Any]]:
        if not statements:
            return []
        body = json.dumps(
            {"batch": [{"sql": sql, "params": list(params)} for sql, params in statements]},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:500]
                # 429 / 5xx 可重试，4xx 直接失败。
                if exc.code == 429 or exc.code >= 500:
                    last_error = D1Error(f"D1 HTTP {exc.code}: {detail}")
                    time.sleep(0.3 * (attempt + 1))
                    continue
                raise D1Error(f"D1 HTTP {exc.code}: {detail}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = D1Error(f"D1 连接失败: {exc}")
                time.sleep(0.3 * (attempt + 1))
                continue
        else:
            raise last_error or D1Error("D1 请求失败")

        if not payload.get("success"):
            errors = payload.get("errors") or []
            message = "; ".join(str(e.get("message", e)) for e in errors) or "未知错误"
            raise D1Error(f"D1 执行失败: {message}")

        results = payload.get("result") or []
        if len(results) != len(statements):
            raise D1Error(f"D1 返回条目数不匹配：期望 {len(statements)}，实际 {len(results)}")
        return results

    def _read(self, sql: str, params: Sequence[Any]) -> Cursor:
        result = self._post([(sql, params)])[0]
        if result.get("success") is False:
            raise D1Error(f"D1 查询失败: {sql[:120]}")
        rows = [_make_row(item, self.row_factory) for item in (result.get("results") or [])]
        return Cursor(rows)

    def _write_now(self, sql: str, params: Sequence[Any]) -> Cursor:
        result = self._post([(sql, params)])[0]
        changes = int((result.get("meta") or {}).get("changes") or 0)
        self.total_changes += changes
        return Cursor([], rowcount=changes)

    # ---------- 公开接口 ----------

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Cursor:
        if self._closed:
            raise D1Error("连接已关闭")
        keyword = _leading_keyword(sql)
        if not keyword:
            return Cursor([])

        if keyword in _TX_KEYWORDS:
            if keyword == "BEGIN":
                self._in_transaction = True
            elif keyword in ("COMMIT", "END"):
                self.commit()
            elif keyword == "ROLLBACK":
                self.rollback()
            return Cursor([])

        if keyword in _READ_KEYWORDS:
            return self._read(sql, params)

        if self._in_transaction:
            self._pending.append((sql, params))
            return Cursor([], rowcount=0)
        return self._write_now(sql, params)

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]) -> Cursor:
        statements = [(sql, params) for params in seq_of_params]
        if not statements:
            return Cursor([])
        if self._in_transaction:
            self._pending.extend(statements)
            return Cursor([], rowcount=0)
        results = self._post(statements)
        changes = sum(int((r.get("meta") or {}).get("changes") or 0) for r in results)
        self.total_changes += changes
        return Cursor([], rowcount=changes)

    def executescript(self, script: str) -> Cursor:
        """按完整语句切分后逐条排队，避免依赖 D1 的多语句解析。"""
        buffer = ""
        for line in script.splitlines(keepends=True):
            buffer += line
            if sqlite3.complete_statement(buffer):
                statement = buffer.strip()
                buffer = ""
                if statement:
                    keyword = _leading_keyword(statement)
                    if keyword and keyword not in _TX_KEYWORDS:
                        self.execute(statement)
        remainder = buffer.strip()
        if remainder and _leading_keyword(remainder) not in _TX_KEYWORDS:
            self.execute(remainder)
        return Cursor([])

    def commit(self) -> None:
        if not self._pending:
            self._in_transaction = False
            return
        pending, self._pending = self._pending, []
        try:
            results = self._post(pending)
        except Exception:
            # 提交失败：放回缓冲，交由 rollback 清理。
            self._pending = pending
            raise
        self.total_changes += sum(int((r.get("meta") or {}).get("changes") or 0) for r in results)
        self._in_transaction = False

    def rollback(self) -> None:
        self._pending.clear()
        self._in_transaction = False

    def close(self) -> None:
        if self._closed:
            return
        if self._pending:
            # 与 sqlite3 一致：未显式 commit 的写入在关闭时丢弃。
            self._pending.clear()
        self._closed = True

    def __enter__(self) -> "D1Connection":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def connect_d1() -> D1Connection:
    return D1Connection(
        account_id=os.environ["ZHICAI_D1_ACCOUNT_ID"].strip(),
        database_id=os.environ["ZHICAI_D1_DATABASE_ID"].strip(),
        api_token=os.environ["ZHICAI_D1_API_TOKEN"].strip(),
        timeout=float(os.getenv("ZHICAI_D1_TIMEOUT", "30")),
    )
