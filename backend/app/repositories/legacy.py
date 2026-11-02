from __future__ import annotations

import os
import json
import math
import re
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Header, Request, Depends
from ..security.policy import require_account_user
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from openpyxl import load_workbook


PROJECT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_DIR / "data"
DATABASE_PATH = Path(os.environ.get("ZHICAI_DATABASE_PATH", os.environ.get("ZHICAO_DATABASE_PATH", DATA_DIR / "zhicai_demo.sqlite3")))
PRODUCTS_XLSX = DATA_DIR / "理财产品演示数据.xlsx"
BILLS_XLSX = DATA_DIR / "账单明细演示数据.xlsx"
CATEGORY_COLORS = {
    "餐饮": "#D08D69",
    "购物": "#7D9D8C",
    "出行": "#8193B6",
    "生活用品": "#C4AA67",
    "其他": "#AE9CB9",
}
CATEGORY_ORDER = ["餐饮", "购物", "出行", "生活用品", "其他"]


def connect_db() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "products" in tables:
        if "wealth_products" not in tables:
            connection.execute("ALTER TABLE products RENAME TO wealth_products")
        else:
            for table in ("products", "wealth_products"):
                columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                if "advantage" not in columns:
                    connection.execute(f'ALTER TABLE "{table}" ADD COLUMN advantage TEXT NOT NULL DEFAULT \'\'')
            connection.execute(
                """INSERT OR IGNORE INTO wealth_products
                (id, name, issuer, risk_level, annualized_rate_pct, term_days,
                 min_purchase, is_open, is_redeemable, advantage)
                SELECT id, name, issuer, risk_level, annualized_rate_pct, term_days,
                       min_purchase, is_open, is_redeemable, advantage
                FROM products"""
            )
            connection.execute("DROP TABLE products")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS wealth_products (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            issuer TEXT NOT NULL,
            risk_level INTEGER NOT NULL,
            annualized_rate_pct REAL NOT NULL,
            term_days INTEGER NOT NULL,
            min_purchase REAL NOT NULL,
            is_open INTEGER NOT NULL,
            is_redeemable INTEGER NOT NULL,
            advantage TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS bill_transactions (
            id TEXT PRIMARY KEY,
            transaction_date TEXT NOT NULL,
            month TEXT NOT NULL,
            direction TEXT NOT NULL,
            counterparty TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_bills_month_date ON bill_transactions(month, transaction_date);
        CREATE INDEX IF NOT EXISTS idx_bills_category ON bill_transactions(category);
        CREATE TABLE IF NOT EXISTS source_state (
            filename TEXT PRIMARY KEY,
            modified_ns INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_messages (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL CHECK(role IN ('user', 'agent')),
            text TEXT NOT NULL,
            action_json TEXT,
            actions_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            currency TEXT NOT NULL DEFAULT 'CNY',
            available_balance REAL NOT NULL CHECK(available_balance >= 0),
            risk_capacity_level INTEGER NOT NULL CHECK(risk_capacity_level BETWEEN 1 AND 5),
            monthly_salary REAL NOT NULL CHECK(monthly_salary >= 0),
            salary_day INTEGER NOT NULL CHECK(salary_day BETWEEN 1 AND 31),
            monthly_expenses REAL NOT NULL CHECK(monthly_expenses >= 0),
            reserve_amount REAL NOT NULL CHECK(reserve_amount >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS investment_holding_lots (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            product_id TEXT NOT NULL REFERENCES wealth_products(id),
            original_principal REAL NOT NULL CHECK(original_principal >= 0),
            remaining_principal REAL NOT NULL CHECK(remaining_principal >= 0),
            profit_amount REAL NOT NULL DEFAULT 0,
            purchase_date TEXT NOT NULL,
            holding_days_override INTEGER,
            status TEXT NOT NULL DEFAULT '持有中',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_holding_lots_account_product
            ON investment_holding_lots(account_id, product_id, purchase_date);
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            task_type TEXT NOT NULL CHECK(task_type IN ('fixed_expense', 'investment', 'redemption')),
            name TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 0 CHECK(amount >= 0),
            frequency TEXT NOT NULL CHECK(frequency IN ('monthly', 'quarterly', 'semiannual', 'weekly')),
            authorized_at TEXT NOT NULL,
            execution_day INTEGER CHECK(execution_day BETWEEN 1 AND 31),
            risk_level INTEGER NOT NULL DEFAULT 1 CHECK(risk_level BETWEEN 1 AND 5),
            status TEXT NOT NULL CHECK(status IN ('运行中', '已暂停', '已取消')),
            product_id TEXT REFERENCES wealth_products(id),
            all_products INTEGER NOT NULL DEFAULT 0 CHECK(all_products IN (0, 1)),
            payee TEXT,
            authorized INTEGER NOT NULL DEFAULT 1 CHECK(authorized IN (0, 1)),
            last_run TEXT,
            last_failure TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_account_status
            ON scheduled_tasks(account_id, status);
        CREATE TABLE IF NOT EXISTS account_transactions (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            transaction_type TEXT NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('in', 'out', 'adjustment')),
            amount REAL NOT NULL CHECK(amount >= 0),
            balance_after REAL NOT NULL CHECK(balance_after >= 0),
            product_id TEXT REFERENCES wealth_products(id),
            task_id TEXT REFERENCES scheduled_tasks(id) ON DELETE SET NULL,
            description TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_account_transactions_account_time
            ON account_transactions(account_id, occurred_at);
        """
    )
    product_columns = {row["name"] for row in connection.execute("PRAGMA table_info(wealth_products)")}
    if "advantage" not in product_columns:
        connection.execute("ALTER TABLE wealth_products ADD COLUMN advantage TEXT NOT NULL DEFAULT ''")


def file_fingerprint(path: Path) -> tuple[int, int]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少数据源文件：{path}")
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def _cell_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _to_product(row: tuple[Any, ...], row_number: int) -> tuple[Any, ...] | None:
    product_id, name, issuer, risk_text, rate_value, term_value, minimum, open_text, redeem_text = row[:9]
    if not product_id or not name:
        return None
    risk_match = re.search(r"R\s*(\d)", _cell_text(risk_text), re.IGNORECASE)
    if not risk_match:
        raise ValueError(f"理财产品第 {row_number} 行风险等级无法识别：{risk_text}")
    rate = float(rate_value or 0)
    if abs(rate) < 1:
        rate *= 100
    term_text = _cell_text(term_value)
    term_days = 0 if term_text == "活期" else int(float(term_value or 0))
    return (
        _cell_text(product_id),
        _cell_text(name),
        _cell_text(issuer),
        int(risk_match.group(1)),
        round(rate, 6),
        term_days,
        round(float(minimum or 0), 2),
        int(_cell_text(open_text) == "可申购"),
        int(_cell_text(redeem_text) == "可赎回"),
        _cell_text(row[9]) if len(row) > 9 else "",
    )


def _iso_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _cell_text(value)
    if not text:
        raise ValueError("账单明细存在空交易日期")
    return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()


def _to_bill(row: tuple[Any, ...], row_number: int) -> tuple[Any, ...] | None:
    transaction_date, month, direction, counterparty, category, amount = row[:6]
    if transaction_date is None and counterparty is None:
        return None
    date_text = _iso_date(transaction_date)
    month_text = _cell_text(month) or date_text[:7]
    return (
        f"{date_text}-{row_number:04d}",
        date_text,
        month_text,
        _cell_text(direction) or "支出",
        _cell_text(counterparty),
        _cell_text(category),
        round(float(amount or 0), 2),
    )


def read_excel_rows(path: Path, expected_sheet: str) -> list[tuple[Any, ...]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if expected_sheet not in workbook.sheetnames:
            raise ValueError(f"{path.name} 中缺少工作表“{expected_sheet}”")
        sheet = workbook[expected_sheet]
        rows = sheet.iter_rows(values_only=True)
        next(rows, None)  # header row
        return [row for row in rows if any(value is not None for value in row)]
    finally:
        workbook.close()


def import_excel_sources(connection: sqlite3.Connection, fingerprints: dict[str, tuple[int, int]]) -> None:
    product_rows = [
        record
        for row_number, row in enumerate(read_excel_rows(PRODUCTS_XLSX, "理财产品"), start=2)
        if (record := _to_product(row, row_number)) is not None
    ]
    bill_rows = [
        record
        for row_number, row in enumerate(read_excel_rows(BILLS_XLSX, "账单明细"), start=2)
        if (record := _to_bill(row, row_number)) is not None
    ]
    if not product_rows or not bill_rows:
        raise ValueError("Excel 数据为空，未更新 SQLite")

    with connection:
        # Upsert preserves holding/transaction foreign keys on Excel refresh.
        connection.execute("DELETE FROM bill_transactions")
        connection.executemany(
            """INSERT INTO wealth_products
            (id, name, issuer, risk_level, annualized_rate_pct, term_days, min_purchase, is_open, is_redeemable, advantage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, issuer=excluded.issuer,
              risk_level=excluded.risk_level, annualized_rate_pct=excluded.annualized_rate_pct,
              term_days=excluded.term_days, min_purchase=excluded.min_purchase,
              is_open=excluded.is_open, is_redeemable=excluded.is_redeemable, advantage=excluded.advantage""",
            product_rows,
        )
        source_ids = {row[0] for row in product_rows}
        for existing in connection.execute("SELECT id FROM wealth_products").fetchall():
            if existing[0] not in source_ids:
                connection.execute("UPDATE wealth_products SET is_open=0 WHERE id=?", (existing[0],))
        connection.executemany(
            """INSERT INTO bill_transactions
            (id, transaction_date, month, direction, counterparty, category, amount)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            bill_rows,
        )
        connection.execute("DELETE FROM source_state")
        connection.executemany(
            "INSERT INTO source_state(filename, modified_ns, size_bytes) VALUES (?, ?, ?)",
            [(name, mtime, size) for name, (mtime, size) in fingerprints.items()],
        )


def initialize_database() -> None:
    fingerprints = {
        PRODUCTS_XLSX.name: file_fingerprint(PRODUCTS_XLSX),
        BILLS_XLSX.name: file_fingerprint(BILLS_XLSX),
    }
    with closing(connect_db()) as connection:
        create_schema(connection)
        stored = {
            row["filename"]: (row["modified_ns"], row["size_bytes"])
            for row in connection.execute("SELECT filename, modified_ns, size_bytes FROM source_state")
        }
        counts = (
            connection.execute("SELECT COUNT(*) FROM wealth_products").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM bill_transactions").fetchone()[0],
        )
        if fingerprints != stored or not all(counts):
            import_excel_sources(connection, fingerprints)


def products_from_db(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute("SELECT * FROM wealth_products ORDER BY rowid").fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "issuer": row["issuer"],
            "risk": row["risk_level"],
            "rate": row["annualized_rate_pct"],
            "days": row["term_days"],
            "min": row["min_purchase"],
            "open": bool(row["is_open"]),
            "redeemable": bool(row["is_redeemable"]),
            "advantage": row["advantage"],
        }
        for row in rows
    ]


def product_advantage_sort_key(product: dict[str, Any]) -> tuple[int, float, float, str]:
    advantage = _cell_text(product.get("advantage"))
    if "历史月月正收益" in advantage:
        category_rank, holder_count = 0, 0.0
    elif "历史季季正收益" in advantage:
        category_rank, holder_count = 1, 0.0
    else:
        holders = re.search(r"超\s*(\d+(?:\.\d+)?)\s*万人持有", advantage)
        if holders:
            category_rank, holder_count = 2, float(holders.group(1))
        elif "近一周" in advantage and "浏览" in advantage:
            category_rank, holder_count = 3, 0.0
        else:
            category_rank, holder_count = 4, 0.0
    return (category_rank, -holder_count, -float(product.get("rate") or 0), str(product.get("name") or ""))


def bills_from_db(connection: sqlite3.Connection, month: str | None = None) -> list[dict[str, Any]]:
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=422, detail="月份格式应为 YYYY-MM")
    if month:
        rows = connection.execute(
            "SELECT * FROM bill_transactions WHERE month = ? ORDER BY transaction_date, id", (month,)
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM bill_transactions ORDER BY transaction_date, id"
        ).fetchall()
    return [
        {
            "id": row["id"],
            "category": row["category"],
            "merchant": row["counterparty"],
            "date": row["transaction_date"],
            "amount": round(row["amount"], 2),
        }
        for row in rows
    ]


def categories_from_db(connection: sqlite3.Connection, month: str | None = None) -> list[dict[str, Any]]:
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=422, detail="月份格式应为 YYYY-MM")
    query = "SELECT category, COUNT(*) AS count, ROUND(SUM(amount), 2) AS amount FROM bill_transactions"
    parameters: tuple[Any, ...] = ()
    if month:
        query += " WHERE month = ?"
        parameters = (month,)
    rows = connection.execute(query + " GROUP BY category", parameters).fetchall()
    result = [
        {
            "name": row["category"],
            "amount": row["amount"] or 0,
            "count": row["count"],
            "color": CATEGORY_COLORS.get(row["category"], "#8193B6"),
        }
        for row in rows
    ]
    return sorted(result, key=lambda item: CATEGORY_ORDER.index(item["name"]) if item["name"] in CATEGORY_ORDER else len(CATEGORY_ORDER))


def recurring_from_db(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT transaction_date, counterparty, amount FROM bill_transactions "
        "WHERE counterparty = ? ORDER BY transaction_date",
        ("视频会员",),
    ).fetchall()
    return [
        {"date": row["transaction_date"], "merchant": row["counterparty"], "amount": row["amount"]}
        for row in rows
    ]


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    from ..config import load_settings
    from ..jobs.scheduler import scheduler
    from ..services.commands import create_command_schema, seed_account
    from ..services.payees import seed_demo_payees
    import asyncio
    load_settings()
    with closing(connect_db()) as connection, connection:
        create_command_schema(connection)
        seed_account(connection)
        seed_demo_payees(connection)
        from ..security.auth import create_auth_schema
        create_auth_schema(connection)
    job = asyncio.create_task(scheduler())
    try:
        yield
    finally:
        job.cancel()
        try:
            await job
        except asyncio.CancelledError:
            pass


app = FastAPI(title="知财 Agent 服务", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5180", "http://127.0.0.1:5180"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


class ConversationMessage(BaseModel):
    id: str | int
    role: str = Field(pattern="^(user|agent)$")
    text: str = Field(min_length=1, max_length=10000)
    action: dict[str, Any] | None = None
    actions: list[dict[str, Any]] | None = None
    parent_message_id: str | None = Field(default=None,max_length=100)


def messages_from_db(connection: sqlite3.Connection, conversation_id: str = 'default') -> list[dict[str, Any]]:
    from ..services.conversations import require_conversation
    require_conversation(connection,conversation_id)
    rows = connection.execute(
        "SELECT message_id, role, text, action_json, actions_json,parent_message_id,topic_id,message_kind "
        "FROM conversation_messages WHERE conversation_id=? ORDER BY sequence", (conversation_id,)
    ).fetchall()
    return [
        {
            "id": row["message_id"],
            "role": row["role"],
            "text": row["text"],
            **({"parent_message_id":row['parent_message_id']} if row['parent_message_id'] else {}),
            **({"topic_id":row['topic_id']} if row['topic_id'] else {}),
            'message_kind':row['message_kind'],
            **({"action": json.loads(row["action_json"])} if row["action_json"] else {}),
            **({"actions": json.loads(row["actions_json"])} if row["actions_json"] else {}),
        }
        for row in rows
    ]


DEMO_ACCOUNT_ID = "demo-account"


def _number(value: Any, field: str, default: float = 0) -> float:
    try:
        result = float(value if value is not None else default)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{field} 必须是数字")
    if not math.isfinite(result) or result < 0:
        raise HTTPException(status_code=422, detail=f"{field} 必须是非负有限数字")
    return result


def _account_state_from_db(connection: sqlite3.Connection, account_id: str = DEMO_ACCOUNT_ID) -> dict[str, Any] | None:
    account = connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if not account:
        return None
    holding_rows = connection.execute(
        "SELECT * FROM investment_holding_lots WHERE account_id = ? AND remaining_principal > 0 "
        "ORDER BY product_id, purchase_date, id",
        (account_id,),
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for row in holding_rows:
        item = grouped.setdefault(row["product_id"], {"productId": row["product_id"], "principal": 0.0, "profit": 0.0, "lots": []})
        item["principal"] += row["remaining_principal"]
        item["profit"] += row["profit_amount"]
        lot = {"id": row["id"], "principal": row["remaining_principal"], "originalPrincipal": row["original_principal"], "purchaseDate": row["purchase_date"]}
        if row["holding_days_override"] is not None:
            lot["holdingDays"] = row["holding_days_override"]
        item["lots"].append(lot)
    task_rows = connection.execute(
        "SELECT * FROM scheduled_tasks WHERE account_id = ? ORDER BY rowid", (account_id,)
    ).fetchall()
    tasks = []
    for row in task_rows:
        task = {
            "id": row["id"], "type": row["task_type"], "name": row["name"], "amount": row["amount"],
            "frequency": row["frequency"], "authorizedAt": row["authorized_at"], "risk": row["risk_level"],
            "status": row["status"], "authorized": bool(row["authorized"]), "allProducts": bool(row["all_products"]),
        }
        if row["execution_day"] is not None:
            task["executionDay"] = row["execution_day"]
        if row["product_id"] is not None:
            task["productId"] = row["product_id"]
        elif row["all_products"]:
            task["productId"] = "all"
        if row["payee"] is not None:
            task["payee"] = row["payee"]
        if 'payee_id' in row.keys() and row['payee_id']:
            task['payeeId']=row['payee_id']
            task['payeeVersion']=row['payee_version']
        if row["last_run"] is not None:
            task["lastRun"] = row["last_run"]
        if row["last_failure"] is not None:
            task["lastFailure"] = row["last_failure"]
        tasks.append(task)
    receipts = [row["description"] for row in connection.execute(
        "SELECT description FROM account_transactions WHERE account_id = ? ORDER BY rowid DESC LIMIT 100",
        (account_id,),
    ).fetchall()]
    return {
        "balance": account["available_balance"], "riskLevel": account["risk_capacity_level"],
        "monthlySalary": account["monthly_salary"], "salaryDay": account["salary_day"],
        "monthlyExpenses": account["monthly_expenses"], "reserveAmount": account["reserve_amount"],
        "holdings": list(grouped.values()), "tasks": tasks, "receipts": receipts,
    }


def _save_account_state(
    connection: sqlite3.Connection,
    state: dict[str, Any],
    transactions: list[dict[str, Any]] | None = None,
    *,
    clear_transactions: bool = False,
    account_id: str = DEMO_ACCOUNT_ID,
) -> None:
    balance = _number(state.get("balance"), "balance")
    risk_level = int(state.get("riskLevel", 2))
    if not 1 <= risk_level <= 5:
        raise HTTPException(status_code=422, detail="riskLevel 必须在 1 到 5 之间")
    salary_day = int(state.get("salaryDay", 15))
    if not 1 <= salary_day <= 31:
        raise HTTPException(status_code=422, detail="salaryDay 必须在 1 到 31 之间")
    monthly_salary = _number(state.get("monthlySalary"), "monthlySalary", 10000)
    monthly_expenses = _number(state.get("monthlyExpenses"), "monthlyExpenses", 6000)
    reserve_amount = _number(state.get("reserveAmount"), "reserveAmount", 2000)
    connection.execute(
        """INSERT INTO accounts
        (id, display_name, currency, available_balance, risk_capacity_level, monthly_salary,
         salary_day, monthly_expenses, reserve_amount, updated_at)
        VALUES (?, ?, 'CNY', ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name, currency=excluded.currency,
            available_balance=excluded.available_balance, risk_capacity_level=excluded.risk_capacity_level,
            monthly_salary=excluded.monthly_salary, salary_day=excluded.salary_day,
            monthly_expenses=excluded.monthly_expenses, reserve_amount=excluded.reserve_amount,
            updated_at=CURRENT_TIMESTAMP""",
        (account_id, "知财演示账户", balance, risk_level, monthly_salary, salary_day, monthly_expenses, reserve_amount),
    )
    connection.execute("DELETE FROM investment_holding_lots WHERE account_id = ?", (account_id,))
    connection.execute("DELETE FROM scheduled_tasks WHERE account_id = ?", (account_id,))
    for holding in state.get("holdings", []):
        if not isinstance(holding, dict):
            continue
        product_id = str(holding.get("productId", "")).strip()
        if not product_id:
            continue
        if not connection.execute("SELECT 1 FROM wealth_products WHERE id = ?", (product_id,)).fetchone():
            raise HTTPException(status_code=422, detail=f"持仓关联的产品不存在：{product_id}")
        principal = _number(holding.get("principal"), "holding.principal")
        profit = float(holding.get("profit") or 0)
        if not math.isfinite(profit):
            raise HTTPException(status_code=422, detail="holding.profit 必须是有限数字")
        lots = holding.get("lots") if isinstance(holding.get("lots"), list) else []
        normalized_lots = []
        for lot in lots:
            if not isinstance(lot, dict):
                continue
            remaining = _number(lot.get("principal"), "lot.principal")
            if remaining <= 0:
                continue
            purchase_date = str(lot.get("purchaseDate") or date.today().isoformat())
            try:
                date.fromisoformat(purchase_date)
            except ValueError:
                raise HTTPException(status_code=422, detail="持仓买入日期格式应为 YYYY-MM-DD")
            original = max(remaining, _number(lot.get("originalPrincipal"), "lot.originalPrincipal", remaining))
            override = lot.get("holdingDays")
            normalized_lots.append((str(lot.get("id") or f"{product_id}-lot-{len(normalized_lots) + 1}"), remaining, original, purchase_date, int(override) if override is not None else None))
        lot_total = sum(lot[1] for lot in normalized_lots)
        if principal > 0 and not normalized_lots:
            normalized_lots = [(f"{product_id}-lot-1", principal, principal, date.today().isoformat(), None)]
            lot_total = principal
        if normalized_lots and abs(lot_total - principal) > 0.01:
            scale = principal / lot_total if lot_total else 0
            normalized_lots = [(lot_id, remaining * scale, max(original, remaining * scale), purchase_date, override) for lot_id, remaining, original, purchase_date, override in normalized_lots]
            lot_total = principal
        for lot_id, remaining, original, purchase_date, override in normalized_lots:
            lot_profit = profit * (remaining / lot_total) if lot_total else 0
            connection.execute(
                """INSERT INTO investment_holding_lots
                (id, account_id, product_id, original_principal, remaining_principal,
                 profit_amount, purchase_date, holding_days_override, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '持有中', CURRENT_TIMESTAMP)""",
                (lot_id, account_id, product_id, original, remaining, lot_profit, purchase_date, override),
            )
    for task in state.get("tasks", []):
        if not isinstance(task, dict):
            continue
        task_type = str(task.get("type") or "")
        if task_type not in {"fixed_expense", "investment", "redemption"}:
            raise HTTPException(status_code=422, detail=f"未知定时任务类型：{task_type}")
        frequency = str(task.get("frequency") or "monthly")
        if frequency not in {"monthly", "quarterly", "semiannual", "weekly"}:
            raise HTTPException(status_code=422, detail=f"未知任务周期：{frequency}")
        status = str(task.get("status") or "运行中")
        if status not in {"运行中", "已暂停", "已取消"}:
            raise HTTPException(status_code=422, detail=f"未知任务状态：{status}")
        raw_product = task.get("productId")
        all_products = int(raw_product == "all" or bool(task.get("allProducts")))
        product_id = None if all_products or raw_product is None else str(raw_product)
        if product_id and not connection.execute("SELECT 1 FROM wealth_products WHERE id = ?", (product_id,)).fetchone():
            raise HTTPException(status_code=422, detail=f"定时任务关联的产品不存在：{product_id}")
        task_id = str(task.get("id") or f"task-{len(state.get('tasks', []))}")
        amount = _number(task.get("amount"), "task.amount")
        execution_day = task.get("executionDay")
        connection.execute(
            """INSERT INTO scheduled_tasks
            (id, account_id, task_type, name, amount, frequency, authorized_at, execution_day,
             risk_level, status, product_id, all_products, payee, authorized, last_run, last_failure,payee_id,payee_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, account_id, task_type, str(task.get("name") or task_type), amount, frequency,
             str(task.get("authorizedAt") or date.today().isoformat()), int(execution_day) if execution_day is not None else None,
             int(task.get("risk") or 1), status, product_id, all_products, task.get("payee"),
             int(bool(task.get("authorized", True))), task.get("lastRun"), task.get("lastFailure"),task.get('payeeId'),task.get('payeeVersion')),
        )
    if clear_transactions:
        connection.execute("DELETE FROM account_transactions WHERE account_id = ?", (account_id,))
        connection.execute('DELETE FROM transfer_records WHERE account_id=?',(account_id,))
    for event in transactions or []:
        if not isinstance(event, dict):
            continue
        direction = str(event.get("direction") or "adjustment")
        if direction not in {"in", "out", "adjustment"}:
            direction = "adjustment"
        product_id = event.get("productId")
        if product_id and not connection.execute("SELECT 1 FROM wealth_products WHERE id = ?", (str(product_id),)).fetchone():
            product_id = None
        connection.execute(
            """INSERT OR IGNORE INTO account_transactions
            (id, account_id, transaction_type, direction, amount, balance_after, product_id, task_id, description, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
            (str(event.get("id") or f"txn-{datetime.now().timestamp()}"), account_id,
             str(event.get("transactionType") or "adjustment"), direction,
             _number(event.get("amount"), "transaction.amount"), _number(event.get("balanceAfter"), "transaction.balanceAfter"),
             str(product_id) if product_id else None, str(event.get("description") or "账户余额变动"),
             str(event.get("occurredAt") or datetime.now().isoformat(timespec="seconds"))),
        )


@app.get("/api/health")
def health() -> dict[str, Any]:
    with closing(connect_db()) as connection:
        return {
            "status": "ok",
            "api_version": "0.3.0",
            "database": DATABASE_PATH.name,
            "products": connection.execute("SELECT COUNT(*) FROM wealth_products").fetchone()[0],
            "bills": connection.execute("SELECT COUNT(*) FROM bill_transactions").fetchone()[0],
        }


@app.get("/api/account-state", dependencies=[Depends(require_account_user)])
def get_account_state(request: Request) -> dict[str, Any]:
    with closing(connect_db()) as connection:
        state = _account_state_from_db(connection, request.state.actor.account_id)
    if state is None:
        raise HTTPException(status_code=404, detail="演示账户尚未初始化")
    return state


@app.post("/api/account-state/initialize", dependencies=[Depends(require_account_user)])
def initialize_account_state(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    state = payload.get("state", payload)
    if not isinstance(state, dict):
        raise HTTPException(status_code=422, detail="state 必须是对象")
    account_id = request.state.actor.account_id
    with closing(connect_db()) as connection, connection:
        if _account_state_from_db(connection, account_id) is None:
            from ..services.commands import seed_account
            seed_account(connection, account_id=account_id)
        current = _account_state_from_db(connection, account_id)
    return current or {}


@app.put("/api/account-state", dependencies=[Depends(require_account_user)])
def save_account_state(payload: dict[str, Any]) -> dict[str, str]:
    raise HTTPException(status_code=410, detail="账户快照写入已停用，请使用预览/确认操作接口")


@app.post("/api/account-state/reset", dependencies=[Depends(require_account_user)])
def reset_account_state(request: Request) -> dict[str, str]:
    """Reset the isolated demo back to its initial account state."""
    account_id = request.state.actor.account_id
    with closing(connect_db()) as connection, connection:
        from ..services.commands import seed_account
        connection.execute("DELETE FROM operation_previews")
        connection.execute("DELETE FROM task_runs")
        connection.execute("DELETE FROM agent_audit")
        connection.execute("DELETE FROM conversation_messages")
        from ..services.conversations import clear_context
        clear_context(connection)
        seed_account(connection, reset=True, account_id=account_id)
    return {"status": "reset"}


@app.get("/api/bootstrap", dependencies=[Depends(require_account_user)])
def bootstrap(request: Request, x_conversation_id: str = Header(default='default')) -> dict[str, Any]:
    with closing(connect_db()) as connection:
        from ..services.conversations import require_conversation
        require_conversation(connection,x_conversation_id,request.state.actor.user_id)
        return {
            "products": products_from_db(connection),
            "bills": bills_from_db(connection),
            "categories": categories_from_db(connection),
            "evidence": {"recurring": recurring_from_db(connection)},
            "messages": messages_from_db(connection,x_conversation_id),
            'conversation_id': x_conversation_id,
        }


@app.get("/api/conversation/messages", dependencies=[Depends(require_account_user)])
def get_conversation_messages(request: Request, x_conversation_id: str = Header(default='default')) -> list[dict[str, Any]]:
    with closing(connect_db()) as connection:
        from ..services.conversations import require_conversation
        require_conversation(connection,x_conversation_id,request.state.actor.user_id)
        return messages_from_db(connection,x_conversation_id)


@app.post("/api/conversation/messages", dependencies=[Depends(require_account_user)])
def save_conversation_message(message: ConversationMessage, request: Request, x_conversation_id: str = Header(default='default')) -> dict[str, str]:
    from ..services.conversations import save_page_message,require_conversation
    with closing(connect_db()) as connection, connection:
        require_conversation(connection,x_conversation_id,request.state.actor.user_id)
        save_page_message(connection,x_conversation_id,message)

    return {"status": "saved", "id": str(message.id)}


@app.delete("/api/conversation/messages", dependencies=[Depends(require_account_user)])
def clear_conversation_messages(request: Request, x_conversation_id: str = Header(default='default')) -> dict[str, Any]:
    with closing(connect_db()) as connection, connection:
        from ..services.conversations import clear_context,require_conversation
        require_conversation(connection,x_conversation_id,request.state.actor.user_id)
        clear_context(connection,x_conversation_id)
        deleted = connection.execute("DELETE FROM conversation_messages WHERE conversation_id=?",(x_conversation_id,)).rowcount
    return {"status": "cleared", "deleted": deleted}


@app.get("/api/products", dependencies=[Depends(require_account_user)])
def get_products() -> list[dict[str, Any]]:
    with closing(connect_db()) as connection:
        return products_from_db(connection)


@app.get("/api/products/recommend", dependencies=[Depends(require_account_user)])
def recommend_products(
    risk_min: int = Query(default=1, ge=1, le=5),
    risk_max: int = Query(default=5, ge=1, le=5),
    only_open: bool = Query(default=True),
    limit: int = Query(default=2, ge=1, le=50),
) -> list[dict[str, Any]]:
    if risk_min > risk_max:
        raise HTTPException(status_code=422, detail="risk_min 不能大于 risk_max")
    with closing(connect_db()) as connection:
        candidates = products_from_db(connection)
    candidates = [
        product for product in candidates
        if risk_min <= int(product["risk"]) <= risk_max and (not only_open or product["open"])
    ]
    return sorted(candidates, key=product_advantage_sort_key)[:limit]


@app.get("/api/bills", dependencies=[Depends(require_account_user)])
def get_bills(month: str | None = Query(default=None)) -> list[dict[str, Any]]:
    with closing(connect_db()) as connection:
        return bills_from_db(connection, month)


@app.get("/api/categories", dependencies=[Depends(require_account_user)])
def get_categories(month: str | None = Query(default=None)) -> list[dict[str, Any]]:
    with closing(connect_db()) as connection:
        return categories_from_db(connection, month)


