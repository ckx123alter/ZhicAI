"""Administrative controls for the local demo; no account transactions."""
import json
import secrets
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from ..repositories import legacy as db
from ..security.policy import require_admin

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


class AvailabilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    open: bool


@router.get("/overview")
def overview():
    with closing(db.connect_db()) as c:
        products = db.products_from_db(c)
        actions = [dict(row) for row in c.execute(
            "SELECT action,target,detail,created_at FROM admin_action_audit ORDER BY rowid DESC LIMIT 20")]
        return {
            "products": [{"id": p["id"], "name": p["name"], "risk": p["risk"],
                          "open": p["open"]} for p in products],
            "counts": {"products": len(products), "open_products": sum(bool(p["open"]) for p in products),
                       "tasks": c.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0]},
            "actions": actions,
            "system": {"auth_sessions": c.execute("SELECT COUNT(*) FROM auth_sessions WHERE datetime(expires_at)>CURRENT_TIMESTAMP").fetchone()[0],
                       "pending_previews": c.execute("SELECT COUNT(*) FROM operation_previews WHERE result_json IS NULL").fetchone()[0],
                       "recent_task_failures": c.execute("SELECT COUNT(*) FROM task_runs WHERE status='failed'").fetchone()[0]},
            "security_audit": [{"kind": row["tool"], "at": row["created_at"]} for row in c.execute(
                "SELECT event_type AS tool,created_at FROM security_events ORDER BY rowid DESC LIMIT 20")],
        }


@router.put("/products/{product_id}/availability")
def set_availability(product_id: str, body: AvailabilityInput, request: Request):
    with closing(db.connect_db()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        changed = c.execute("UPDATE wealth_products SET is_open=? WHERE id=?",
                            (int(body.open), product_id)).rowcount
        if not changed:
            raise HTTPException(404, "理财产品不存在")
        c.execute("INSERT INTO admin_action_audit(id,user_id,action,target,detail) VALUES (?,?,?,?,?)",
                  (secrets.token_hex(16), request.state.actor.user_id, "product_availability",
                   product_id, json.dumps({"open": body.open}, ensure_ascii=False)))
    return {"id": product_id, "open": body.open}


@router.post("/reset")
def reset_demo(request: Request):
    """Reset demo data. Admin-only, audited; never reachable by an account user."""
    with closing(db.connect_db()) as c, c:
        from ..services.commands import seed_account
        from ..services.conversations import clear_context
        c.execute("DELETE FROM operation_previews")
        c.execute("DELETE FROM task_runs")
        c.execute("DELETE FROM agent_audit")
        c.execute("DELETE FROM conversation_messages")
        clear_context(c)
        seed_account(c, reset=True)
        c.execute("INSERT INTO admin_action_audit(id,user_id,action,target,detail) VALUES (?,?,?,?,?)",
                  (secrets.token_hex(16), request.state.actor.user_id, "reset_demo", "demo",
                   json.dumps({"scope": "account_state_previews_tasks_audit_messages"}, ensure_ascii=False)))
    return {"status": "reset"}
