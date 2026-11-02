"""Saved-recipient restrictions and transfer history use temporary demo accounts."""
import json
from contextlib import closing
from datetime import date
from backend.app.repositories import legacy as db
from backend.app.agent import chat as agent
from backend.app.agent.schemas import Intent
from backend.tests.helpers import operation, add


def test_payee_management_masks_accounts_and_rejects_duplicates(client):
    payee=add(client)
    assert payee['account_last4']=='2789' and payee['first_transfer']
    assert 'account_number' not in payee
    assert '0000000000002789' not in client.get('/api/payees').text
    full=client.get('/api/payees/'+payee['id']).json()
    assert full['account_masked']=='**** 2789' and 'account_number' not in full
    unchanged=client.put('/api/payees/'+payee['id'],json={k:full[k] for k in ('nickname','holder_name','bank_name','version')})
    assert unchanged.status_code==200 and unchanged.json()['account_masked']=='**** 2789'
    duplicate=client.post('/api/payees',json={'nickname':'另一个名字','holder_name':full['holder_name'],'bank_name':full['bank_name'],'account_number':'0000000000002789'})
    assert duplicate.status_code==409
    invalid=client.post('/api/payees',json={'holder_name':'测试','bank_name':'演示银行','account_number':'bad-card'})
    assert invalid.status_code==422


def test_unknown_and_ambiguous_recipient_cannot_bypass_preview(client):
    unknown=client.post('/api/operations/preview',json={'kind':'transfer','payee':'陌生人','amount':100})
    assert unknown.status_code==409 and '先添加' in unknown.text
    add(client,'同名收款人','0000000000002001')
    add(client,'同名收款人','0000000000002002')
    assert client.post('/api/operations/preview',json={'kind':'transfer','payee':'同名收款人','amount':100}).status_code==409
    assert client.get('/api/account-state').json()['balance']==12000


def test_transfer_confirmation_records_once_and_updates_recent_people(client):
    payee=add(client)
    preview=client.post('/api/operations/preview',json={'kind':'transfer','payee_id':payee['id'],'payee_version':payee['version'],'amount':123.45}).json()
    assert preview['recipient']['account_last4']=='2789'
    assert client.get('/api/transfers').json()==[]
    url='/api/operations/'+preview['preview_id']+'/confirm'
    first=client.post(url,json={'confirmed':True})
    assert first.status_code==200,first.text
    assert client.post(url,json={'confirmed':True}).json()==first.json()
    records=client.get('/api/transfers').json()
    assert len(records)==1 and records[0]['amount']==123.45 and records[0]['display_name']=='阿姨'
    assert records[0]['account_masked']=='**** 2789' and records[0]['source']=='instant'
    assert client.get('/api/transfers/recent').json()[0]['payee']['id']==payee['id']
    assert not client.get('/api/payees/'+payee['id']).json()['first_transfer']
    assert client.get('/api/account-state').json()['balance']==11876.55


def test_one_time_bank_transfer_keeps_recipient_out_of_saved_payees(client):
    number='0000000000002789'
    existing=client.get('/api/payees').json()
    command={'kind':'transfer','amount':123.45,'manual_recipient':{
        'holder_name':'演示收款人','bank_name':'演示银行','account_number':number}}
    preview=client.post('/api/operations/preview',json=command)
    assert preview.status_code==200,preview.text
    assert number not in preview.text
    assert preview.json()['recipient']['account_masked']=='**** 2789'
    assert client.get('/api/payees').json()==existing
    url='/api/operations/'+preview.json()['preview_id']+'/confirm'
    ch=client.post('/api/operations/'+preview.json()['preview_id']+'/challenge',json={'password':'test-password-123'})
    assert ch.status_code==200,ch.text
    confirmed=client.post(url,json={'confirmed':True,'challenge_token':ch.json()['challenge_token']})
    assert confirmed.status_code==200,confirmed.text
    assert client.post(url,json={'confirmed':True}).json()==confirmed.json()
    assert client.get('/api/payees').json()==existing
    records=client.get('/api/transfers').json()
    assert len(records)==1 and records[0]['payee_id'] is None
    assert records[0]['holder_name']=='演示收款人' and records[0]['account_masked']=='**** 2789'
    assert client.get('/api/account-state').json()['balance']==11876.55
    with closing(db.connect_db()) as c:
        stored=c.execute('SELECT command_json FROM operation_previews WHERE id=?',(preview.json()['preview_id'],)).fetchone()[0]
        audit=c.execute("SELECT intent_json FROM agent_audit WHERE tool='command:transfer' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    assert number not in stored and number not in audit
    assert client.post('/api/operations/preview',json={**command,'kind':'task','task_type':'fixed_expense','execution_day':5}).status_code==422


def test_changed_or_removed_recipient_invalidates_pending_preview_and_preserves_history(client):
    payee=add(client)
    operation(client,{'kind':'transfer','payee_id':payee['id'],'amount':10})
    preview=client.post('/api/operations/preview',json={'kind':'transfer','payee_id':payee['id'],'payee_version':1,'amount':100}).json()
    full=client.get('/api/payees/'+payee['id']).json()
    edited=client.put('/api/payees/'+payee['id'],json={k:full[k] for k in ('nickname','holder_name','bank_name','version')}|{'account_number':'0000000000002790'}).json()
    assert edited['version']==2
    rejected=client.post('/api/operations/'+preview['preview_id']+'/confirm',json={'confirmed':True})
    assert rejected.status_code==409 and client.get('/api/account-state').json()['balance']==11990
    assert client.get('/api/transfers').json()[0]['account_masked']=='**** 2789'
    assert client.get('/api/transfers/recent').json()[0]['account_changed']
    preview=client.post('/api/operations/preview',json={'kind':'transfer','payee_id':payee['id'],'payee_version':2,'amount':100}).json()
    assert client.delete('/api/payees/'+payee['id']).status_code==200
    assert client.post('/api/operations/'+preview['preview_id']+'/confirm',json={'confirmed':True}).status_code==409
    recent=client.get('/api/transfers/recent').json()[0]
    assert recent['payee'] is None and recent['account_masked']=='**** 2789'


def test_chat_unknown_account_offers_add_and_duplicate_names_offer_choices(client,monkeypatch):
    class Model:
        def invoke(self,_):return Intent(intent='transfer',payee='陌生人',amount=50)
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    body=client.post('/api/chat',json={'message':'给陌生人转50元'}).json()
    assert '先添加' in body['text'] and body['actions'][0]['modal']=='payee'
    assert body['actions'][0]['path']=='/transfer/payees'
    assert client.get('/api/account-state').json()['balance']==12000
    a,b=add(client,'陌生人','0000000000002001'),add(client,'陌生人','0000000000002002')
    body=client.post('/api/chat',json={'message':'给陌生人转50元'}).json()
    assert '多个收款账户' in body['text']
    assert {action['payeeId'] for action in body['actions']}=={a['id'],b['id']}


def test_chat_nickname_without_account_requires_saved_recipient(client,monkeypatch):
    class Model:
        def __init__(self,name):self.name=name
        def invoke(self,messages):
            return Intent(intent='transfer',payee=self.name,amount=200)
    for name in ('姐姐','小萌'):
        monkeypatch.setattr(agent,'intent_client',lambda:Model(name))
        body=client.post('/api/chat',json={'message':f'给{name}转账200元'}).json()
        assert body['text']==f'还没保存“{name}”的收款账户。请先添加并核对账户信息。'
        assert body['actions'][0]['modal']=='payee'


def test_chat_explicit_name_and_full_account_offers_one_time_confirmation(client,monkeypatch):
    number='0000000000001004'
    for saved in client.get('/api/payees').json():
        if saved['account_last4']=='1004':
            assert client.delete('/api/payees/'+saved['id']).status_code==200
    class Model:
        def invoke(self,_):raise AssertionError('完整账号即时转账不应调用意图模型')
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    response=client.post('/api/chat',json={'message':f'向李小萌账号{number}转账200元'})
    assert response.status_code==200,response.text
    body=response.json()
    assert body['text']=='请核对本次转账信息，确认后立即执行。'
    assert body['actions'][0]['manualRecipient']=={'holder_name':'李小萌','account_number':number}
    assert body['actions'][0]['amount']==200
    assert client.get('/api/account-state').json()['balance']==12000


def test_unsaved_account_demo_prompt_asks_for_holder_name(client,monkeypatch):
    class Model:
        def invoke(self,_):raise AssertionError('完整账号即时转账不应调用意图模型')
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    body=client.post('/api/chat',json={'message':'给0000000000001234转2000元'}).json()
    assert body['text']=='请告诉我收款人开户姓名，我会生成一次性转账确认信息供你核对。'
    assert body['actions']==[]
    assert client.get('/api/account-state').json()['balance']==12000


def test_unsaved_account_without_amount_continues_one_time_transfer(client,monkeypatch):
    number='0000000000001234'
    class Model:
        def invoke(self,_):raise AssertionError('完整账号转账及其简单追问不应调用意图模型')
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    first=client.post('/api/chat',json={'message':f'给{number}转钱'}).json()
    assert first['text']=='请告诉我收款人开户姓名和本次转账金额，我会生成一次性转账确认信息供你核对。'
    assert first['actions']==[]
    second=client.post('/api/chat',json={'message':'李小萌'}).json()
    assert second['text']=='请告诉我本次转账金额，我会生成一次性转账确认信息供你核对。'
    third=client.post('/api/chat',json={'message':'200元'}).json()
    assert third['text']=='请核对本次转账信息，确认后立即执行。'
    assert third['actions'][0]['manualRecipient']=={'holder_name':'李小萌','account_number':number}
    assert third['actions'][0]['amount']==200
    assert not any(payee['account_last4']=='1234' for payee in client.get('/api/payees').json())
    assert client.get('/api/account-state').json()['balance']==12000


def test_labeled_eight_digit_account_and_holder_name_confirm_directly(client,monkeypatch):
    class Model:
        def invoke(self,_):return Intent(intent='unknown')
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    body=client.post('/api/chat',json={'message':'向收款人姓名李小萌账号12345678转账200元'}).json()
    assert body['text']=='请核对本次转账信息，确认后立即执行。'
    assert body['actions'][0]['manualRecipient']=={'holder_name':'李小萌','account_number':'12345678'}


def test_unknown_full_account_asks_for_missing_name_then_confirms(client,monkeypatch):
    number='0000000000009876'
    replies=iter([Intent(intent='transfer',payee='尾号9876',amount=200),
                  Intent(intent='transfer',payee='李小萌',continues_transfer=True)])
    class Model:
        def invoke(self,_):return next(replies)
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    first=client.post('/api/chat',json={'message':f'向账号{number}转账200元'}).json()
    assert '收款人开户姓名' in first['text'] and first['actions']==[]
    second=client.post('/api/chat',json={'message':'李小萌'}).json()
    assert second['text']=='请核对本次转账信息，确认后立即执行。'
    assert second['actions'][0]['manualRecipient']=={'holder_name':'李小萌','account_number':number}
    assert second['actions'][0]['amount']==200


def test_unknown_full_account_waits_for_name_and_amount_across_turns(client,monkeypatch):
    number='0000000000009877'
    replies=iter([Intent(intent='transfer',payee='尾号9877'),
                  Intent(intent='unknown'),Intent(intent='unknown')])
    class Model:
        def invoke(self,_):return next(replies)
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    first=client.post('/api/chat',json={'message':f'向账号{number}转账'}).json()
    assert '收款人开户姓名' in first['text'] and '金额' in first['text']
    second=client.post('/api/chat',json={'message':'李小萌'}).json()
    assert '金额' in second['text'] and not second['actions']
    third=client.post('/api/chat',json={'message':'200元'}).json()
    assert third['text']=='请核对本次转账信息，确认后立即执行。'
    assert third['actions'][0]['manualRecipient']['account_number']==number
    assert third['actions'][0]['amount']==200


def test_full_account_input_is_resolved_locally_and_not_sent_to_model(client,monkeypatch):
    payee=add(client)
    class Model:
        def invoke(self,_):raise AssertionError('完整账号即时转账不应调用意图模型')
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    body=client.post('/api/chat',json={'message':'向0000 0000 0000 2789转50元'}).json()
    assert body['actions'][0]['payeeId']==payee['id']


def test_scheduled_transfer_uses_bound_account_and_account_edit_pauses_task(client):
    from backend.app.jobs.scheduler import run_due
    payee=add(client)
    response=operation(client,{'kind':'task','task_type':'fixed_expense','payee_id':payee['id'],'amount':100,'frequency':'monthly','execution_day':5})
    assert response.status_code==200,response.text
    task=response.json()['state']['tasks'][0]
    assert task['payeeId']==payee['id'] and task['payeeVersion']==1
    with closing(db.connect_db()) as c,c:
        c.execute("UPDATE scheduled_tasks SET authorized_at='2026-11-01'")
    run_due(date(2026,11,5))
    assert client.get('/api/transfers').json()[0]['source']=='task'
    full=client.get('/api/payees/'+payee['id']).json()
    client.put('/api/payees/'+payee['id'],json={k:full[k] for k in ('nickname','holder_name','bank_name','version')}|{'account_number':'0000000000002790'})
    assert client.get('/api/account-state').json()['tasks'][0]['status']=='已暂停'
    balance=client.get('/api/account-state').json()['balance']
    run_due(date(2026,12,5))
    assert client.get('/api/account-state').json()['balance']==balance


def test_legacy_history_migration_does_not_invent_payees_or_accounts(client):
    from backend.app.services.payees import create_payee_schema
    with closing(db.connect_db()) as c,c:
        c.execute("INSERT INTO account_transactions(id,account_id,transaction_type,direction,amount,balance_after,description,occurred_at) VALUES ('legacy-t','demo-account','transfer','out',20,12000,'历史转账','2026-10-01T12:00:00')")
        create_payee_schema(c)
        create_payee_schema(c)
    record=client.get('/api/transfers').json()[0]
    assert record['payee_id'] is None and record['account_masked'] is None
    assert len(client.get('/api/transfers').json())==1


def test_holder_name_without_nickname_remains_valid_at_confirmation(client):
    holder='演示收款姓名'*6
    result=client.post('/api/payees',json={'holder_name':holder,'bank_name':'演示银行','account_number':'0000000000004040'}).json()
    response=operation(client,{'kind':'transfer','payee_id':result['id'],'amount':1})
    assert response.status_code==200,response.text
    assert client.get('/api/transfers').json()[0]['display_name']==holder


def test_multiple_accounts_in_one_message_require_explicit_selection(client,monkeypatch):
    class Model:
        def invoke(self,_):return Intent(intent='transfer',payee='妈妈',amount=50)
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    body=client.post('/api/chat',json={'message':'从0000000000002222给0000000000001001转50元'}).json()
    assert '多个账号' in body['text']
    assert body['actions'][0]['path']=='/transfer/payees' and body['actions'][0]['modal'] is None
    assert client.get('/api/account-state').json()['balance']==12000
