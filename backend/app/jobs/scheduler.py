"""Runs with backend lifespan; missed dates are skipped and recorded, never backcharged."""
import asyncio
import logging
from contextlib import closing
from datetime import date, timedelta
from fastapi import HTTPException
from pydantic import ValidationError
from ..repositories import legacy as db
from ..services.commands import Command, apply, snapshot, validate, authorization_active
from ..services.finance import today, occurs, forecast

log = logging.getLogger(__name__)


def run_due(on=None):
    on = on or today()
    with closing(db.connect_db()) as c:
        ids = [r[0] for r in c.execute("SELECT id FROM scheduled_tasks WHERE status='运行中' AND authorized=1")]
    for tid in ids:
        with closing(db.connect_db()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            state, products = snapshot(c), db.products_from_db(c)
            task = next((t for t in state["tasks"] if t["id"] == tid), None)
            if not task or task["status"] != "运行中" or not task.get("authorized"):
                continue
            if not authorization_active(c, tid):
                c.execute("INSERT INTO task_runs VALUES (?,?,?,?)", (tid, on.isoformat(), "failed", "授权已撤销或失效，未执行"))
                continue
            # Persist each missed period once, including process downtime.
            cursor = date.fromisoformat(task.get("lastRun") or task["authorizedAt"])
            latest = c.execute("SELECT MAX(run_date) FROM task_runs WHERE task_id=?", (tid,)).fetchone()[0]
            if latest:
                cursor = max(cursor, date.fromisoformat(latest)+timedelta(days=1))
            while cursor < on:
                if occurs(task, cursor) and task.get("lastRun") != cursor.isoformat():
                    c.execute("INSERT OR IGNORE INTO task_runs VALUES (?,?,?,?)", (tid, cursor.isoformat(), "skipped", "错过执行日，未补扣"))
                cursor += timedelta(days=1)
            if not occurs(task, on) or c.execute("SELECT 1 FROM task_runs WHERE task_id=? AND run_date=?", (tid, on.isoformat())).fetchone():
                continue
            try:
                command = Command(kind={"fixed_expense": "transfer", "investment": "purchase", "redemption": "redemption"}[task["type"]],
                                  amount=task["amount"], product_id=task.get("productId"), payee=task.get("payee"),payee_id=task.get('payeeId'),payee_version=task.get('payeeVersion'))
                from ..services.payees import bind_command
                command=bind_command(c,command,require_bound=task['type']=='fixed_expense')
                # Exclude this occurrence; validate the purchase delta exactly once.
                task["lastRun"] = on.isoformat()
                warning = validate(command, state, products,c)
                if task["type"] == "investment" and warning:
                    raise HTTPException(409, "现金缓冲不足，本期跳过")
                result = apply(c, command, state, products, f"task:{tid}:{on}")
                status, detail = "executed", result["text"]
            except (HTTPException, ValidationError) as exc:
                status, detail = "failed", str(exc.detail) if isinstance(exc, HTTPException) else "任务参数不完整或金额无效"
                c.execute("UPDATE scheduled_tasks SET last_failure=? WHERE id=?", (on.isoformat(), tid))
            c.execute("INSERT INTO task_runs VALUES (?,?,?,?)", (tid, on.isoformat(), status, detail))


async def scheduler():
    while True:
        try:
            await asyncio.to_thread(run_due)
        except Exception:
            log.error("定时任务检查失败；下轮将重试", exc_info=False)
        await asyncio.sleep(30)
