#!/usr/bin/env python3
"""把本地 SQLite 演示库导出成 Cloudflare D1 可导入的 SQL 文件。

Cloudflare Workers 没有持久化文件系统，data/ 下的 .sqlite3 与 .xlsx
无法随代码部署，必须在构建阶段转成 SQL，再用 wrangler d1 execute 导入。

用法：
    python backend/scripts/export_d1_seed.py
    python backend/scripts/export_d1_seed.py --exclude-runtime
    python backend/scripts/export_d1_seed.py --db data/other.sqlite3 --out cloudflare/d1
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_DIR / "data" / "zhicai_demo.sqlite3"
DEFAULT_OUT = PROJECT_DIR / "cloudflare" / "d1"

# 按外键依赖排序，保证 D1 开启外键检查时插入不失败。
TABLE_ORDER = [
    "wealth_products",
    "accounts",
    "conversations",
    "bill_transactions",
    "context_metadata",
    "scheduled_tasks",
    "investment_holding_lots",
    "account_transactions",
    "saved_payees",
    "transfer_records",
    "conversation_topics",
    "conversation_messages",
    "agent_audit",
    "operation_previews",
    "task_runs",
    "source_state",
]

# 纯运行时状态：预览会过期、任务运行记录随时间增长，演示环境可不导入。
RUNTIME_TABLES = {"operation_previews", "task_runs"}


def quote(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (bytes, bytearray)):
        return "X'" + bytes(value).hex() + "'"
    return "'" + str(value).replace("'", "''") + "'"


def existing_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def ordered_tables(conn: sqlite3.Connection, excluded: set[str]) -> list[str]:
    present = existing_tables(conn) - excluded
    ordered = [name for name in TABLE_ORDER if name in present]
    # 兜底：TABLE_ORDER 未覆盖的新表按字母序补在后面，避免静默丢表。
    leftover = sorted(present - set(ordered))
    if leftover:
        print(f"  注意：以下表不在预定义顺序中，按字母序追加：{', '.join(leftover)}")
    return ordered + leftover


def export_schema(conn: sqlite3.Connection, tables: list[str]) -> str:
    wanted = set(tables)
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
        "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name"
    ).fetchall()

    lines = ["-- 由 export_d1_seed.py 生成，请勿手工编辑。", "PRAGMA foreign_keys = OFF;", ""]
    for kind, name, sql in rows:
        if kind == "table" and name not in wanted:
            continue
        if kind == "index":
            # 索引所属的表被排除时，索引也一并跳过。
            owner = conn.execute(
                "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?", (name,)
            ).fetchone()
            if owner and owner[0] not in wanted:
                continue
        lines.append(f"{sql.strip()};")
        lines.append("")
    # The local SQLite schema has no D1 operation version or distributed quota.
    # Keep these additions in the exporter so a fresh export stays safe.
    if "accounts" in wanted:
        account_columns = {row[1] for row in conn.execute('PRAGMA table_info("accounts")')}
        if "operation_version" not in account_columns:
            lines.append("ALTER TABLE accounts ADD COLUMN operation_version INTEGER NOT NULL DEFAULT 0;")
        if not any(kind == "trigger" and name == "account_version_guard" for kind, name, _ in rows):
            lines.append("""CREATE TRIGGER account_version_guard BEFORE UPDATE ON accounts
WHEN NEW.operation_version != OLD.operation_version + 1
BEGIN
  SELECT RAISE(ABORT, 'stale account state');
END;""")
        lines.append("")
    if "api_quota" not in wanted:
        lines.append("""CREATE TABLE api_quota (
  bucket TEXT NOT NULL,
  actor TEXT NOT NULL,
  count INTEGER NOT NULL,
  PRIMARY KEY(bucket, actor)
);""")
        lines.append("")
    lines.append("PRAGMA foreign_keys = ON;")
    lines.append("")
    return "\n".join(lines)


def export_data(conn: sqlite3.Connection, tables: list[str]) -> str:
    lines = [
        "-- 由 export_d1_seed.py 生成，请勿手工编辑。",
        "PRAGMA foreign_keys = OFF;",
        "",
    ]
    total = 0
    for table in tables:
        columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
        if not columns:
            continue
        col_sql = ", ".join(f'"{c}"' for c in columns)
        rows = conn.execute(f'SELECT {col_sql} FROM "{table}"').fetchall()
        if not rows:
            continue
        lines.append(f"-- {table}: {len(rows)} 行")
        for row in rows:
            values = ", ".join(quote(v) for v in row)
            lines.append(f'INSERT OR IGNORE INTO "{table}" ({col_sql}) VALUES ({values});')
        lines.append("")
        total += len(rows)
        print(f"  {table}: {len(rows)} 行")
    lines.append("PRAGMA foreign_keys = ON;")
    lines.append("")
    print(f"  合计 {total} 行")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 D1 可导入的 schema.sql 与 seed.sql")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="源 SQLite 文件")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出目录")
    parser.add_argument(
        "--exclude-runtime",
        action="store_true",
        help="排除 operation_previews / task_runs 等运行时状态表",
    )
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"源数据库不存在：{args.db}", file=sys.stderr)
        return 1

    excluded = RUNTIME_TABLES if args.exclude_runtime else set()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        tables = ordered_tables(conn, excluded)
        if not tables:
            print("没有可导出的表。", file=sys.stderr)
            return 1

        args.out.mkdir(parents=True, exist_ok=True)
        print(f"源库：{args.db}")
        print(f"导出 {len(tables)} 张表 → {args.out}")

        schema_path = args.out / "schema.sql"
        seed_path = args.out / "seed.sql"
        schema_path.write_text(export_schema(conn, tables), encoding="utf-8")
        print(f"  schema.sql  {schema_path.stat().st_size:,} 字节")
        seed_path.write_text(export_data(conn, tables), encoding="utf-8")
        print(f"  seed.sql    {seed_path.stat().st_size:,} 字节")
    finally:
        conn.close()

    print("\n导入 D1：")
    print(f"  npx wrangler d1 execute zhicai --remote --file={schema_path.as_posix()}")
    print(f"  npx wrangler d1 execute zhicai --remote --file={seed_path.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
