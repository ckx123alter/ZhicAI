"""Preview-bound operations, atomic ledger updates and durable idempotency."""
import hashlib
import json
import secrets
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Lock
from time import monotonic
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from fastapi import HTTPException
from ..repositories import legacy as db
from .finance import money, today, forecast, monthly
from .payees import OneTimeRecipient,one_time_summary


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["purchase", "redemption", "transfer", "task", "cancel_task", "risk"]
    amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    product_id: str | None = Field(default=None, max_length=100)
    task_id: str | None = Field(default=None, max_length=100)
    task_type: Literal["fixed_expense", "investment", "redemption"] | None = None
    name: str = Field(default="定时任务", max_length=60)
    payee: str | None = Field(default=None, min_length=1, max_length=60)
    payee_id: str | None = Field(default=None,max_length=80)
    payee_version: int | None = Field(default=None,ge=1)
    manual_recipient: OneTimeRecipient | None = None
    frequency: Literal["monthly", "quarterly", "semiannual"] = "monthly"
    execution_day: int | None = Field(default=None, ge=1, le=31)
    answers: list[Literal[1, 3, 5]] | None = None

    @field_validator("amount")
    @classmethod
    def finite(cls, value):
        if value is not None and not value.is_finite():
            raise ValueError("金额必须为有限数字")
        return value


def create_command_schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS operation_previews (
      id TEXT PRIMARY KEY, command_json TEXT NOT NULL, state_hash TEXT NOT NULL,
      expires_at TEXT NOT NULL, warning INTEGER NOT NULL, result_json TEXT);
    CREATE TABLE IF NOT EXISTS task_runs (
      task_id TEXT NOT NULL, run_date TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL,
      PRIMARY KEY(task_id, run_date));
    CREATE TABLE IF NOT EXISTS agent_audit (
      id TEXT PRIMARY KEY, intent_json TEXT NOT NULL, tool TEXT NOT NULL,
      result_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS transaction_challenges (
      id TEXT PRIMARY KEY, preview_id TEXT NOT NULL, user_id TEXT NOT NULL,
      command_hash TEXT NOT NULL, token_hash TEXT NOT NULL,
      expires_at TEXT NOT NULL, used_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS ix_challenges_preview ON transaction_challenges(preview_id);
    CREATE TABLE IF NOT EXISTS task_authorizations (
      id TEXT PRIMARY KEY, task_id TEXT NOT NULL, account_id TEXT NOT NULL,
      task_type TEXT NOT NULL, amount_cap REAL NOT NULL,
      payee_id TEXT, payee_version INTEGER, product_id TEXT,
      frequency TEXT NOT NULL, execution_day INTEGER,
      status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked','expired')),
      authorized_at TEXT NOT NULL, revoked_at TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS ix_task_auth_task ON task_authorizations(task_id,status);
    """)
    from .conversations import create_context_schema
    create_context_schema(c)
    from .payees import create_payee_schema
    create_payee_schema(c)


def seed_account(c, reset=False, account_id=db.DEMO_ACCOUNT_ID):
    if not reset and db._account_state_from_db(c, account_id):
        return
    state = {"balance": 12000, "riskLevel": 2, "monthlySalary": 10000, "salaryDay": 15,
             "monthlyExpenses": 6000, "reserveAmount": 2000, "tasks": [], "holdings": []}
    for pid, principal, profit, purchased, days in [("D", 8000, 14.55, "2026-09-30", 30), ("K", 5000, 13.2, "2026-09-16", 45)]:
        if c.execute("SELECT 1 FROM wealth_products WHERE id=?", (pid,)).fetchone():
            state["holdings"].append({"productId": pid, "principal": principal, "profit": profit,
                                     "lots": [{"id": str(uuid.uuid4()), "principal": principal, "purchaseDate": purchased, "holdingDays": days}]})
    db._save_account_state(c, state, clear_transactions=reset, account_id=account_id)


def snapshot(c, account_id=db.DEMO_ACCOUNT_ID):
    state = db._account_state_from_db(c, account_id)
    if state is None:
        raise HTTPException(409, "账户未初始化")
    return state


def state_hash(state, products):
    return hashlib.sha256(json.dumps([state, products], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


CHALLENGE_AMOUNT_THRESHOLD = Decimal("2000")


def command_digest(command) -> str:
    """Canonical digest of a command; binds a re-auth challenge to the exact command."""
    data = command.model_dump(mode="json", exclude_none=True)
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def requires_challenge(command) -> bool:
    """High-risk operations need a second identity verification.

    Covers plan section 4.4: task authorization, one-off/stranger bank-account
    transfers, and any transfer/purchase over the configured amount threshold.
    """
    if command.kind == "task":
        return True
    if command.kind == "transfer" and command.manual_recipient is not None:
        return True
    if command.kind in ("transfer", "purchase") and command.amount is not None and command.amount > CHALLENGE_AMOUNT_THRESHOLD:
        return True
    return False


# Re-auth (challenge) failure throttling mirrors login, keyed by account user.
_CHALLENGE_FAILURES: dict[str, deque[float]] = defaultdict(deque)
_CHALLENGE_FAILURES_LOCK = Lock()
_CHALLENGE_MAX_FAILURES = 5
_CHALLENGE_WINDOW_SECONDS = 900


def _challenge_key(user_id: str, account_id: str) -> str:
    return f"{user_id}:{account_id}"


def enforce_challenge_quota(key: str) -> None:
    now = monotonic()
    with _CHALLENGE_FAILURES_LOCK:
        failures = _CHALLENGE_FAILURES[key]
        while failures and failures[0] <= now - _CHALLENGE_WINDOW_SECONDS:
            failures.popleft()
        if len(failures) >= _CHALLENGE_MAX_FAILURES:
            raise HTTPException(429, "身份验证尝试次数过多，请稍后再试")


def record_challenge_failure(key: str) -> None:
    with _CHALLENGE_FAILURES_LOCK:
        _CHALLENGE_FAILURES[key].append(monotonic())


def clear_challenge_failures(key: str) -> None:
    with _CHALLENGE_FAILURES_LOCK:
        _CHALLENGE_FAILURES.pop(key, None)


def validate(command, state, products, c=None, account_id=db.DEMO_ACCOUNT_ID):
    p = next((p for p in products if p["id"] == command.product_id), None)
    amount = command.amount
    kind = command.task_type if command.kind == "task" else command.kind
    if command.kind == "cancel_task":
        if not any(t["id"] == command.task_id and t["status"] != "已取消" for t in state["tasks"]):
            raise HTTPException(409, "任务不存在或已取消")
        return False
    if command.kind == "risk":
        if not command.answers or len(command.answers) != 10:
            raise HTTPException(422, "请提交完整的10道风险评估答案")
        return False
    if amount is None:
        raise HTTPException(422, "请填写金额")
    if command.kind == "task" and command.execution_day is None:
        raise HTTPException(422, "请填写执行日")
    if command.manual_recipient is not None and (command.kind!='transfer' or command.payee_id or command.payee_version is not None):
        raise HTTPException(422,'银行账号转账只能用于本次即时转账')
    if kind in ("transfer", "fixed_expense") and not (command.payee or "").strip() and not (kind=='transfer' and command.manual_recipient):
        raise HTTPException(422, "请填写收款方")
    if kind in ('transfer','fixed_expense') and command.manual_recipient is None:
        from .payees import bind_command
        bind_command(c,command,account_id=account_id)
    if kind in ("purchase", "investment"):
        if not p:
            raise HTTPException(422, "请选择产品")
        if p["risk"] > state["riskLevel"]:
            raise HTTPException(409, "产品风险超出账户风险承受能力")
        if not p["open"] or amount < money(p["min"]):
            raise HTTPException(409, "产品未开放或低于起购金额")
    if kind == "redemption":
        if command.product_id is None:
            raise HTTPException(422, "请选择赎回产品")
        candidates = [h for h in state["holdings"] if command.product_id == "all" or h["productId"] == command.product_id]
        available = sum(money(h["principal"]) for h in candidates if next((x["redeemable"] for x in products if x["id"] == h["productId"]), False))
        if available < amount:
            raise HTTPException(409, "可赎回持仓不足或产品暂不可赎回")
    projection = forecast(state, products)
    if command.kind == "task" and kind == "investment":
        draft = task_from(command, products)
        planned = {**state, "tasks": state["tasks"]+[draft]}
        if forecast(planned, products)["minimum"] < state["reserveAmount"]:
            raise HTTPException(409, "定时买入后预测余额低于警戒线")
    if command.kind in ("purchase", "transfer"):
        if amount > money(state["balance"]):
            raise HTTPException(409, "可用余额不足")
        return money(projection["minimum"])-amount < money(state["reserveAmount"])
    return False


def task_from(command, products):
    p = next((x for x in products if x["id"] == command.product_id), None)
    return {"id": str(uuid.uuid4()), "type": command.task_type, "name": command.name,
            "amount": float(command.amount), "frequency": command.frequency,
            "authorizedAt": today().isoformat(), "executionDay": command.execution_day,
            "risk": p["risk"] if p else 1, "status": "运行中", "productId": command.product_id,
            "payee": command.payee, 'payeeId':command.payee_id, 'payeeVersion':command.payee_version, "authorized": True}


def record_task_authorization(c, task, account_id):
    c.execute("""INSERT INTO task_authorizations(id,task_id,account_id,task_type,amount_cap,payee_id,payee_version,product_id,frequency,execution_day,status,authorized_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,'active',?)""",
              (str(uuid.uuid4()), task["id"], account_id, task["type"], float(task["amount"]),
               task.get("payeeId"), task.get("payeeVersion"), task.get("productId"),
               task["frequency"], task.get("executionDay"), task["authorizedAt"]))


def revoke_task_authorization(c, task_id):
    c.execute("UPDATE task_authorizations SET status='revoked',revoked_at=CURRENT_TIMESTAMP "
              "WHERE task_id=? AND status='active'", (task_id,))


def authorization_active(c, task_id):
    """Legacy tasks without an authorization row keep the existing flags; tasks
    that have rows must carry an active one."""
    if c.execute("SELECT 1 FROM task_authorizations WHERE task_id=? LIMIT 1", (task_id,)).fetchone() is None:
        return True
    return c.execute("SELECT 1 FROM task_authorizations WHERE task_id=? AND status='active'",
                     (task_id,)).fetchone() is not None


def preview(c, command, account_id=db.DEMO_ACCOUNT_ID):
    from .payees import bind_command,get_payee
    c.execute("DELETE FROM operation_previews WHERE expires_at<? AND result_json IS NULL",(datetime.now(timezone.utc).isoformat(),))
    if command.manual_recipient is None:
        command=bind_command(c,command,account_id=account_id)
    state, products = snapshot(c, account_id), db.products_from_db(c)
    warning = validate(command, state, products,c)
    pid = str(uuid.uuid4())
    expiry = (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()
    # Never persist the full one-time bank account number in command_json: keep the
    # full value in a dedicated column only until confirmation, mask it everywhere else.
    manual_account = None
    stored_command = command
    if command.manual_recipient:
        manual_account = command.manual_recipient.account_number
        redacted = command.manual_recipient.model_copy(update={"account_number": "**** " + manual_account[-4:]})
        stored_command = command.model_copy(update={"manual_recipient": redacted})
    c.execute("INSERT INTO operation_previews(id,command_json,state_hash,expires_at,warning,result_json,account_id,command_hash,manual_recipient_account) VALUES (?,?,?,?,?,NULL,?,?,?)",
              (pid, stored_command.model_dump_json(), state_hash(state, products), expiry, int(warning), account_id, command_digest(command), manual_account))
    public_command=command.model_dump(mode='json')
    if command.manual_recipient:
        public_command['manual_recipient']['account_number']='**** '+command.manual_recipient.account_number[-4:]
    return {"preview_id": pid, "command": public_command, "expires_at": expiry,
            'recipient':one_time_summary(command.manual_recipient) if command.manual_recipient else get_payee(c,command.payee_id,account_id=account_id) if command.payee_id else None,
            "requires_buffer_confirmation": warning,
            "requires_challenge": requires_challenge(command),
            "warning": "预计最低余额将低于警戒线，请再次确认" if warning else None}


def apply(c, command, state, products, event_id=None, conversation_id='default', account_id=db.DEMO_ACCOUNT_ID):
    from .payees import bind_command,record_transfer
    if command.manual_recipient is None:
        command=bind_command(c,command,account_id=account_id)
    kind = command.kind
    amount = money(command.amount or 0)
    credited = amount
    if kind == "task":
        task = task_from(command, products)
        state["tasks"].append(task)
        record_task_authorization(c, task, account_id)
    elif kind == "cancel_task":
        next(t for t in state["tasks"] if t["id"] == command.task_id)["status"] = "已取消"
        revoke_task_authorization(c, command.task_id)
    elif kind == "risk":
        state["riskLevel"] = int((Decimal(sum(command.answers))/10).quantize(Decimal("1"), rounding="ROUND_HALF_UP"))
    elif kind == "purchase":
        state["balance"] = float(money(state["balance"])-amount)
        h = next((h for h in state["holdings"] if h["productId"] == command.product_id), None)
        if not h:
            h = {"productId": command.product_id, "principal": 0, "profit": 0, "lots": []}
            state["holdings"].append(h)
        h["principal"] = float(money(h["principal"])+amount)
        h["lots"].append({"id": str(uuid.uuid4()), "principal": float(amount), "purchaseDate": today().isoformat()})
    elif kind == "redemption":
        remaining, profit = amount, Decimal(0)
        for h in state["holdings"]:
            if command.product_id not in ("all", h["productId"]) or not next(x["redeemable"] for x in products if x["id"] == h["productId"]):
                continue
            take = min(money(h["principal"]), remaining)
            if not take:
                continue
            realized = money(money(h["profit"])*take/money(h["principal"]))
            profit += realized
            h["profit"] = float(money(h["profit"])-realized)
            h["principal"] = float(money(h["principal"])-take)
            left = take
            for lot in h["lots"]:
                reduction = min(money(lot["principal"]), left)
                lot["principal"] = float(money(lot["principal"])-reduction)
                left -= reduction
            remaining -= take
        credited += profit
        state["balance"] = float(money(state["balance"])+credited)
    elif kind == "transfer":
        state["balance"] = float(money(state["balance"])-amount)
    label = {"purchase": "申购", "redemption": "赎回", "transfer": "转账", "task": "任务授权", "cancel_task": "取消任务", "risk": "风险评估"}[kind]
    text = f"{label}已完成" + (f"，¥{credited:.2f}" if amount and kind != "task" else "")
    events = []
    if kind in ("purchase", "redemption", "transfer"):
        events = [{"id": event_id or str(uuid.uuid4()), "transactionType": kind,
                   "direction": "in" if kind == "redemption" else "out", "amount": float(credited),
                   "balanceAfter": state["balance"], "productId": command.product_id if command.product_id != "all" else None,
                   "description": text}]
    db._save_account_state(c, state, events, account_id=account_id)
    if kind=='transfer':
        record_transfer(c,command,events[0],account_id=account_id)
    audit_command=command.model_dump(mode='json')
    if command.manual_recipient:
        audit_command['manual_recipient']['account_number']='**** '+command.manual_recipient.account_number[-4:]
    c.execute("INSERT INTO agent_audit(id,intent_json,tool,result_json,conversation_id) VALUES (?,?,?,?,?)",
              (str(uuid.uuid4()), json.dumps(audit_command,ensure_ascii=False), "command:"+kind, json.dumps({"text": text, "balance": state["balance"]}, ensure_ascii=False),conversation_id))
    return {"text": text, "state": snapshot(c, account_id)}


def _command_from_row(row):
    """Reconstruct a command from a stored preview row, restoring the full one-time account number."""
    data = json.loads(row["command_json"])
    if row["manual_recipient_account"] and isinstance(data.get("manual_recipient"), dict):
        data["manual_recipient"]["account_number"] = row["manual_recipient_account"]
    return Command.model_validate(data)


def issue_challenge(c, preview_id, user_id, password, account_id=db.DEMO_ACCOUNT_ID):
    """Verify the account user's password and mint a single-use challenge token."""
    row = c.execute("SELECT * FROM operation_previews WHERE id=?", (preview_id,)).fetchone()
    if not row or row["result_json"] or row["owner_user_id"] != user_id:
        raise HTTPException(404, "操作预览不存在")
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        raise HTTPException(409, "操作预览已过期，请重新预览")
    if row["account_id"] != account_id:
        raise HTTPException(409, "操作预览与当前账户不一致")
    command = _command_from_row(row)
    if not requires_challenge(command):
        raise HTTPException(409, "该操作无需二次验证")
    key = _challenge_key(user_id, account_id)
    enforce_challenge_quota(key)
    from ..security.auth import verify_password
    user = c.execute("SELECT password_hash FROM local_users WHERE id=?", (user_id,)).fetchone()
    if user is None or not verify_password(password or "", user["password_hash"]):
        record_challenge_failure(key)
        # 用 403 而非 401：401 在前端被当作“登录已过期”触发整页刷新，密码错误应就地提示。
        raise HTTPException(403, "密码错误")
    clear_challenge_failures(key)
    token = secrets.token_urlsafe(32)
    c.execute("INSERT INTO transaction_challenges(id,preview_id,user_id,command_hash,token_hash,expires_at) VALUES (?,?,?,?,?,?)",
              (str(uuid.uuid4()), preview_id, user_id, row["command_hash"],
               hashlib.sha256(token.encode("utf-8")).hexdigest(),
               (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()))
    return {"challenge_token": token}


def consume_challenge(c, preview_id, user_id, command_hash, token):
    if not token:
        raise HTTPException(403, "需要先完成身份验证")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = c.execute("SELECT * FROM transaction_challenges WHERE preview_id=? AND user_id=? AND command_hash=? AND token_hash=? AND used_at IS NULL",
                    (preview_id, user_id, command_hash, token_hash)).fetchone()
    if row is None:
        raise HTTPException(403, "身份验证无效或已使用")
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        raise HTTPException(403, "身份验证已过期，请重新验证")
    c.execute("UPDATE transaction_challenges SET used_at=? WHERE id=?",
              (datetime.now(timezone.utc).isoformat(), row["id"]))


def confirm(c, preview_id, acknowledged=False, account_id=db.DEMO_ACCOUNT_ID, challenge_token=None):
    row = c.execute("SELECT * FROM operation_previews WHERE id=?", (preview_id,)).fetchone()
    if not row:
        raise HTTPException(404, "操作预览不存在")
    if row["result_json"]:
        return json.loads(row["result_json"])
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        raise HTTPException(409, "操作预览已过期，请重新预览")
    state, products = snapshot(c, account_id), db.products_from_db(c)
    if row["state_hash"] != state_hash(state, products):
        raise HTTPException(409, "账户或产品状态已变化，请重新预览")
    command = _command_from_row(row)
    warning = validate(command, state, products,c,account_id=account_id)
    if warning and not acknowledged:
        raise HTTPException(409, "请再次确认低于现金警戒线")
    if requires_challenge(command):
        consume_challenge(c, preview_id, row["owner_user_id"], row["command_hash"], challenge_token)
    result = apply(c, command, state, products, preview_id, row['conversation_id'], account_id=account_id)
    if command.manual_recipient:
        public_command=command.model_dump(mode='json')
        public_command['manual_recipient']['account_number']='**** '+command.manual_recipient.account_number[-4:]
        c.execute('UPDATE operation_previews SET command_json=? WHERE id=?',(json.dumps(public_command,ensure_ascii=False),preview_id))
    # Clear the transient full account number once the transfer is applied.
    c.execute('UPDATE operation_previews SET manual_recipient_account=NULL WHERE id=?', (preview_id,))
    c.execute("UPDATE operation_previews SET result_json=? WHERE id=?", (json.dumps(result, ensure_ascii=False), preview_id))
    return result
