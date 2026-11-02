"""Shared pytest fixtures for the backend test suite.

Keeping these in conftest.py means individual test modules do not import from
one another. pytest auto-discovers these fixtures for every test_*.py in this
directory.

- ``isolated_narrative`` (autouse): pins a deterministic prose writer so tests
  never call a real model and always get a stable, evidence-grounded answer.
- ``client``: provisions an isolated SQLite database (tmp_path) with a logged-in
  account user and a logged-in admin, plus a CSRF token.
"""
import json
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.app.repositories import legacy as db
from backend.app.services.commands import create_command_schema, seed_account
from backend.app.config import load_settings


@pytest.fixture(autouse=True)
def isolated_narrative(monkeypatch):
    from backend.app.agent import narrative
    from backend.app.agent.schemas import Narrative
    from backend.app import llm_client
    from backend.app.api import routes
    with routes._chat_hits_lock:
        routes._chat_hits.clear()

    class FakeWriter:
        def invoke(self, messages):
            context = json.loads(messages[-1][1])['context']
            return Narrative(text='根据当前账户数据，' + context['required_text'],
                             bubble_text=context['required_bubble'])

    monkeypatch.setattr(narrative, 'narrative_client', lambda: FakeWriter())
    llm_client.model_client.cache_clear()
    load_settings.cache_clear()
    with routes._chat_hits_lock:
        routes._chat_hits.clear()
    yield
    for fn in (llm_client.model_client, llm_client.intent_client, llm_client.narrative_client):
        if hasattr(fn, 'cache_clear'):
            fn.cache_clear()
    load_settings.cache_clear()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "test.sqlite3")
    db.initialize_database()
    with closing(db.connect_db()) as c, c:
        create_command_schema(c)
        seed_account(c)
        from backend.tests.payee_seed import seed_test_payees
        seed_test_payees(c)
        from backend.app.security.auth import create_auth_schema, provision_user
        create_auth_schema(c)
        provision_user(c, 'test-account', 'test-password-123', 'account_user')
        provision_user(c, 'test-admin', 'test-admin-password-123', 'admin')
    client = TestClient(app)
    login = client.post('/api/auth/login', json={'username': 'test-account', 'password': 'test-password-123'})
    assert login.status_code == 200, login.text
    client.headers.update({'X-CSRF-Token': login.json()['csrf_token']})
    return client
