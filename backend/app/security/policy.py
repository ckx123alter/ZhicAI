"""Role authorization policies.

Identity resolution lives in auth.py; these dependents enforce that a resolved
identity may reach a given route. The demo has exactly two roles and one account,
so account_user is additionally pinned to the demo account id bound at login.

Imports are lazy to avoid a module-level import cycle: legacy -> policy -> auth
-> legacy. Nothing app-local is imported at module import time here.
"""
from fastapi import HTTPException, Request


def require_account_user(request: Request):
    from ..repositories import legacy as db
    actor = request.state.actor
    if actor.role != "account_user" or actor.account_id != db.DEMO_ACCOUNT_ID:
        from .auth import record_security_event
        record_security_event('account_route_denied', actor.role)
        raise HTTPException(403, "当前身份无权执行此操作")
    from contextlib import closing
    with closing(db.connect_db()) as c:
        granted = c.execute('SELECT 1 FROM account_access WHERE user_id=? AND account_id=? AND active=1',
                            (actor.user_id, actor.account_id)).fetchone()
    if not granted:
        from .auth import record_security_event
        record_security_event('account_access_revoked', actor.role)
        raise HTTPException(403, '账户授权已失效')
    return actor


def require_admin(request: Request):
    actor = request.state.actor
    if actor.role != "admin":
        from .auth import record_security_event
        record_security_event('admin_route_denied', actor.role)
        raise HTTPException(403, "当前身份无权执行此操作")
    return actor
