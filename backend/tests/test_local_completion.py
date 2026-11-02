"""Acceptance checks for local evidence trace and redacted admin overview."""
from backend.app.agent.evidence import collect_facts


def test_evidence_fact_traces_bill_source_and_snapshot():
    snapshot = collect_facts({"tool": "bills", "result": {
        "month": "2026-10", "sourceIds": ["bill-1", "bill-2"],
        "total": 321.50, "account_number": "1234567890123456"}}, "demo-account")
    total = next(f for f in snapshot["facts"] if f["fact_type"] == "result.total")
    assert total["value"] == 321.50
    assert total["source_record_ids"] == ["bill-1", "bill-2"]
    assert total["account_id"] == "demo-account"
    assert total["snapshot_id"] == snapshot["snapshot_id"]
    assert total["fact_id"]
    assert all("account_number" not in fact["fact_type"] for fact in snapshot["facts"])


def test_admin_overview_contains_redacted_monitoring(client):
    from backend.app.security.auth import COOKIE
    from backend.app.repositories import legacy as db
    from contextlib import closing
    with closing(db.connect_db()) as c:
        row = c.execute("SELECT password_hash FROM local_users WHERE username='test-admin'").fetchone()
    assert row is not None
    login = client.post('/api/auth/login', json={'username': 'test-admin', 'password': 'test-admin-password-123'})
    assert login.status_code == 200
    client.headers.update({'X-CSRF-Token': login.json()['csrf_token']})
    denied = client.get('/api/finance/overview')
    assert denied.status_code == 403
    response = client.get('/api/admin/overview')
    assert response.status_code == 200
    body = response.json()
    assert set(body['system']) == {'auth_sessions', 'pending_previews', 'recent_task_failures'}
    assert all(set(item) == {'kind', 'at'} for item in body['security_audit'])
    assert any(item['kind'] == 'account_route_denied' for item in body['security_audit'])
    assert 'password_hash' not in response.text and 'command_json' not in response.text


def test_revoked_account_access_blocks_existing_session(client):
    from backend.app.repositories import legacy as db
    from contextlib import closing
    assert client.get('/api/finance/overview').status_code == 200
    with closing(db.connect_db()) as c, c:
        c.execute("UPDATE account_access SET active=0 WHERE user_id=(SELECT id FROM local_users WHERE username='test-account')")
    assert client.get('/api/finance/overview').status_code == 403
    assert client.post('/api/operations/preview', json={'kind': 'transfer', 'amount': 1}).status_code == 403


def test_claims_reference_current_bill_fact_ids():
    from backend.app.agent.evidence import bind_claims
    snapshot = collect_facts({'tool': 'bills', 'result': {
        'sourceIds': ['bill-101'], 'month': '2026-10', 'total': 321.5}}, 'demo-account')
    bindings = bind_claims('2026-10消费合计 ¥321.50。', snapshot)
    assert len(bindings) == 1 and bindings[0]['verified']
    ids = set(bindings[0]['fact_ids'])
    assert any(f['fact_id'] in ids and f['source_record_ids'] == ['bill-101']
               for f in snapshot['facts'])
    assert not bind_claims('2026-10消费合计 ¥999.00。', snapshot)[0]['verified']


def test_read_plan_executes_one_registered_tool_and_resumes_same_id():
    from backend.app.agent.planner import begin_plan, execute_read_steps, finish_plan
    from backend.app.agent.schemas import Intent, AgentResponse
    intent = Intent(intent='bills', month='2026-10')
    first = begin_plan(intent, 'conversation-1', 'demo-account', 1)
    calls = []
    def read(c, month, category, focus, comparison_months, account_id):
        calls.append((month, account_id))
        return {'month': month, 'total': 12.5, 'sourceIds': ['bill-1']}
    result = execute_read_steps(first, intent, object(), {'bills': read}, 'demo-account')
    assert calls == [('2026-10', 'demo-account')]
    finished = finish_plan(first, intent, AgentResponse(text='2026-10消费合计 ¥12.50。',
                                                        evidence={'tool': 'bills', 'result': result}))
    assert finished.steps[0].status == 'completed'
    resume_state = {**finished.model_dump(), 'status': 'needs_details'}
    resumed = begin_plan(intent, 'conversation-1', 'demo-account', 2, resume_state)
    assert resumed.plan_id == first.plan_id and resumed.steps[0].status == 'pending'
    changed_account = begin_plan(intent, 'conversation-1', 'another-account', 2, resume_state)
    assert changed_account.plan_id != first.plan_id


def test_swapped_known_amounts_do_not_pass_relation_validation(monkeypatch):
    from backend.app.agent import narrative
    from backend.app.agent.schemas import Intent, AgentResponse, Narrative
    class Writer:
        def invoke(self, messages):
            return Narrative(text='当前可用余额 ¥1005.00，持仓市值 ¥12000.00。')
    monkeypatch.setattr(narrative, 'narrative_client', lambda: Writer())
    draft = AgentResponse(text='当前可用余额 ¥12000.00，持仓市值 ¥1005.00。',
                          evidence={'tool': 'account', 'result': {'balance': 12000.0,
                          'holdings': [{'principal': 1000.0, 'profit': 5.0}]}})
    reply = narrative.generate('我有多少钱？', [], Intent(intent='account'), draft)
    assert reply.text == '当前可用余额 ¥12000.00，持仓市值 ¥1005.00。'
    assert reply.evidence['reply_source'] == 'evidence_fallback'


def test_preview_and_confirm_advance_persisted_plan():
    import json
    import sqlite3
    from backend.app.agent.planner import Plan, advance_operation_plan, compile_plan
    from backend.app.agent.schemas import Intent, AgentResponse, Action
    plan = compile_plan(Intent(intent='transfer', amount=500),
                        AgentResponse(text='请确认', actions=[Action(label='转账', path='/transfer')]),
                        conversation_id='conversation-1', account_id='demo-account')
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE conversation_topics(id TEXT,conversation_id TEXT,plan_json TEXT)')
    c.execute('INSERT INTO conversation_topics VALUES (?,?,?)',
              ('topic-1', 'conversation-1', plan.model_dump_json()))
    advance_operation_plan(c, 'conversation-1', 'topic-1', 'preview')
    after_preview = Plan.model_validate_json(c.execute('SELECT plan_json FROM conversation_topics').fetchone()[0])
    assert [(s.name, s.status) for s in after_preview.steps[-2:]] == [
        ('create_preview', 'completed'), ('await_confirmation', 'pending')]
    advance_operation_plan(c, 'conversation-1', 'topic-1', 'confirmed')
    after_confirm = Plan.model_validate_json(c.execute('SELECT plan_json FROM conversation_topics').fetchone()[0])
    assert after_confirm.status == 'answered' and after_confirm.steps[-1].status == 'completed'
    c.close()


def test_compound_product_plan_runs_query_and_limit_steps(client, monkeypatch):
    from backend.app.agent import chat as agent
    from backend.app.agent.schemas import Intent
    monkeypatch.setattr(agent, 'interpret', lambda *args, **kwargs:
                        Intent(intent='products', risk=2, include_forecast=True))
    response = client.post('/api/chat', json={'message': 'R2 理财最多能投多少？'})
    assert response.status_code == 200, response.text
    plan = response.json()['evidence']['plan']
    assert plan['goals'] == ['products', 'forecast']
    assert [step['name'] for step in plan['steps']] == [
        'query_products', 'query_forecast', 'calculate_limit', 'compose_answer']
    assert all(step['status'] == 'completed' for step in plan['steps'])


def test_swapped_bill_month_totals_fall_back(monkeypatch):
    from backend.app.agent import narrative
    from backend.app.agent.schemas import Intent, AgentResponse, Narrative
    class Writer:
        def invoke(self, messages):
            return Narrative(text='2026-10消费合计 ¥80.00。2026-09消费合计 ¥120.00。')
    monkeypatch.setattr(narrative, 'narrative_client', lambda: Writer())
    original = '2026-10消费合计 ¥120.00。2026-09消费合计 ¥80.00。'
    result = {'month': '2026-10', 'total': 120, 'sourceIds': ['oct-1'],
              'comparison': {'baseline_month': '2026-10', 'months': [
                  {'month': '2026-10', 'available': True, 'total': 120},
                  {'month': '2026-09', 'available': True, 'total': 80}],
                  'differences': [{'compared_month': '2026-09', 'change': 40}]}}
    reply = narrative.generate('比较两个月', [], Intent(intent='bills', month='2026-10',
        comparison_months=['2026-09']), AgentResponse(text=original, evidence={'tool': 'bills', 'result': result}))
    assert reply.text == original and reply.evidence['reply_source'] == 'evidence_fallback'


def test_multi_month_bill_plan_records_comparison_step(client, monkeypatch):
    from backend.app.agent import chat as agent
    from backend.app.agent.schemas import Intent
    monkeypatch.setattr(agent, 'interpret', lambda *args, **kwargs: Intent(
        intent='bills', month='2026-10', comparison_months=['2026-09']))
    response = client.post('/api/chat', json={'message': '10月比9月多花在哪？'})
    assert response.status_code == 200, response.text
    plan = response.json()['evidence']['plan']
    assert [step['name'] for step in plan['steps']] == [
        'query_bills', 'compare_bills', 'compose_answer']
    assert all(step['status'] == 'completed' for step in plan['steps'])


def test_derived_holding_value_has_its_own_fact_id():
    from backend.app.agent.evidence import bind_claims
    snapshot = collect_facts({'tool': 'account', 'result': {'balance': 12000,
        'holdings': [{'id': 'lot-1', 'principal': 1000, 'profit': 5}]}}, 'demo-account')
    derived = next(f for f in snapshot['facts'] if f['fact_type'] == 'derived.holdings_market_value')
    assert derived['value'] == 1005 and derived['source_record_ids'] == ['lot-1']
    claim = bind_claims('持仓市值 ¥1005.00。', snapshot)[0]
    assert claim['verified'] and derived['fact_id'] in claim['fact_ids']
