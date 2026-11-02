"""FastAPI entry point."""
import hmac

if __package__:
    from .app.repositories.legacy import app
    from .app.api.routes import router
    from .app.security.auth import router as auth_router, actor_from_request, record_security_event
    from .app.api.admin import router as admin_router
else:
    from app.repositories.legacy import app
    from app.api.routes import router
    from app.security.auth import router as auth_router, actor_from_request, record_security_event
    from app.api.admin import router as admin_router

from fastapi import HTTPException
from fastapi.responses import JSONResponse


@app.middleware("http")
async def require_local_identity(request, call_next):
    """Resolve identity and enforce CSRF; role authorization is per-route (policy.py)."""
    path = request.url.path
    if path.startswith("/api/") and path not in ("/api/health", "/api/auth/login"):
        try:
            actor = actor_from_request(request)
        except HTTPException as exc:
            record_security_event('session_rejected')
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            submitted = request.headers.get("X-CSRF-Token", "")
            if not submitted or not hmac.compare_digest(submitted, actor.csrf_token):
                record_security_event('csrf_rejected', actor.role)
                return JSONResponse({"detail": "请求验证失败，请刷新页面"}, status_code=403)
        request.state.actor = actor
    return await call_next(request)

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(router)

__all__ = ["app"]
