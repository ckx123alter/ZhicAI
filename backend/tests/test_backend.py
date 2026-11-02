from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
from decimal import Decimal
import json
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.app.repositories import legacy as db
from backend.app.services.commands import create_command_schema, seed_account, Command, snapshot
from backend.app.services.finance import forecast, occurs, performance
from backend.app.jobs.scheduler import run_due
from backend.app.agent.schemas import Intent
from backend.app.agent import chat as agent
from backend.app.config import load_settings
from backend.tests.helpers import operation


def test_expired_chat_turn_does_not_save_late_model_reply(client, monkeypatch):
    with closing(db.connect_db()) as c:
        before = c.execute('SELECT COUNT(*) FROM conversation_messages').fetchone()[0]
    ticks = iter([0, 0, 91])
    monkeypatch.setattr(agent, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(agent, 'interpret', lambda *args, **kwargs: Intent(intent='products', risk=2))
    response = client.post('/api/chat', json={'message': '我要购买R2理财'})
    assert response.status_code == 504
    with closing(db.connect_db()) as c:
        assert c.execute('SELECT COUNT(*) FROM conversation_messages').fetchone()[0] == before


def test_r3_request_uses_eligible_r2_products_then_offers_reassessment(client, monkeypatch):
    class Model:
        def invoke(self, _):
            # Risk-only purchase language is a product search, not an order.
            return Intent(intent='purchase', risk=3)

    monkeypatch.setattr(agent, 'intent_client', lambda: Model())
    first = client.post('/api/chat', json={'message': '我要购买R3理财'})
    assert first.status_code == 200, first.text
    initial = first.json()
    assert initial['evidence']['reply_source'] == 'suitability_rule'
    assert 'R3 产品无法申购' in initial['text']
    assert 'R2 可申购产品' in initial['text']
    assert '进取精选' not in initial['text'] and '进取多元精选' not in initial['text']
    with closing(db.connect_db()) as c:
        eligible = {p['name'] for p in db.products_from_db(c) if p['risk'] == 2 and p['open']}
    assert all(line.split('，')[0] in eligible for line in initial['text'].splitlines()[3:])

    second = client.post('/api/chat', json={'message': '我就是想买R3理财'})
    assert second.status_code == 200, second.text
    followup = second.json()
    assert len(followup['actions']) == 1
    assert followup['actions'][0]['label'] == '重新评估'
    assert followup['actions'][0]['path'] == '/risk-assessment'
    assert '仍无法申购 R3' in followup['text']


def test_unspecified_product_risk_clarifies_without_querying_or_assuming(client,monkeypatch):
    class Model:
        def invoke(self,_):
            return Intent(intent='products',risk=1,include_forecast=True)
    def forbidden(*_):
        raise AssertionError('Product lookup must wait for user risk selection')
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    monkeypatch.setitem(agent.TOOLS,'products',forbidden)
    response=client.post('/api/chat',json={'message':'我想买理财，最多能投多少？'})
    assert response.status_code==200,response.text
    body=response.json()
    assert body['evidence']['missing_fields']==['产品风险等级']
    assert body['evidence']['reply_source']=='clarification_rule'
    assert body['actions']==[] and body['response_mode']=='direct'
    assert '哪个风险等级' in body['text'] and '¥' not in body['text']


@pytest.mark.parametrize('include_forecast', [False, True])
def test_generic_product_search_resets_old_risk_and_resumes_after_answer(client,monkeypatch,include_forecast):
    answers=iter([Intent(intent='products',risk=1),
                  Intent(intent='products',risk=1,include_forecast=include_forecast,context_mode='continue'),
                  Intent(intent='products',risk=2)])
    class Model:
        def invoke(self,_):return next(answers)
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    assert client.post('/api/chat',json={'message':'我想买R1理财'}).status_code==200
    message = '我想买理财，最多能投多少？' if include_forecast else '我想买理财'
    clarified=client.post('/api/chat',json={'message':message}).json()
    assert clarified['evidence']['missing_fields']==['产品风险等级']
    assert ('计算可投额度' in clarified['text']) == include_forecast
    completed=client.post('/api/chat',json={'message':'R2中低风险'}).json()
    assert completed['evidence']['result']['risk']==2
    expected_actions = {'查看理财产品','查看现金流预测'} if include_forecast else {'查看理财产品'}
    assert {a['label'] for a in completed['actions']}==expected_actions
    assert ('建议投入不超过' in completed['text']) == include_forecast


def test_missing_product_risk_never_defaults_to_capacity(client):
    with closing(db.connect_db()) as c:
        response=agent.respond(c,Intent(intent='products',include_forecast=True))
    assert response.evidence['missing_fields']==['产品风险等级']
    assert not response.actions


def test_exact_product_name_resolves_risk_without_repeating_question(client, monkeypatch):
    class Model:
        def invoke(self, _):
            # The model misses the catalog match and repeats the previous generic intent.
            return Intent(intent='products', risk=None, context_mode='continue')

    monkeypatch.setattr(agent, 'intent_client', lambda: Model())
    first = client.post('/api/chat', json={'message': '我想买理财'})
    assert first.status_code == 200, first.text
    assert first.json()['evidence']['missing_fields'] == ['产品风险等级']

    second = client.post('/api/chat', json={'message': '我想买进取精选365天'})
    assert second.status_code == 200, second.text
    body = second.json()
    assert '哪个风险等级' not in body['text']
    assert '进取精选 365 天' in body['text']
    assert 'R3' in body['text']
    assert body['evidence']['product_id'] == 'O'
    assert body['evidence']['reply_source'] == 'suitability_rule'


def test_named_eligible_product_targets_only_that_product(client, monkeypatch):
    monkeypatch.setattr(agent, 'interpret',
                        lambda *args, **kwargs: Intent(intent='products', risk=None))
    response = client.post('/api/chat', json={'message': '我想买安盈现金管理'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert '哪个风险等级' not in body['text']
    assert body['evidence']['result']['risk'] == 1
    assert [p['id'] for p in body['evidence']['result']['products']] == ['D']
    assert body['actions'][0]['resultIds'] == ['D']


@pytest.mark.parametrize('model_intent', ['explain', 'forecast', 'purchase'])
def test_named_product_followup_cannot_skip_unmet_risk_capacity(client, monkeypatch, model_intent):
    calls = 0
    def parse(*args, **kwargs):
        nonlocal calls
        calls += 1
        return Intent(intent='products' if calls == 1 else model_intent)
    monkeypatch.setattr(agent, 'interpret', parse)
    first = client.post('/api/chat', json={'message': '我想买进取精选365天'})
    assert first.status_code == 200, first.text
    followup = client.post('/api/chat', json={'message': '现在可以买吗'})
    assert followup.status_code == 200, followup.text
    body = followup.json()
    assert body['evidence']['reply_source'] == 'suitability_rule'
    assert body['evidence']['product_id'] == 'O'
    assert 'R3 产品无法申购' in body['text']
    assert all(action.get('modal') != 'buy' for action in body['actions'])


@pytest.mark.parametrize('model_intent', ['products', 'purchase', 'forecast'])
def test_risk_reassessment_named_product_followup_warns_when_min_exceeds_cashflow(client, monkeypatch, model_intent):
    # The model may omit the product and forecast slots in an elliptical follow-up.
    calls = 0
    def parse(*args, **kwargs):
        nonlocal calls
        calls += 1
        return Intent(intent='products' if calls == 1 else model_intent, risk=None)
    monkeypatch.setattr(agent, 'interpret', parse)
    first = client.post('/api/chat', json={'message': '我想买进取精选365天'})
    assert first.status_code == 200, first.text
    assert first.json()['evidence']['reply_source'] == 'suitability_rule'

    reassessed = operation(client, {'kind': 'risk', 'answers': [3] * 10})
    assert reassessed.status_code == 200, reassessed.text
    assert reassessed.json()['state']['riskLevel'] == 3

    followup = client.post('/api/chat', json={'message': '现在可以买吗'})
    assert followup.status_code == 200, followup.text
    body = followup.json()
    evidence = body['evidence']
    assert evidence['product_id'] == 'O'
    assert evidence['cashflow_conflict'] is True
    assert evidence['min_purchase'] == 10000
    assert evidence['recommended_cap'] < 10000
    assert evidence['reply_source'] == 'cashflow_rule'
    assert '起购金额' in body['text'] and '高于' in body['text'] and '现金流建议投入上限' in body['text']
    assert f"¥{evidence['recommended_cap']:,.2f}" in body['text']
    assert {action['label'] for action in body['actions']} == {'查看理财产品', '查看现金流预测'}


def test_named_purchase_over_cashflow_limit_warns_and_still_requires_confirmation(client, monkeypatch):
    reassessed = operation(client, {'kind': 'risk', 'answers': [3] * 10})
    assert reassessed.status_code == 200, reassessed.text
    monkeypatch.setattr(agent, 'interpret',
                        lambda *args, **kwargs: Intent(intent='purchase', product_id='O', amount=10000))
    before = client.get('/api/account-state').json()['balance']
    response = client.post('/api/chat', json={'message': '申购进取精选365天10000元'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['evidence']['cashflow_conflict'] is True
    assert '高于现金流建议上限' in body['text']
    assert body['actions'][0]['modal'] == 'buy'
    assert client.get('/api/account-state').json()['balance'] == before
    preview = client.post('/api/operations/preview',
                          json={'kind': 'purchase', 'product_id': 'O', 'amount': 10000})
    assert preview.status_code == 200, preview.text
    assert preview.json()['requires_buffer_confirmation'] is True


def test_product_followup_ignores_model_invented_topic_reference(client, monkeypatch):
    calls = 0
    def parse(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return Intent(intent='products')
        return Intent(intent='products', product_id='O',
                      context_topic_id='invented-topic-id', context_mode='continue')
    monkeypatch.setattr(agent, 'interpret', parse)
    first = client.post('/api/chat', json={'message': '我想买进取精选365天'})
    assert first.status_code == 200, first.text
    followup = client.post('/api/chat', json={'message': '现在可以买吗'})
    assert followup.status_code == 200, followup.text
    assert followup.json()['evidence']['reply_source'] == 'suitability_rule'
    assert followup.json()['evidence']['product_id'] == 'O'


@pytest.mark.parametrize('risk,days,redeemable,expected', [(1,0,True,9400),(1,30,True,7400),(1,0,False,7400),(2,0,True,9400)])
def test_liquidity_limit_depends_on_redemption_not_risk(risk,days,redeemable,expected):
    from backend.app.services.investment_limits import investment_limit
    result=investment_limit({'minimum':9400,'reserve':2000,'cap':7400},
                            [{'id':'test','risk':risk,'days':days,'redeemable':redeemable}])
    assert result['cap']==expected
    assert result['reserve']==2000
    assert result['deducted_reserve']==(0 if expected==9400 else 2000)


def test_daily_redeemable_recommendation_and_limit_followup(client,monkeypatch):
    from backend.app.agent import tools
    projection={'minimum':9400,'reserve':2000,'cap':7400,'points':[]}
    monkeypatch.setattr(tools,'forecast',lambda *a,**k:dict(projection))
    monkeypatch.setitem(agent.TOOLS,'forecast',lambda *a,**k:dict(projection))
    class Model:
        def invoke(self,messages):
            return Intent(intent='products',risk=1,include_forecast=True) if 'R1' in messages[-1][1] else Intent(intent='explain')
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    first=client.post('/api/chat',json={'message':'我想买R1理财，最多能投多少？'})
    assert first.status_code==200,first.text
    result=first.json()['evidence']['result']
    assert result['cashflow']['cap']==9400
    assert result['cashflow']['reserve']==2000
    assert all(item['deducted_reserve']==0 for item in result['investment_limits'])
    followup=client.post('/api/chat',json={'message':'为什么是9400？'})
    assert followup.status_code==200,followup.text
    body=followup.json()
    assert body['evidence']['result']['cap']==9400
    assert body['evidence']['previous_basis']['cap']==9400
    assert body['evidence']['result']['deducted_reserve']==0
    assert '每日可赎回' in body['text'] and '不再扣除' in body['text']


def test_mixed_r1_products_have_separate_liquidity_limits(client,monkeypatch):
    from backend.app.agent import tools
    monkeypatch.setattr(tools,'forecast',lambda *a,**k:{'minimum':9400,'reserve':2000,'cap':7400,'points':[]})
    with closing(db.connect_db()) as c,c:
        c.execute("UPDATE wealth_products SET term_days=30,is_redeemable=0 WHERE id='E'")
        result=agent.respond(c,Intent(intent='products',risk=1,include_forecast=True))
    limits={item['product_id']:item['cap'] for item in result.evidence['result']['investment_limits']}
    assert limits['D']==9400 and limits['E']==7400
    assert '9400' in result.text and '7400' in result.text


def test_multi_month_bill_followup_fetches_all_requested_months(client, monkeypatch):
    with closing(db.connect_db()) as c, c:
        c.execute('DELETE FROM bill_transactions')
        c.executemany('INSERT INTO bill_transactions(id,month,transaction_date,category,counterparty,amount,direction) VALUES (?,?,?,?,?,?,?)', [
            ('aug','2026-08','2026-08-10','餐饮','餐厅',4800,'支出'),
            ('sep','2026-09','2026-09-10','餐饮','餐厅',5000,'支出'),
            ('oct','2026-10','2026-10-10','餐饮','餐厅',6000,'支出'),
        ])
    class Model:
        def invoke(self, messages):
            if messages[-1][1] == '帮我分析上个月的账单':
                return Intent(intent='bills', month='2026-10')
            # Reproduce the original model output: no multiple-month slots.
            return Intent(intent='bills', context_mode='continue')
    monkeypatch.setattr(agent, 'intent_client', lambda: Model())
    assert client.post('/api/chat', json={'message':'帮我分析上个月的账单'}).status_code == 200
    response = client.post('/api/chat', json={'message':'和8月9月的消费相比有什么区别'})
    assert response.status_code == 200, response.text
    data = response.json()
    comparison = data['evidence']['result']['comparison']
    assert comparison['baseline_month'] == '2026-10'
    assert {item['month']:item['total'] for item in comparison['months']} == {'2026-10':6000, '2026-08':4800, '2026-09':5000}
    assert comparison['missing_months'] == []
    assert [(item['change'],item['change_pct']) for item in comparison['differences']] == [(1200,25),(1000,20)]
    assert all(month in data['text'] for month in ('2026-08','2026-09','2026-10'))
    assert data['response_mode'] == 'direct'


def test_multi_month_bill_comparison_missing_month_and_category(client):
    with closing(db.connect_db()) as c, c:
        c.execute('DELETE FROM bill_transactions')
        c.executemany('INSERT INTO bill_transactions(id,month,transaction_date,category,counterparty,amount,direction) VALUES (?,?,?,?,?,?,?)', [
            ('food','2026-10','2026-10-10','餐饮','餐厅',300,'支出'),
            ('shop','2026-10','2026-10-11','购物','商店',600,'支出'),
            ('sep','2026-09','2026-09-10','购物','商店',500,'支出'),
        ])
        response = agent.respond(c, Intent(intent='bills',month='2026-10',category='餐饮',comparison_months=['2026-09','2026-07']))
    comparison = response.evidence['result']['comparison']
    assert comparison['missing_months'] == ['2026-07']
    assert comparison['months'][1]['available'] is True
    assert comparison['months'][1]['total'] == 0
    assert comparison['differences'][0]['change'] == 300
    assert comparison['differences'][0]['change_pct'] is None


def test_query_and_legacy_write_blocked(client):
    assert client.get('/api/finance/overview').status_code == 200
    assert client.put('/api/account-state', json={'state': {'balance': 999999}}).status_code == 410
    assert client.post('/api/operations/preview', json={'kind':'purchase','amount':-1}).status_code == 422


def test_idempotent_concurrent_confirmation(client):
    p = client.post('/api/operations/preview', json={'kind':'purchase','product_id':'D','amount':1000}).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post('/api/operations/'+p['preview_id']+'/confirm',json={'confirmed':True}),range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json() == responses[1].json()
    assert client.get('/api/account-state').json()['balance'] == 11000
    with closing(db.connect_db()) as c:
        assert c.execute('SELECT COUNT(*) FROM account_transactions').fetchone()[0] == 1


def test_stale_preview_and_no_confirmation(client):
    p = client.post('/api/operations/preview', json={'kind':'purchase','product_id':'D','amount':1000}).json()
    assert operation(client, {'kind':'transfer','amount':10,'payee':'测试'}).status_code == 200
    url='/api/operations/'+p['preview_id']+'/confirm'
    assert client.post(url,json={'confirmed':False}).status_code == 422
    assert client.post(url,json={'confirmed':True}).status_code == 409


def test_buffer_second_confirmation_and_hard_block(client):
    command={'kind':'purchase','product_id':'D','amount':11000}
    p=client.post('/api/operations/preview',json=command).json()
    assert p['requires_buffer_confirmation']
    assert p['requires_challenge']
    url='/api/operations/'+p['preview_id']+'/confirm'
    # buffer acknowledgement is checked before identity challenge is consumed
    assert client.post(url,json={'confirmed':True}).status_code == 409
    ch=client.post('/api/operations/'+p['preview_id']+'/challenge',json={'password':'test-password-123'})
    assert ch.status_code == 200, ch.text
    assert client.post(url,json={'confirmed':True,'buffer_acknowledged':True,'challenge_token':ch.json()['challenge_token']}).status_code == 200
    assert client.post('/api/operations/preview',json=command).status_code == 409


def test_redemption_realizes_profit(client):
    r=operation(client,{'kind':'redemption','product_id':'D','amount':8000})
    assert r.status_code == 200
    state=r.json()['state']
    assert state['balance'] == 20014.55
    assert all(h['productId'] != 'D' for h in state['holdings'])


def test_risk_questionnaire_and_product_recheck(client):
    assert client.post('/api/operations/preview',json={'kind':'risk','answers':[5]}).status_code == 422
    assert operation(client,{'kind':'risk','answers':[1]*10}).json()['state']['riskLevel'] == 1
    with closing(db.connect_db()) as c, c:
        c.execute("UPDATE wealth_products SET risk_level=5 WHERE id='D'")
    assert client.post('/api/operations/preview',json={'kind':'purchase','product_id':'D','amount':1000}).status_code == 409


def test_calendar_and_cashflow(client):
    with closing(db.connect_db()) as c:
        state=snapshot(c); products=db.products_from_db(c)
    state['tasks']=[{'id':'rent','type':'fixed_expense','name':'租金','amount':3000,'frequency':'monthly','authorized':True,'authorizedAt':'2026-11-01','executionDay':5,'status':'运行中'}]
    result=forecast(state,products,date(2026,11,1))
    assert result['minimum'] == 7700
    assert result['cap'] == 5700
    task={**state['tasks'][0],'executionDay':31}
    assert occurs(task,date(2027,2,28))
    assert not occurs(task,date(2027,2,27))


def test_scheduler_survives_browser_and_deduplicates(client):
    r=operation(client,{'kind':'task','task_type':'fixed_expense','name':'租金','amount':100,'payee':'房东','execution_day':5})
    assert r.status_code == 200
    with closing(db.connect_db()) as c, c:
        c.execute("UPDATE scheduled_tasks SET authorized_at='2026-11-01'")
    run_due(date(2026,11,5));run_due(date(2026,11,5))
    assert client.get('/api/account-state').json()['balance'] == 11900
    with closing(db.connect_db()) as c:
        assert c.execute("SELECT COUNT(*) FROM task_runs WHERE status='executed'").fetchone()[0] == 1


def test_model_failure_and_grounded_response(client, monkeypatch):
    class Fake:
        def invoke(self,messages):return Intent(intent='products',risk=1)
    monkeypatch.setattr(agent,'intent_client',lambda:Fake())
    r=client.post('/api/chat',json={'message':'我想看R1理财'})
    assert r.status_code == 200
    assert r.json()['actions'][0]['risk'] == 1
    assert all(p['risk']==1 for p in r.json()['evidence']['result']['products'])
    assert len(client.get('/api/conversation/messages').json()) == 2
    def fail():raise RuntimeError('secret-must-not-leak')
    monkeypatch.setattr(agent,'intent_client',fail)
    r=client.post('/api/chat',json={'message':'余额'})
    assert r.status_code == 200 and 'secret-must-not-leak' not in r.text
    assert r.json()['text'] == '抱歉，知财暂不支持这类请求。'


def test_missing_slots_and_new_lot_performance(client,monkeypatch):
    class Fake:
        def invoke(self,_):return Intent(intent='task',task_type='fixed_expense')
    monkeypatch.setattr(agent,'intent_client',lambda:Fake())
    r=client.post('/api/chat',json={'message':'帮我付房租'}).json()
    assert r['response_mode']=='direct' and not r['actions']
    assert '收款方' in r['text']
    with closing(db.connect_db()) as c:
        state=snapshot(c);products=db.products_from_db(c)
    state['holdings']=[{'productId':'D','principal':100,'profit':0,'lots':[{'principal':100,'purchaseDate':'2026-11-01'}]}]
    assert performance(state,products,date(2026,11,1))[0]['annualized'] is None


def test_import_with_existing_holdings(client):
    with closing(db.connect_db()) as c:
        db.import_excel_sources(c,{db.PRODUCTS_XLSX.name:db.file_fingerprint(db.PRODUCTS_XLSX),db.BILLS_XLSX.name:db.file_fingerprint(db.BILLS_XLSX)})
        assert snapshot(c)['holdings']


def test_invalid_selected_profile_fails(monkeypatch):
    monkeypatch.setenv('ZHICAI_LLM_MODEL','missing-profile')
    load_settings.cache_clear()
    with pytest.raises(RuntimeError,match='配置不存在'):load_settings()
    load_settings.cache_clear()


def test_expired_preview_and_changed_product(client):
    command={'kind':'purchase','product_id':'D','amount':1000}
    p=client.post('/api/operations/preview',json=command).json()
    with closing(db.connect_db()) as c,c:
        c.execute("UPDATE operation_previews SET expires_at='2000-01-01T00:00:00+00:00'")
    assert client.post('/api/operations/'+p['preview_id']+'/confirm',json={'confirmed':True}).status_code==409
    p=client.post('/api/operations/preview',json=command).json()
    with closing(db.connect_db()) as c,c:
        c.execute("UPDATE wealth_products SET is_open=0 WHERE id='D'")
    assert client.post('/api/operations/'+p['preview_id']+'/confirm',json={'confirmed':True}).status_code==409
    assert client.get('/api/account-state').json()['balance']==12000


def test_missed_schedule_is_recorded_not_charged(client):
    operation(client,{'kind':'task','task_type':'fixed_expense','amount':100,'name':'租金','payee':'房东','execution_day':5})
    with closing(db.connect_db()) as c,c:
        c.execute("UPDATE scheduled_tasks SET authorized_at='2026-10-01'")
    run_due(date(2026,11,6));run_due(date(2026,11,6))
    assert client.get('/api/account-state').json()['balance']==12000
    rows=client.get('/api/tasks/runs').json()
    assert len(rows)==2 and all(x['status']=='skipped' for x in rows)


def test_model_operation_is_only_draft(client,monkeypatch):
    class Fake:
        def invoke(self,_):return Intent(intent='purchase',product_id='D',amount=1000)
    monkeypatch.setattr(agent,'intent_client',lambda:Fake())
    response=client.post('/api/chat',json={'message':'买入一千'}).json()
    assert response['response_mode']=='open_action'
    assert client.get('/api/account-state').json()['balance']==12000


def test_lifespan_and_reset_use_server_seed(client,monkeypatch):
    monkeypatch.setenv('ZHICAI_LLM_MODEL','')
    load_settings.cache_clear()
    with TestClient(app) as running:
        login = running.post('/api/auth/login',json={'username':'test-account','password':'test-password-123'})
        assert login.status_code == 200,login.text
        running.headers.update({'X-CSRF-Token':login.json()['csrf_token']})
        assert running.get('/api/health').json()['api_version']=='0.3.0'
        # account_user cannot reset demo data
        assert running.post('/api/admin/reset').status_code == 403
        # admin reset reseeds from the server-owned value, ignoring any client payload
        admin = TestClient(app)
        alogin = admin.post('/api/auth/login',json={'username':'test-admin','password':'test-admin-password-123'})
        assert alogin.status_code == 200, alogin.text
        admin.headers.update({'X-CSRF-Token':alogin.json()['csrf_token']})
        assert admin.post('/api/admin/reset').status_code == 200
        assert running.get('/api/account-state').json()['balance']==12000


def test_real_langchain_schema_adapter(monkeypatch):
    from backend.app import llm_client
    from backend.app.config import Settings
    monkeypatch.setattr(llm_client,'load_settings',lambda:Settings(profile='test',model='test',base_url='http://127.0.0.1:1/v1',api_key='unused'))
    llm_client.intent_client.cache_clear()
    adapter=llm_client.intent_client()
    assert adapter is not None
    llm_client.intent_client.cache_clear()


def test_narrative_is_generated_and_not_template(client,monkeypatch):
    from backend.app.agent import narrative
    from backend.app.agent.schemas import Narrative
    class IntentModel:
        def invoke(self,_):return Intent(intent='account')
    class Writer:
        def invoke(self,messages):
            context=json.loads(messages[-1][1])['context']
            return Narrative(text='这笔备用金我们一起看：'+context['required_text'])
    monkeypatch.setattr(agent,'intent_client',lambda:IntentModel())
    monkeypatch.setattr(narrative,'narrative_client',lambda:Writer())
    result=client.post('/api/chat',json={'message':'我有多少可用的钱？'})
    assert result.status_code==200,result.text
    assert result.json()['text'].startswith('这笔备用金我们一起看')
    assert result.json()['evidence']['reply_source']=='llm_grounded'
    assert '12000.00' in result.json()['text']


@pytest.mark.parametrize('bad_text',['你的余额是999999元','你的余额是{{fact_99999}}元','请确认，我已经替你买入1000元'])
def test_narrative_unproven_numbers_are_not_shown(client,monkeypatch,bad_text):
    from backend.app.agent import narrative
    from backend.app.agent.schemas import Narrative
    class IntentModel:
        def invoke(self,_):return Intent(intent='account')
    class Writer:
        def invoke(self,_):return Narrative(text=bad_text)
    monkeypatch.setattr(agent,'intent_client',lambda:IntentModel())
    monkeypatch.setattr(narrative,'narrative_client',lambda:Writer())
    response=client.post('/api/chat',json={'message':'余额'})
    assert response.status_code==200,response.text
    assert response.json()['evidence']['reply_source']=='evidence_fallback'
    assert bad_text not in response.json()['text']
    assert '12000.00' in response.json()['text']
    assert len(client.get('/api/conversation/messages').json())==2


def test_deepseek_profile(monkeypatch):
    monkeypatch.setenv('ZHICAI_LLM_MODEL','deepseek')
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-key-not-real')
    settings=load_settings()
    assert settings.model=='deepseek-v4-pro'
    assert settings.provider=='deepseek'
    from backend.app import llm_client
    from langchain_deepseek import ChatDeepSeek
    assert isinstance(llm_client.model_client(),ChatDeepSeek)
    assert llm_client.model_client().extra_body['thinking']['type']=='disabled'


def test_deepseek_missing_key_explicit(monkeypatch):
    monkeypatch.setenv('ZHICAI_LLM_MODEL','deepseek')
    monkeypatch.setenv('DEEPSEEK_API_KEY','')
    with pytest.raises(RuntimeError,match='DEEPSEEK_API_KEY'):load_settings()


def test_deepseek_sdk_two_stage_wire_format(client,monkeypatch):
    import httpx
    from langchain_deepseek import ChatDeepSeek
    from backend.app import llm_client
    from backend.app.agent import narrative
    requests=[]
    def handle(request):
        assert request.headers['accept-encoding']=='gzip, deflate'
        payload=json.loads(request.content)
        requests.append(payload)
        if not payload.get('tools'):
            context=json.loads(payload['messages'][-1]['content'])['context']
            return httpx.Response(200,json={'id':'test-prose','object':'chat.completion','created':1,'model':'deepseek-v4-pro',
                'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'让我结合你的账户解释：'+context['required_text']}}]})
        function=payload['tools'][0]['function']['name']
        arguments={'intent':'account'}
        return httpx.Response(200,json={'id':'test','object':'chat.completion','created':1,'model':'deepseek-v4-pro',
          'choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':'',
          'tool_calls':[{'id':'call-test','type':'function','function':{'name':function,'arguments':json.dumps(arguments,ensure_ascii=False)}}]}}],
          'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
    monkeypatch.setenv('ZHICAI_LLM_MODEL','deepseek')
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-not-real')
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        model=ChatDeepSeek(model='deepseek-v4-pro',api_key='test-not-real',base_url='https://api.deepseek.com',
                           http_client=http,extra_body={'thinking':{'type':'disabled'}},max_retries=0,
                           default_headers={'Accept-Encoding':'gzip, deflate'})
        monkeypatch.setattr(llm_client,'model_client',lambda:model)
        # Keep the real LangChain parsers; only the HTTP transport is fake.
        actual_writer=llm_client.narrative_client
        monkeypatch.setattr(narrative,'narrative_client',actual_writer)
        r=client.post('/api/chat',json={'message':'余额多少？'})
        assert r.status_code==200,r.text
        assert r.json()['text'].startswith('让我结合你的账户解释')
        assert len(requests)==2
        assert all(p['model']=='deepseek-v4-pro' and p['thinking']['type']=='disabled' for p in requests)


def test_category_followup_keeps_previous_month(client,monkeypatch):
    with closing(db.connect_db()) as c,c:
        c.execute('DELETE FROM bill_transactions')
        c.executemany('INSERT INTO bill_transactions VALUES (?,?,?,?,?,?,?)',[
            ('test-a','2026-09-01','2026-09','支出','测试餐馆甲','餐饮',12.30),
            ('test-b','2026-09-02','2026-09','支出','测试餐馆甲','餐饮',7.70),
            ('test-c','2026-09-03','2026-09','支出','测试餐馆乙','餐饮',30),
            ('test-d','2026-09-04','2026-09','支出','测试商店','购物',50)])
    class IntentModel:
        def invoke(self,messages):
            if messages[-1][1]=='分析9月的账单':
                return Intent(intent='bills',month='2026-09')
            assert '2026-09' in str(messages[1:])
            return Intent(intent='bills',category='餐饮')
    monkeypatch.setattr(agent,'intent_client',lambda:IntentModel())
    first=client.post('/api/chat',json={'message':'分析9月的账单'})
    assert first.status_code==200,first.text
    second=client.post('/api/chat',json={'message':'需要拆解餐饮支出'})
    assert second.status_code==200,second.text
    body=second.json()
    assert body['response_mode']=='direct' and not body['actions']
    assert body['evidence']['result']['month']=='2026-09'
    detail=body['evidence']['result']['category_detail']
    assert detail['total']==50 and detail['count']==3 and detail['average']==16.67
    assert detail['share_pct']==50 and detail['merchant_count']==2
    assert detail['merchants'][0]['merchant']=='测试餐馆乙'
    assert detail['merchants'][1]['amount']==20 and detail['merchants'][1]['count']==2
    assert detail['top_transactions'][0]['amount']==30
    assert '测试餐馆甲' in body['text'] and '测试餐馆乙' in body['text']
    assert len(client.get('/api/conversation/messages').json())==4
    # Context is structured, so a third follow-up also keeps September.
    third=client.post('/api/chat',json={'message':'继续查看餐饮'})
    assert third.json()['evidence']['result']['month']=='2026-09'


def test_category_explicit_new_month_and_empty_category(client,monkeypatch):
    class IntentModel:
        def invoke(self,_):return Intent(intent='bills',month='2025-01',category='餐饮')
    monkeypatch.setattr(agent,'intent_client',lambda:IntentModel())
    result=client.post('/api/chat',json={'message':'查看2025年1月餐饮支出'})
    assert result.status_code==200,result.text
    body=result.json()
    assert body['evidence']['result']['month']=='2025-01'
    assert body['evidence']['result']['category_detail']['count']==0
    assert '没有餐饮支出记录' in body['text']


def test_narrative_ids_are_not_sent_to_model(client,monkeypatch):
    from backend.app.agent import narrative
    from backend.app.agent.schemas import Narrative,AgentResponse,Action
    class Writer:
        def invoke(self,messages):
            payload=json.loads(messages[-1][1])
            assert 'DO-NOT-SEND-SOURCE-ID' not in messages[-1][1]
            return Narrative(text=payload['context']['required_text'])
    monkeypatch.setattr(narrative,'narrative_client',lambda:Writer())
    draft=AgentResponse(text='合计20元。',evidence={'sourceIds':['DO-NOT-SEND-SOURCE-ID'],'amount':20})
    result=narrative.generate('拆解餐饮',[],Intent(intent='bills',category='餐饮'),draft)
    assert result.evidence['sourceIds']==['DO-NOT-SEND-SOURCE-ID']


def test_narrative_failure_stage_is_identified(client,monkeypatch,caplog):
    from backend.app.agent import narrative
    class IntentModel:
        def invoke(self,_):return Intent(intent='bills',month='2026-09',category='餐饮')
    class Writer:
        def invoke(self,_):raise RuntimeError('sensitive-do-not-print')
    monkeypatch.setattr(agent,'intent_client',lambda:IntentModel())
    monkeypatch.setattr(narrative,'narrative_client',lambda:Writer())
    result=client.post('/api/chat',json={'message':'拆解餐饮'})
    assert result.status_code==200
    assert result.json()['evidence']['reply_source']=='evidence_fallback'
    assert 'narrative_model' in caplog.text
    assert 'sensitive-do-not-print' not in result.text+caplog.text


def test_focused_answer_may_omit_reference_and_use_literal_numbers(client,monkeypatch):
    from backend.app.agent import narrative
    from backend.app.agent.schemas import Narrative,AgentResponse
    class Writer:
        def invoke(self,_):
            return Narrative(text='1. 测试咖啡有4笔消费，合计80元，可先看看是否都必要。')
    monkeypatch.setattr(narrative,'narrative_client',lambda:Writer())
    draft=AgentResponse(text='2026-09总支出600元。',evidence={'result':{'month':'2026-09','total':600,
        'frequency_detail':{'merchants':[{'merchant':'测试咖啡','count':4,'amount':80}]}}})
    result=narrative.generate('有什么高频消费吗',[],Intent(intent='bills',bill_focus='frequent'),draft)
    assert result.text.startswith('1. 测试咖啡有4笔')
    assert '600' not in result.text
    assert result.evidence['reply_source']=='llm_grounded'


def test_optional_bubble_and_natural_clarification_are_not_errors(client,monkeypatch):
    from backend.app.agent import narrative
    from backend.app.agent.schemas import Narrative,AgentResponse,Action
    class Writer:
        def invoke(self,_):return Narrative(text='你准备每次转多少钱，转给谁？')
    monkeypatch.setattr(narrative,'narrative_client',lambda:Writer())
    draft=AgentResponse(text='请补充金额和收款方',evidence={'missing_fields':['金额','收款方']})
    result=narrative.generate('帮我转账',[],Intent(intent='transfer'),draft)
    assert result.evidence['reply_source']=='llm_grounded' and not result.actions
    draft=AgentResponse(text='账单合计80元',actions=[Action(label='查看账单',path='/bills',bubbleSegments=['账单合计80元','查看明细'])])
    result=narrative.generate('看看账单',[],Intent(intent='bills'),draft)
    assert result.evidence['reply_source']=='llm_grounded'
    assert result.actions[0].bubbleSegments[0]=='账单合计80元'


def test_bad_numeric_claim_gets_one_repair_attempt(client,monkeypatch):
    from backend.app.agent import narrative
    from backend.app.agent.schemas import Narrative,AgentResponse
    calls=[]
    class Writer:
        def invoke(self,messages):
            calls.append(messages)
            return Narrative(text='合计999999元') if len(calls)==1 else Narrative(text='已核实合计80元，可以进一步看商户构成。')
    monkeypatch.setattr(narrative,'narrative_client',lambda:Writer())
    result=narrative.generate('高频消费',[],Intent(intent='bills'),AgentResponse(text='合计80元',evidence={'amount':80}))
    assert len(calls)==2
    assert result.evidence['reply_source']=='llm_grounded_repaired'
    assert '999999' not in result.text


def test_frequent_consumption_is_not_a_fixed_coffee_keyword(client,monkeypatch):
    with closing(db.connect_db()) as c,c:
        c.execute('DELETE FROM bill_transactions')
        rows=[]
        for merchant,count,amount,category in [('虚构早餐店',4,10,'餐饮'),('虚构地铁',3,5,'出行'),('虚构偶发商户',2,20,'购物')]:
            for index in range(count):
                rows.append((merchant+str(index),f'2026-09-{index+1:02d}','2026-09','支出',merchant,category,amount))
        c.executemany('INSERT INTO bill_transactions VALUES (?,?,?,?,?,?,?)',rows)
    class IntentModel:
        def invoke(self,messages):
            if messages[-1][1]=='分析9月的账单':return Intent(intent='bills',month='2026-09')
            return Intent(intent='bills',bill_focus='frequent')
    monkeypatch.setattr(agent,'intent_client',lambda:IntentModel())
    assert client.post('/api/chat',json={'message':'分析9月的账单'}).status_code==200
    result=client.post('/api/chat',json={'message':'有什么高频消费吗'})
    assert result.status_code==200,result.text
    body=result.json()
    assert body['response_mode']=='direct' and not body['actions']
    assert body['evidence']['result']['month']=='2026-09'
    detail=body['evidence']['result']['frequency_detail']
    assert detail['minimum_count']==3 and detail['transaction_count']==7
    assert detail['total']==55
    assert [item['merchant'] for item in detail['merchants']]==['虚构早餐店','虚构地铁']
    assert '虚构偶发商户' not in body['text']


def test_finance_explanation_is_a_supported_intent(client,monkeypatch):
    class IntentModel:
        def invoke(self,_):return Intent(intent='explain')
    monkeypatch.setattr(agent,'intent_client',lambda:IntentModel())
    result=client.post('/api/chat',json={'message':'为什么需要留应急资金？'})
    assert result.status_code==200,result.text
    assert result.json()['evidence']['topic']=='general_finance'
    assert not result.json()['actions']


@pytest.mark.parametrize('task_type,product,payee',[('fixed_expense',None,'测试收款人'),('investment','D',None),('redemption','all',None)])
def test_task_confirmation_is_exact_and_never_calls_prose_model(client,monkeypatch,task_type,product,payee):
    from backend.app.agent.task_dialogue import TASK_CONFIRMATION
    calls=[]
    class IntentModel:
        def invoke(self,messages):
            calls.append(messages)
            return Intent(intent='task',task_type=task_type,amount='3000.50',frequency='monthly',
                          execution_day=31,payee=payee,product_id=product,name='租金' if payee else None)
    def forbidden(*args,**kwargs):raise AssertionError('Task confirmation must not call prose generation')
    monkeypatch.setattr(agent,'intent_client',lambda:IntentModel())
    monkeypatch.setattr(agent,'generate',forbidden)
    response=client.post('/api/chat',json={'message':'每月31号执行3000.50元的定时任务'})
    assert response.status_code==200,response.text
    body=response.json()
    assert body['text']==TASK_CONFIRMATION=='请核对定时任务信息，授权后开始执行。'
    assert body['response_mode']=='open_action'
    assert len(body['actions'])==1 and body['actions'][0]['label']=='授权定时任务'
    action=body['actions'][0]
    assert action['modal']=='task' and action['taskType']==task_type
    assert action['amount']==3000.50 and action['day']==31
    assert action['frequency']=='monthly' and action['payee']==payee and action['productId']==product
    assert body['evidence']['reply_source']=='task_rule'
    assert len(calls)==1
    state=client.get('/api/account-state').json()
    assert state['balance']==12000 and not state['tasks']
    assert client.get('/api/conversation/messages').json()[-1]['text']==TASK_CONFIRMATION


def test_task_missing_fields_are_collected_over_multiple_rounds(client,monkeypatch):
    calls=[]
    class IntentModel:
        def invoke(self,messages):
            calls.append(messages)
            if len(calls)==1:return Intent(intent='task',task_type='fixed_expense',frequency='monthly',payee='测试房东',name='房租')
            if len(calls)==2:return Intent(intent='task',amount=3000,continues_task=True)
            return Intent(intent='task',execution_day=5,continues_task=True)
    def forbidden(*args,**kwargs):raise AssertionError('Task dialogue must not call prose generation')
    monkeypatch.setattr(agent,'intent_client',lambda:IntentModel())
    monkeypatch.setattr(agent,'generate',forbidden)
    first=client.post('/api/chat',json={'message':'每月给测试房东转房租'}).json()
    assert not first['actions'] and first['response_mode']=='direct'
    assert '每次金额' in first['text'] and '执行日' in first['text']
    second=client.post('/api/chat',json={'message':'3000元'}).json()
    assert not second['actions'] and '执行日' in second['text']
    assert '每次金额' not in second['text']
    third=client.post('/api/chat',json={'message':'每月5号'}).json()
    assert third['text']=='请核对定时任务信息，授权后开始执行。'
    action=third['actions'][0]
    assert action['amount']==3000 and action['day']==5 and action['frequency']=='monthly'
    assert action['payee']=='测试房东' and action['taskType']=='fixed_expense'
    assert len(calls)==3 and any('测试房东' in m[1] for m in calls[1])
    assert not client.get('/api/account-state').json()['tasks']


def test_new_task_does_not_reuse_pending_fields(client,monkeypatch):
    class IntentModel:
        count=0
        def invoke(self,_):
            self.count+=1
            return (Intent(intent='task',task_type='fixed_expense',amount=800,frequency='monthly',payee='旧收款方')
                    if self.count==1 else Intent(intent='task',task_type='fixed_expense',name='生活缴费'))
    model=IntentModel()
    monkeypatch.setattr(agent,'intent_client',lambda:model)
    client.post('/api/chat',json={'message':'每月向旧收款方转800元'})
    body=client.post('/api/chat',json={'message':'另外新建一个生活缴费任务'}).json()
    assert not body['actions']
    assert '每次金额' in body['text'] and '收款方' in body['text'] and '执行周期' in body['text']


def test_unknown_task_product_requires_clarification(client,monkeypatch):
    class IntentModel:
        def invoke(self,_):return Intent(intent='task',task_type='investment',amount=1000,frequency='monthly',execution_day=5,product_id='NOT-A-PRODUCT')
    monkeypatch.setattr(agent,'intent_client',lambda:IntentModel())
    body=client.post('/api/chat',json={'message':'定时申购未知产品'}).json()
    assert not body['actions'] and '具体理财产品' in body['text']


def test_out_of_scope_reply_is_fixed_without_prose_call(client,monkeypatch):
    calls=[]
    class IntentModel:
        def invoke(self,messages):
            calls.append(messages)
            return Intent(intent='unknown')
    def forbidden(*args,**kwargs):raise AssertionError('Out-of-scope reply must not call prose generation')
    monkeypatch.setattr(agent,'intent_client',lambda:IntentModel())
    monkeypatch.setattr(agent,'generate',forbidden)
    response=client.post('/api/chat',json={'message':'帮我写一首天气主题的诗'})
    assert response.status_code==200,response.text
    body=response.json()
    assert body['text']=='抱歉，知财暂不支持这类请求。'
    assert body['response_mode']=='direct' and not body['actions'] and not body['bubble']
    assert body['evidence']['reply_source']=='unsupported_rule'
    assert len(calls)==1
    assert client.get('/api/conversation/messages').json()[-1]['text']==body['text']


def test_invalid_intent_output_uses_fixed_chat_fallback(client,monkeypatch):
    class InvalidIntentModel:
        def invoke(self,_):return {'intent':'transfer','amount':-1}
    monkeypatch.setattr(agent,'intent_client',lambda:InvalidIntentModel())
    response=client.post('/api/chat',json={'message':'帮我看看这个问题'})
    assert response.status_code==200,response.text
    body=response.json()
    assert body['text']=='抱歉，知财暂不支持这类请求。'
    assert body['response_mode']=='direct' and not body['actions']
    assert client.get('/api/conversation/messages').json()[-1]['text']==body['text']
    assert client.get('/api/account-state').json()['balance']==12000


def test_today_transfer_is_not_a_recurring_task_even_if_model_mislabels(client,monkeypatch):
    class Model:
        def invoke(self,_):return Intent(intent='task',task_type='fixed_expense',payee='妈妈',frequency='monthly',execution_day=1,amount=3000)
    def forbidden(*args,**kwargs):raise AssertionError('Immediate transfer copy must not use the prose model')
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    monkeypatch.setattr(agent,'generate',forbidden)
    body=client.post('/api/chat',json={'message':'今天给妈妈转钱'}).json()
    assert body['text']=='这次给妈妈转多少钱？'
    assert not body['actions']
    assert body['evidence']['reply_source']=='transfer_rule'
    draft=body['evidence']['operation_draft']
    assert draft['intent']=='transfer' and draft['payee']=='妈妈'
    assert 'amount' not in draft and 'frequency' not in draft and 'execution_day' not in draft


def test_instant_transfer_amount_followup_keeps_payee_not_task_fields(client,monkeypatch):
    class Model:
        count=0
        def invoke(self,_):
            self.count+=1
            return Intent(intent='transfer',payee='妈妈') if self.count==1 else Intent(intent='transfer',amount=500,continues_transfer=True)
    model=Model()
    monkeypatch.setattr(agent,'intent_client',lambda:model)
    first=client.post('/api/chat',json={'message':'今天给妈妈转钱'}).json()
    assert '转多少钱' in first['text']
    body=client.post('/api/chat',json={'message':'500元'}).json()
    assert body['response_mode']=='open_action'
    action=body['actions'][0]
    assert action['modal']=='transfer' and action['payee']=='妈妈' and action['amount']==500
    assert action['taskType'] is None and action['frequency'] is None and action['day'] is None
    assert '定时任务' not in body['text']
    state=client.get('/api/account-state').json()
    assert state['balance']==12000 and not state['tasks']


def test_new_today_transfer_does_not_inherit_pending_task_amount(client,monkeypatch):
    class Model:
        count=0
        def invoke(self,_):
            self.count+=1
            return (Intent(intent='task',task_type='fixed_expense',payee='房东',amount=3000,frequency='monthly') if self.count==1 else
                    Intent(intent='task',task_type='fixed_expense',payee='妈妈',amount=3000,frequency='monthly',execution_day=5,continues_task=True))
    model=Model();monkeypatch.setattr(agent,'intent_client',lambda:model)
    client.post('/api/chat',json={'message':'每月给房东转3000元'})
    body=client.post('/api/chat',json={'message':'今天给妈妈转钱'}).json()
    assert body['text']=='这次给妈妈转多少钱？' and not body['actions']
    assert body['evidence']['operation_draft']['intent']=='transfer'


@pytest.mark.parametrize('message',['经常帮我给妈妈转钱','总是帮我给妈妈转钱'])
def test_vague_repeat_words_do_not_invent_a_monthly_cycle(client,monkeypatch,message):
    class Model:
        def invoke(self,_):return Intent(intent='task',task_type='fixed_expense',payee='妈妈',amount=500,frequency='monthly',execution_day=5)
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    body=client.post('/api/chat',json={'message':message}).json()
    assert body['evidence']['reply_source']=='task_rule' and not body['actions']
    assert '执行周期' in body['text'] and '执行日' in body['text']
    assert 'frequency' not in body['evidence']['task_draft']


def test_negated_repeat_stays_a_single_transfer(client,monkeypatch):
    class Model:
        def invoke(self,_):return Intent(intent='task',task_type='fixed_expense',payee='妈妈',amount=500,frequency='monthly',execution_day=5)
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    body=client.post('/api/chat',json={'message':'今天给妈妈转500元，不要每月转，只转这一次'}).json()
    assert body['actions'][0]['modal']=='transfer' and body['actions'][0]['amount']==500
    assert body['evidence']['reply_source']=='transfer_rule'


def test_historical_frequency_is_not_itself_a_task_authorization(client,monkeypatch):
    from backend.app.agent.operation_routing import route_operation
    intent=Intent(intent='transfer',payee='妈妈',amount=500)
    result=route_operation(intent,'我以前每月都转，但这次先给妈妈转500元')
    assert result.intent=='transfer' and result.frequency is None


def test_payee_followup_does_not_discard_pending_recurring_task():
    from backend.app.agent.operation_routing import route_operation
    from backend.app.agent.task_dialogue import merge_task_draft
    previous=Intent(intent='task',task_type='fixed_expense',amount=500,frequency='monthly',execution_day=5,recurrence_evidence='每月')
    current=Intent(intent='task',payee='妈妈',continues_task=True)
    result=merge_task_draft(route_operation(current,'给妈妈转',previous),previous)
    assert result.intent=='task' and result.payee=='妈妈'
    assert result.amount==500 and result.frequency=='monthly' and result.execution_day==5


def test_product_and_investment_limit_query_keeps_both_actions(client,monkeypatch):
    class Model:
        def invoke(self,_):return Intent(intent='products',risk=2,include_forecast=True)
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    response=client.post('/api/chat',json={'message':'我想买R2理财，最多能投多少？'})
    assert response.status_code==200,response.text
    body=response.json()
    actions=body['actions']
    assert [(a['label'],a['path']) for a in actions]==[('查看理财产品','/wealth'),('查看现金流预测','/me')]
    assert actions[0]['risk']==2 and actions[0]['mode']=='products'
    assert actions[0]['bubbleSegments'] and actions[0]['resultIds']
    assert actions[1]['modal'] is None
    assert body['evidence']['reply_source']=='llm_grounded'
    result=body['evidence']['result']
    assert all(product['risk']==2 for product in result['products'])
    forecast_result=client.get('/api/finance/overview').json()
    assert result['cashflow']['cap']==forecast_result['cashflow']['cap']
    saved=client.get('/api/conversation/messages').json()[-1]
    assert [a['label'] for a in saved['actions']]==['查看理财产品','查看现金流预测']
    state=client.get('/api/account-state').json()
    assert state['balance']==12000 and not state['tasks']


@pytest.mark.parametrize('kind,label',[('products','查看理财产品'),('forecast','查看现金流预测')])
def test_single_product_or_forecast_query_has_only_its_own_action(client,monkeypatch,kind,label):
    class Model:
        def invoke(self,_):return Intent(intent=kind,risk=2)
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    body=client.post('/api/chat',json={'message':'有哪些R2理财？' if kind=='products' else '查看现金流预测'}).json()
    assert [a['label'] for a in body['actions']]==[label]


def test_monthly_insights_compute_shares_and_personal_change():
    from backend.app.services.bills import monthly_insights
    rows=[{'id':str(i),'category':category,'amount':amount,'merchant':'虚构商户','date':'2026-01-05'}
          for i,(category,amount) in enumerate([('餐饮',2400),('购物',1440),('出行',840),('生活用品',720),('其他',600)])]
    previous=[{**rows[0],'date':'2025-12-05','amount':5000}]
    result=monthly_insights(rows,previous,'2025-12')
    assert result['categories_ranked'][0]=={'name':'餐饮','amount':2400,'count':1,'share_pct':40}
    assert result['top_two_share_pct']==64 and result['average']==1200
    assert result['month_change']==1000 and result['month_change_pct']==20
    assert result['previous_month']=='2025-12' and not result['peer_comparison_available']
    rounding_rows=[{**rows[0],'amount':amount} for amount in (800,800,800,1440,840,720,600)]
    rounded=monthly_insights(rounding_rows,[],'2025-12')
    assert rounded['average']==857.14 and rounded['average_whole_yuan']==857


def test_bill_bubbles_keep_analysis_on_model_failure_and_persist(client,monkeypatch):
    from backend.app.agent import narrative
    with closing(db.connect_db()) as c,c:
        c.execute('DELETE FROM bill_transactions')
        c.executemany('INSERT INTO bill_transactions(id,month,transaction_date,category,counterparty,amount,direction) VALUES (?,?,?,?,?,?,?)',[
            ('p','2025-12','2025-12-05','餐饮','虚构餐馆',5000,'支出'),
            ('a','2026-01','2026-01-05','餐饮','虚构餐馆',2000,'支出'),
            ('b','2026-01','2026-01-06','餐饮','虚构餐馆',2000,'支出'),
            ('c','2026-01','2026-01-07','餐饮','虚构餐馆',1400,'支出'),
            ('d','2026-01','2026-01-08','购物','虚构商店',600,'支出')])
    class Model:
        def invoke(self,_):return Intent(intent='bills',month='2026-01')
    class BrokenWriter:
        def invoke(self,_):raise RuntimeError('synthetic failure')
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    monkeypatch.setattr(narrative,'narrative_client',lambda:BrokenWriter())
    response=client.post('/api/chat',json={'message':'分析2026年1月账单'})
    assert response.status_code==200,response.text
    body=response.json()
    segments=body['actions'][0]['bubbleSegments']
    assert len(segments)==4
    assert '餐饮' in segments[0] and '90.00%' in segments[0] and '平均每笔' in segments[0]
    assert '高频商户' in segments[1] and '3笔' in segments[1]
    assert '2025-12' in segments[2] and '20.00%' in segments[2] and '下月预算' in segments[2]
    assert segments[-1]=='点击闪烁可以查看消费明细。'
    assert not any(word in ''.join(segments) for word in ('超过90%的人','同龄人','全国平均'))
    assert body['evidence']['reply_source']=='evidence_fallback'
    assert body['bubble']['segments']==segments
    saved=client.get('/api/conversation/messages').json()[-1]
    assert saved['actions'][0]['bubbleSegments']==segments


def test_bill_insights_without_previous_or_current_records():
    from backend.app.services.bills import monthly_insights,frequent_consumption
    from backend.app.agent.bill_dialogue import bill_bubble_segments
    rows=[{'id':'a','category':'餐饮','amount':20,'merchant':'虚构餐馆','date':'2026-09-05'}]
    def segments(items):
        return bill_bubble_segments({'month':'2026-09','insights':monthly_insights(items,[],'2026-08'),
                                     'frequency_detail':frequent_consumption(items)})
    assert '还没有上月账单' in segments(rows)[2]
    assert '只有这一类' in segments(rows)[0]
    assert '没有消费记录' in segments([])[0]


@pytest.mark.parametrize('writer_fails',[False,True])
def test_limit_why_followup_requeries_forecast_and_keeps_action(client,monkeypatch,writer_fails):
    from backend.app.agent import narrative
    calls=[]
    class Model:
        def invoke(self,messages):
            calls.append(messages)
            return Intent(intent='products',risk=2,include_forecast=True) if len(calls)==1 else Intent(intent='explain')
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    product_tool=agent.TOOLS['products']
    forecast_calls=[]
    def fixed_forecast(c,_,**kwargs):
        forecast_calls.append(True)
        return {'minimum':9400,'reserve':2000,'cap':7400,'points':[]}
    def products(c,risk,account_id=None):
        return {**product_tool(c,risk,account_id=account_id),'cashflow':fixed_forecast(c,risk,account_id=account_id)}
    monkeypatch.setitem(agent.TOOLS,'products',products)
    monkeypatch.setitem(agent.TOOLS,'forecast',fixed_forecast)
    client.post('/api/chat',json={'message':'我想买R2理财，最多能投多少？'})
    if writer_fails:
        class Broken:
            def invoke(self,_):raise RuntimeError('synthetic failure')
        monkeypatch.setattr(narrative,'narrative_client',lambda:Broken())
    response=client.post('/api/chat',json={'message':'为什么是7400？'})
    assert response.status_code==200,response.text
    body=response.json()
    assert all(amount in body['text'] for amount in ('7400','9400','2000')), body['text']
    assert '扣除' in body['text'] and '预留' in body['text']
    assert body['evidence']['topic']=='investment_limit_explanation'
    assert body['evidence']['previous_basis']['cap']==7400
    assert body['evidence']['result']['cap']==7400
    assert body['evidence']['reply_source']==('evidence_fallback' if writer_fails else 'llm_grounded')
    assert [a['label'] for a in body['actions']]==['查看现金流预测']
    assert body['actions'][0]['path']=='/me' and body['actions'][0]['modal'] is None
    assert len(forecast_calls)==2 and '最近投入上限计算上下文' in str(calls[1][1:])
    assert client.get('/api/conversation/messages').json()[-1]['actions'][0]['label']=='查看现金流预测'
    assert client.get('/api/account-state').json()['balance']==12000


def test_limit_followup_preserves_old_basis_when_state_changes(client,monkeypatch):
    count=[]
    class Model:
        def invoke(self,_):
            count.append(True)
            return Intent(intent='products',risk=2) if len(count)==1 else Intent(intent='forecast',forecast_focus='investment_limit')
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    client.post('/api/chat',json={'message':'想买R2，最多能投多少'})
    with closing(db.connect_db()) as c:
        from backend.app.agent.forecast_dialogue import recent_forecast_context
        old=recent_forecast_context(c,'为什么')['basis']
    operation(client,{'kind':'transfer','amount':100,'payee':'虚构收款方'})
    body=client.post('/api/chat',json={'message':f"为什么是{old['cap']}？"}).json()
    assert body['evidence']['previous_basis']['cap']==old['cap']
    assert body['evidence']['result']['cap']==old['cap']-100
    assert '已更新为' in body['text'] and '上次' in body['text']
    assert [a['label'] for a in body['actions']]==['查看现金流预测']


def test_general_concept_and_unrelated_queries_do_not_inherit_limit_context(client,monkeypatch):
    from backend.app.agent.forecast_dialogue import route_forecast_followup
    context={'basis':{'minimum':9400,'reserve':2000,'cap':7400,'days':30},'quoted_amount_matched':False,'direct_previous':True}
    for message,kind in [('为什么需要应急金？','explain'),('为什么餐饮这么多？','bills')]:
        assert route_forecast_followup(Intent(intent=kind),message,context).intent==kind
    assert route_forecast_followup(Intent(intent='explain'),'怎么算的？',context).intent=='forecast'
    assert route_forecast_followup(Intent(intent='explain'),'怎么算的？',None).intent=='explain'
