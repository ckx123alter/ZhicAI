"""Two-role, local-only authentication. No role or account comes from model output."""
import hashlib
import hmac
import os
import secrets
from collections import defaultdict, deque
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..repositories import legacy as db

COOKIE = "zhicai_session"
ACCOUNT_ID = db.DEMO_ACCOUNT_ID
SESSION_HOURS = 2
router = APIRouter(prefix="/api/auth")

# Login failure throttling: keyed by (username, client address), in-memory only.
_LOGIN_FAILURES: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_LOGIN_FAILURES_LOCK = Lock()
_MAX_FAILURES = 5
_FAILURE_WINDOW = 900


def _login_key(body: "LoginInput", request: Request) -> tuple[str, str]:
    address = request.client.host if request.client else "unknown"
    return (body.username.strip().lower(), address)


def enforce_login_quota(key: tuple[str, str]) -> None:
    now = monotonic()
    with _LOGIN_FAILURES_LOCK:
        failures = _LOGIN_FAILURES[key]
        while failures and failures[0] <= now - _FAILURE_WINDOW:
            failures.popleft()
        if len(failures) >= _MAX_FAILURES:
            raise HTTPException(429, "登录失败次数过多，请稍后再试")


def record_login_failure(key: tuple[str, str]) -> None:
    with _LOGIN_FAILURES_LOCK:
        _LOGIN_FAILURES[key].append(monotonic())


def clear_login_failures(key: tuple[str, str]) -> None:
    with _LOGIN_FAILURES_LOCK:
        _LOGIN_FAILURES.pop(key, None)


def create_auth_schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS local_users (
      id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
      role TEXT NOT NULL CHECK(role IN ('account_user','admin')),
      account_id TEXT, password_hash TEXT NOT NULL,
      active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE UNIQUE INDEX IF NOT EXISTS ix_local_users_active_role
      ON local_users(role) WHERE active=1;
    CREATE TABLE IF NOT EXISTS account_access (
      user_id TEXT NOT NULL REFERENCES local_users(id),
      account_id TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
      PRIMARY KEY(user_id,account_id));
    CREATE TABLE IF NOT EXISTS auth_sessions (
      token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES local_users(id),
      csrf_token TEXT NOT NULL, expires_at TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS ix_auth_sessions_user ON auth_sessions(user_id);
    INSERT OR IGNORE INTO account_access(user_id,account_id)
      SELECT id,account_id FROM local_users WHERE role='account_user' AND account_id IS NOT NULL;
    CREATE TABLE IF NOT EXISTS security_events (
      id TEXT PRIMARY KEY, event_type TEXT NOT NULL, actor_role TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS admin_action_audit (
      id TEXT PRIMARY KEY, user_id TEXT NOT NULL, action TEXT NOT NULL,
      target TEXT NOT NULL, detail TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    """)


def record_security_event(event_type: str, actor_role: str | None = None) -> None:
    with closing(db.connect_db()) as c, c:
        c.execute('INSERT INTO security_events(id,event_type,actor_role) VALUES (?,?,?)',
                  (secrets.token_hex(16), event_type, actor_role))


def password_hash(password: str) -> str:
    if len(password) < 8:
        raise ValueError("密码至少需要8个字符")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ":" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=16384, r=8, p=1)
        return hmac.compare_digest(actual, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


def provision_user(c, username: str, password: str, role: str):
    if role not in ("account_user", "admin"):
        raise ValueError("未知身份")
    username = username.strip()
    if not username or len(username) > 40:
        raise ValueError("用户名长度不正确")
    user_id = secrets.token_hex(16)
    c.execute(
        "INSERT INTO local_users(id,username,role,account_id,password_hash) VALUES (?,?,?,?,?)",
        (user_id, username, role, ACCOUNT_ID if role == "account_user" else None, password_hash(password)),
    )
    if role == 'account_user':
        c.execute('INSERT INTO account_access(user_id,account_id) VALUES (?,?)', (user_id, ACCOUNT_ID))
    return user_id


@dataclass(frozen=True)
class Actor:
    user_id: str
    username: str
    role: str
    account_id: str | None
    csrf_token: str
    token_hash: str


def actor_from_request(request: Request) -> Actor:
    token = request.cookies.get(COOKIE, "")
    if not token:
        raise HTTPException(401, "请先登录知财")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with closing(db.connect_db()) as c:
        row = c.execute("""
          SELECT s.token_hash,s.csrf_token,s.expires_at,u.id,u.username,u.role,u.account_id
          FROM auth_sessions s JOIN local_users u ON u.id=s.user_id
          WHERE s.token_hash=? AND u.active=1
        """, (token_hash,)).fetchone()
    if row is None or datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(401, "登录已过期，请重新登录")
    return Actor(row["id"], row["username"], row["role"], row["account_id"],
                 row["csrf_token"], row["token_hash"])


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
def login(body: LoginInput, response: Response, request: Request):
    key = _login_key(body, request)
    enforce_login_quota(key)
    with closing(db.connect_db()) as c, c:
        row = c.execute("SELECT * FROM local_users WHERE username=? AND active=1", (body.username.strip(),)).fetchone()
        # Equal-cost verification for unknown usernames.
        if row is None:
            hashlib.scrypt(body.password.encode("utf-8"), salt=b"zhicai-demo-salt", n=16384, r=8, p=1)
        if row is None or not verify_password(body.password, row["password_hash"]):
            record_login_failure(key)
            c.execute('INSERT INTO security_events(id,event_type) VALUES (?,?)', (secrets.token_hex(16), 'login_failed'))
            c.commit()
            raise HTTPException(401, "用户名或密码不正确")
        clear_login_failures(key)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expiry = (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).isoformat()
        c.execute("INSERT INTO auth_sessions(token_hash,user_id,csrf_token,expires_at) VALUES (?,?,?,?)",
                  (token_hash, row["id"], csrf, expiry))
        if row["role"] == "account_user":
            c.execute("UPDATE conversations SET owner_user_id=? WHERE owner_user_id IS NULL", (row["id"],))
    response.set_cookie(COOKIE, token, httponly=True, secure=os.getenv("ZHICAI_COOKIE_SECURE") == "1",
                        samesite="lax", max_age=SESSION_HOURS * 3600, path="/")
    return {"username": row["username"], "role": row["role"], "csrf_token": csrf}


@router.get("/me")
def me(request: Request):
    actor = request.state.actor
    return {"username": actor.username, "role": actor.role, "csrf_token": actor.csrf_token}


@router.post("/logout")
def logout(request: Request, response: Response):
    with closing(db.connect_db()) as c, c:
        c.execute("DELETE FROM auth_sessions WHERE token_hash=?", (request.state.actor.token_hash,))
    response.delete_cookie(COOKIE, path="/")
    return {"status": "logged_out"}
