"""Permission matrix and identity-boundary regression tests.

Covers plan section 10.1 item 1: unauthenticated 401, account_user blocked from
/admin, admin blocked from account operations, CSRF, login throttling, and
cross-user preview isolation. All use the isolated SQLite database via tmp_path.
"""
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.app.repositories import legacy as db
from backend.app.services.commands import create_command_schema, seed_account
from backend.app.security import auth as auth_mod
from backend.app.security.auth import create_auth_schema, provision_user


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "test.sqlite3")
    db.initialize_database()
    with closing(db.connect_db()) as c, c:
        create_command_schema(c)
        seed_account(c)
        from backend.tests.payee_seed import seed_test_payees
        seed_test_payees(c)
        create_auth_schema(c)
        provision_user(c, 'account-a', 'password-123', 'account_user')
        provision_user(c, 'admin', 'admin-password-123', 'admin')
    with auth_mod._LOGIN_FAILURES_LOCK:
        auth_mod._LOGIN_FAILURES.clear()


def _login(client, username, password):
    response = client.post('/api/auth/login', json={'username': username, 'password': password})
    assert response.status_code == 200, response.text
    client.headers.update({'X-CSRF-Token': response.json()['csrf_token']})
    return client


@pytest.fixture
def account(seeded):
    client = TestClient(app)
    return _login(client, 'account-a', 'password-123')


@pytest.fixture
def admin(seeded):
    client = TestClient(app)
    return _login(client, 'admin', 'admin-password-123')


def test_unauthenticated_account_api_returns_401(seeded):
    client = TestClient(app)
    for method, path, kwargs in [
        ('get', '/api/finance/overview', {}),
        ('get', '/api/account-state', {}),
        ('get', '/api/payees', {}),
        ('post', '/api/chat', {'json': {'message': '你好'}}),
        ('post', '/api/operations/preview', {'json': {'kind': 'purchase', 'amount': 100}}),
    ]:
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 401, (method, path, response.text)


def test_account_user_cannot_reach_admin_api(account):
    assert account.get('/api/admin/overview').status_code == 403
    assert account.post('/api/admin/reset').status_code == 403
    assert account.put('/api/admin/products/D/availability', json={'open': False}).status_code == 403


def test_admin_cannot_use_account_operations(admin):
    assert admin.get('/api/account-state').status_code == 403
    assert admin.get('/api/payees').status_code == 403
    assert admin.get('/api/transfers').status_code == 403
    assert admin.get('/api/finance/overview').status_code == 403
    assert admin.post('/api/chat', json={'message': '你好'}).status_code == 403
    assert admin.post('/api/operations/preview', json={'kind': 'purchase', 'amount': 100}).status_code == 403
    assert admin.post('/api/operations/any/confirm', json={'confirmed': True}).status_code == 403


def test_admin_overview_and_reset_allowed(admin, account):
    assert admin.get('/api/admin/overview').status_code == 200
    assert admin.post('/api/admin/reset').status_code == 200
    # account user still reads its own state after reset
    assert account.get('/api/account-state').json()['balance'] == 12000


def test_admin_can_change_product_availability(admin, account):
    changed = admin.put('/api/admin/products/D/availability', json={'open': False})
    assert changed.status_code == 200, changed.text
    assert changed.json() == {'id': 'D', 'open': False}
    overview = admin.get('/api/admin/overview').json()
    assert next(p for p in overview['products'] if p['id'] == 'D')['open'] is False
    assert overview['actions'][0]['action'] == 'product_availability'
    assert account.get('/api/products') .json()[0]['open'] is False


def test_write_requires_csrf_token(account):
    bare = TestClient(app)
    login = bare.post('/api/auth/login', json={'username': 'account-a', 'password': 'password-123'})
    assert login.status_code == 200
    # authenticated cookie, but no X-CSRF-Token on a write
    assert bare.post('/api/payees', json={'nickname': 'x', 'holder_name': '李四', 'bank_name': '演示银行',
                                          'account_number': '0000000000009999'}).status_code == 403


def test_login_failure_is_throttled(seeded):
    client = TestClient(app)
    for _ in range(5):
        response = client.post('/api/auth/login', json={'username': 'account-a', 'password': 'wrong-password'})
        assert response.status_code == 401
    throttled = client.post('/api/auth/login', json={'username': 'account-a', 'password': 'wrong-password'})
    assert throttled.status_code == 429


def test_confirm_unknown_preview_is_not_found(account):
    assert account.post('/api/operations/does-not-exist/confirm', json={'confirmed': True}).status_code == 404


def test_confirm_binds_to_preview_owner(account):
    preview = account.post('/api/operations/preview', json={'kind': 'purchase', 'product_id': 'D', 'amount': 100})
    assert preview.status_code == 200, preview.text
    preview_id = preview.json()['preview_id']
    # Tamper with the owner id; confirm must fail because the preview no longer
    # belongs to this session's user.
    with closing(db.connect_db()) as c, c:
        c.execute("UPDATE operation_previews SET owner_user_id='someone-else' WHERE id=?", (preview_id,))
    foreign = account.post(f'/api/operations/{preview_id}/confirm', json={'confirmed': True})
    assert foreign.status_code == 404, foreign.text


def _preview(account, command):
    response = account.post('/api/operations/preview', json=command)
    assert response.status_code == 200, response.text
    return response.json()


def test_high_risk_transfer_requires_challenge(account):
    preview = _preview(account, {'kind': 'transfer', 'amount': 5000, 'manual_recipient': {
        'holder_name': '演示收款人', 'bank_name': '演示银行', 'account_number': '0000000000002789'}})
    assert preview['requires_challenge']
    # no token -> blocked before any write
    blocked = account.post(f"/api/operations/{preview['preview_id']}/confirm", json={'confirmed': True})
    assert blocked.status_code == 403, blocked.text
    # wrong password -> rejected（403 而非 401，避免前端当作“登录已过期”整页刷新）
    wrong = account.post(f"/api/operations/{preview['preview_id']}/challenge", json={'password': 'wrong-password'})
    assert wrong.status_code == 403
    # correct password issues a single-use token
    ch = account.post(f"/api/operations/{preview['preview_id']}/challenge", json={'password': 'password-123'})
    assert ch.status_code == 200, ch.text
    token = ch.json()['challenge_token']
    assert account.post(f"/api/operations/{preview['preview_id']}/confirm",
                        json={'confirmed': True, 'challenge_token': token}).status_code == 200
    # token is single-use; the stored result short-circuits, so re-confirm is idempotent
    assert account.post(f"/api/operations/{preview['preview_id']}/confirm",
                        json={'confirmed': True}).status_code == 200


def test_low_risk_transfer_needs_no_challenge(account):
    payee = account.get('/api/payees').json()[0]
    preview = _preview(account, {'kind': 'transfer', 'payee_id': payee['id'],
                                 'payee_version': payee['version'], 'amount': 100})
    assert not preview['requires_challenge']
    assert account.post(f"/api/operations/{preview['preview_id']}/confirm",
                        json={'confirmed': True}).status_code == 200


def test_account_user_can_reset_demo(account):
    # Change state first, then reset back to the initial account state.
    payee = account.get('/api/payees').json()[0]
    preview = _preview(account, {'kind': 'transfer', 'payee_id': payee['id'],
                                 'payee_version': payee['version'], 'amount': 100})
    assert account.post(f"/api/operations/{preview['preview_id']}/confirm",
                        json={'confirmed': True}).status_code == 200
    assert account.get('/api/account-state').json()['balance'] == 11900
    assert account.post('/api/account-state/reset', json={'state': {}}).status_code == 200
    assert account.get('/api/account-state').json()['balance'] == 12000


def test_revoked_task_authorization_blocks_scheduler(account):
    from backend.app.jobs.scheduler import run_due
    from backend.app.services.finance import today
    day = today().day
    preview = _preview(account, {'kind': 'task', 'task_type': 'investment', 'product_id': 'D',
                                 'amount': 100, 'frequency': 'monthly', 'execution_day': day})
    assert preview['requires_challenge']
    ch = account.post(f"/api/operations/{preview['preview_id']}/challenge", json={'password': 'password-123'})
    assert ch.status_code == 200, ch.text
    assert account.post(f"/api/operations/{preview['preview_id']}/confirm",
                        json={'confirmed': True, 'challenge_token': ch.json()['challenge_token']}).status_code == 200
    with closing(db.connect_db()) as c, c:
        tid = c.execute("SELECT id FROM scheduled_tasks ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        c.execute("UPDATE task_authorizations SET status='revoked' WHERE task_id=?", (tid,))
    run_due(today())
    with closing(db.connect_db()) as c:
        run = c.execute("SELECT status, detail FROM task_runs WHERE task_id=?", (tid,)).fetchone()
    assert run is not None and run['status'] == 'failed' and '授权' in run['detail']
