"""Conversation state, source links, retrieval and isolation; all data is temporary."""
import json
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from langchain_core.messages.utils import count_tokens_approximately
from backend.app.agent import chat as agent
from backend.app.agent.schemas import Intent
from backend.app.repositories import legacy as db
from backend.tests.helpers import new_session, topics, sequence


def test_sessions_isolate_history_topics_audit_and_references(client,monkeypatch):
    a,b=new_session(client),new_session(client)
    calls=sequence(monkeypatch,[Intent(intent='bills',month='2026-09'),Intent(intent='bills',month='2026-10')])
    first=client.post('/api/chat',headers=a,json={'message':'只在甲会话分析9月'}).json()
    client.post('/api/chat',headers=b,json={'message':'只在乙会话分析10月'})
    assert '只在甲会话' not in str(calls[1])
    assert topics(client,a)[0]['slots']['month']=='2026-09'
    assert topics(client,b)[0]['slots']['month']=='2026-10'
    assert len(client.get('/api/conversation/messages',headers=a).json())==2
    assert len(client.get('/api/agent/audit',headers=b).json())==1
    assert client.get('/api/conversation/messages').json()==[]
    assert client.post('/api/chat',headers=b,json={'message':'继续','reply_to_message_id':first['id']}).status_code==422
    assert client.post('/api/conversation/messages',headers=b,json={'id':'cross-bubble','role':'agent','text':'不应保存','parent_message_id':first['id']}).status_code==422
    sequence(monkeypatch,[Intent(intent='bills',context_topic_id=topics(client,a)[0]['topic_id'],context_mode='resume')])
    body=client.post('/api/chat',headers=b,json={'message':'继续之前账单'}).json()
    assert body['evidence']['reply_source']=='context_rule' and not body['actions']
    assert len(topics(client,b))==1


def test_legacy_history_is_claimed_once_and_migration_is_repeatable(client,monkeypatch):
    sequence(monkeypatch,[Intent(intent='task',task_type='fixed_expense',frequency='monthly',payee='旧房东')])
    response=client.post('/api/chat',json={'message':'每月给旧房东转房租'}).json()
    a,b=new_session(client,True),new_session(client,True)
    assert client.get('/api/conversation/messages',headers=a).json()[-1]['id']==response['id']
    assert client.get('/api/conversation/messages',headers=b).json()==[]
    assert topics(client,a)[0]['slots']['payee']=='旧房东'
    from backend.app.services.conversations import create_context_schema
    with closing(db.connect_db()) as c,c:
        create_context_schema(c)
        create_context_schema(c)
    assert len(client.get('/api/conversation/messages',headers=a).json())==2


def test_pending_task_resumes_after_products_without_new_authorization(client,monkeypatch):
    h=new_session(client)
    sequence(monkeypatch,[Intent(intent='task',task_type='fixed_expense',name='房租',frequency='monthly',execution_day=5,payee='房东'),Intent(intent='products',risk=2)])
    client.post('/api/chat',headers=h,json={'message':'每月5号给房东交房租'})
    original=topics(client,h)[0]['topic_id']
    client.post('/api/chat',headers=h,json={'message':'先看看R2理财'})
    sequence(monkeypatch,[Intent(intent='task',amount=3000,context_mode='resume',context_topic_id=original)])
    body=client.post('/api/chat',headers=h,json={'message':'回到刚才房租，每次3000元'}).json()
    assert body['text']=='请核对定时任务信息，授权后开始执行。'
    action=body['actions'][0]
    assert (action['payee'],action['amount'],action['day'],action['frequency'])==('房东',3000,5,'monthly')
    assert len(topics(client,h))==2
    assert topics(client,h)[0]['topic_id']==original
    assert not client.get('/api/account-state').json()['tasks']


def test_pending_instant_transfer_resumes_after_bill_topic(client,monkeypatch):
    h=new_session(client)
    sequence(monkeypatch,[Intent(intent='transfer',payee='妈妈'),Intent(intent='bills',month='2026-09')])
    client.post('/api/chat',headers=h,json={'message':'今天给妈妈转钱'})
    original=topics(client,h)[0]['topic_id']
    client.post('/api/chat',headers=h,json={'message':'先看9月账单'})
    sequence(monkeypatch,[Intent(intent='transfer',amount=500,context_mode='resume',context_topic_id=original)])
    body=client.post('/api/chat',headers=h,json={'message':'回到妈妈那笔，500元'}).json()
    action=body['actions'][0]
    assert action['modal']=='transfer' and action['payee']=='妈妈' and action['amount']==500
    assert action['taskType'] is None and action['day'] is None
    assert client.get('/api/account-state').json()['balance']==12000


def test_ambiguous_paused_drafts_require_selection(client,monkeypatch):
    h=new_session(client)
    sequence(monkeypatch,[Intent(intent='task',task_type='fixed_expense',payee='房东甲',frequency='monthly',context_mode='new'),
                         Intent(intent='task',task_type='fixed_expense',payee='房东乙',frequency='monthly',context_mode='new'),
                         Intent(intent='products',risk=2),Intent(intent='task',amount=500,continues_task=True)])
    for message in ['每月给房东甲转房租','新建每月给房东乙转钱','先看R2','500元']:
        body=client.post('/api/chat',headers=h,json={'message':message}).json()
    assert body['evidence']['reply_source']=='context_rule' and not body['actions']
    assert '房东甲' in body['text'] and '房东乙' in body['text']
    assert len([t for t in topics(client,h) if t['status']=='needs_details'])==2


def test_confirmed_operation_draft_is_not_resumed(client,monkeypatch):
    h=new_session(client)
    sequence(monkeypatch,[Intent(intent='task',task_type='fixed_expense',payee='房东',amount=100,frequency='monthly',execution_day=5)])
    body=client.post('/api/chat',headers=h,json={'message':'每月5号给房东转100元'}).json()
    original=topics(client,h)[0]['topic_id']
    preview=client.post('/api/operations/preview',headers={**h,'X-Context-Message-Id':body['id']},json={
        'kind':'task','task_type':'fixed_expense','payee':'房东','amount':100,'frequency':'monthly','execution_day':5}).json()
    ch=client.post('/api/operations/'+preview['preview_id']+'/challenge',headers=h,json={'password':'test-password-123'})
    assert ch.status_code==200,ch.text
    assert client.post('/api/operations/'+preview['preview_id']+'/confirm',headers=h,json={'confirmed':True,'challenge_token':ch.json()['challenge_token']}).status_code==200
    assert topics(client,h)[0]['status']=='completed'
    sequence(monkeypatch,[Intent(intent='task',context_mode='resume',context_topic_id=original)])
    resumed=client.post('/api/chat',headers=h,json={'message':'继续刚才那笔任务'}).json()
    assert resumed['evidence']['reply_source']=='context_rule' and not resumed['actions']
    assert '已经确认完成' in resumed['text']
    assert len(client.get('/api/account-state').json()['tasks'])==1
    assert client.get('/api/agent/audit').json()==[]


def test_page_explanation_retains_source_and_focus(client,monkeypatch):
    h=new_session(client)
    sequence(monkeypatch,[Intent(intent='bills',month='2026-09'),Intent(intent='products',risk=2),Intent(intent='bills',category='餐饮',context_mode='continue')])
    first=client.post('/api/chat',headers=h,json={'message':'分析9月账单'}).json()
    old=topics(client,h)[0]['topic_id']
    client.post('/api/chat',headers=h,json={'message':'看R2产品'})
    forged=client.post('/api/conversation/messages',headers=h,json={'id':'forged-insight','role':'agent','text':'这里是餐饮占比的页面解读','parent_message_id':first['id']})
    assert forged.status_code==422
    segments=[segment for action in first['actions'] for segment in action.get('bubbleSegments',[])]
    assert segments
    bubble=client.post('/api/conversation/messages',headers=h,json={'id':'page-insight','role':'agent','text':segments[0],'parent_message_id':first['id']})
    assert bubble.status_code==200,bubble.text
    history=client.get('/api/conversation/messages',headers=h).json()
    assert history[-1]['message_kind']=='page_explanation' and history[-1]['topic_id']==old
    assert history[-1]['parent_message_id']==first['id']
    assert client.get('/api/conversation/context',headers=h).json()['active_topic_id']==old
    result=client.post('/api/chat',headers=h,json={'message':'继续拆解餐饮'}).json()
    assert result['evidence']['result']['month']=='2026-09'


def test_older_limit_reference_is_retrieved_beyond_recent_audit_window(client,monkeypatch):
    h=new_session(client)
    sequence(monkeypatch,[Intent(intent='products',risk=2,include_forecast=True),Intent(intent='explain')])
    first=client.post('/api/chat',headers=h,json={'message':'R2最多能投多少'}).json()
    cap=first['evidence']['result']['cashflow']['cap']
    cid=h['X-Conversation-Id']
    with closing(db.connect_db()) as c,c:
        for i in range(35):
            c.execute('INSERT INTO conversation_messages(message_id,role,text,conversation_id) VALUES (?,?,?,?)',(f'old-u{i}','user','另一个独立话题',cid))
            c.execute('INSERT INTO conversation_messages(message_id,role,text,conversation_id) VALUES (?,?,?,?)',(f'old-a{i}','agent','一般知识解释',cid))
            c.execute('INSERT INTO agent_audit(id,intent_json,tool,result_json,conversation_id) VALUES (?,?,?,?,?)',
                      (f'old-a{i}',Intent(intent='explain').model_dump_json(),'explain',json.dumps({'text':'一般知识解释','evidence':{'topic':'general_finance'}}),cid))
    result=client.post('/api/chat',headers=h,json={'message':f'之前为什么是{cap}？'}).json()
    assert result['evidence']['topic']=='investment_limit_explanation'
    assert result['evidence']['previous_basis']['cap']==cap
    assert result['actions'][0]['label']=='查看现金流预测'


def test_history_assembly_keeps_full_text_and_relevant_old_turn():
    from backend.app.agent.context import assemble_messages
    history=[{'role':'user','text':'早先问题','_relevant':True},{'role':'agent','text':'独有的历史依据','_relevant':True}]
    for i in range(20):
        history.extend([{'role':'user','text':'普通问题'+str(i)},{'role':'agent','text':'普通回答'+str(i)}])
    long='完整内容'*410
    history.extend([{'role':'user','text':'最新问题'},{'role':'agent','text':long}])
    messages=assemble_messages('规则',history,'继续追问',budget=1800)
    assert ('ai',long) in messages and ('ai','独有的历史依据') in messages
    assert count_tokens_approximately(messages,chars_per_token=1.6)<=1800


def test_same_conversation_serializes_turns_and_clear_resets_state(client,monkeypatch):
    h=new_session(client)
    sequence(monkeypatch,[Intent(intent='account'),Intent(intent='account')])
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda text:client.post('/api/chat',headers=h,json={'message':text}),['余额甲','余额乙']))
    assert all(r.status_code==200 for r in responses)
    assert len(client.get('/api/conversation/messages',headers=h).json())==4
    assert client.get('/api/conversation/context',headers=h).json()['revision']==2
    assert client.delete('/api/conversation/messages',headers=h).status_code==200
    assert topics(client,h)==[]
    assert client.get('/api/agent/audit',headers=h).json()==[]


def test_referenced_bill_evidence_is_historical_and_current_data_is_requeried(client,monkeypatch):
    h=new_session(client)
    sequence(monkeypatch,[Intent(intent='bills',month='2026-09')])
    first=client.post('/api/chat',headers=h,json={'message':'分析9月账单'}).json()
    old_total=first['evidence']['result']['total']
    with closing(db.connect_db()) as c,c:
        c.execute("DELETE FROM bill_transactions WHERE month='2026-09'")
    sequence(monkeypatch,[Intent(intent='bills',month='2026-09',context_mode='continue',context_message_id=first['id'])])
    body=client.post('/api/chat',headers=h,json={'message':'为什么刚才是这个支出结构？'}).json()
    assert body['evidence']['result']['total']==0
    old=body['evidence']['referenced_answer']
    assert old['scope']=='historical_snapshot' and old['evidence']['result']['total']==old_total


def test_operation_preview_cannot_be_confirmed_from_another_conversation(client):
    a,b=new_session(client),new_session(client)
    preview=client.post('/api/operations/preview',headers=a,json={'kind':'transfer','payee':'虚构收款人','amount':10}).json()
    assert client.post('/api/operations/'+preview['preview_id']+'/confirm',headers=b,json={'confirmed':True}).status_code==404
    assert client.get('/api/account-state').json()['balance']==12000


def test_original_schema_migrates_without_losing_messages_or_audit(tmp_path):
    import sqlite3
    from backend.app.services.conversations import create_context_schema,hydrate_legacy_context,create_conversation
    path=tmp_path/'legacy.sqlite3'
    with sqlite3.connect(path) as c:
        c.row_factory=sqlite3.Row
        c.executescript("""
        CREATE TABLE conversation_messages(sequence INTEGER PRIMARY KEY AUTOINCREMENT,message_id TEXT UNIQUE,role TEXT,text TEXT,action_json TEXT,actions_json TEXT);
        CREATE TABLE agent_audit(id TEXT PRIMARY KEY,intent_json TEXT,tool TEXT,result_json TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE operation_previews(id TEXT PRIMARY KEY,command_json TEXT,state_hash TEXT,expires_at TEXT,warning INTEGER,result_json TEXT);
        """)
        c.execute('INSERT INTO conversation_messages(message_id,role,text) VALUES (?,?,?)',('old-user','user','原来的9月问题'))
        c.execute('INSERT INTO conversation_messages(message_id,role,text) VALUES (?,?,?)',('old-agent','agent','原来的分析回答'))
        c.execute('INSERT INTO agent_audit(id,intent_json,tool,result_json) VALUES (?,?,?,?)',('old-agent',Intent(intent='bills',month='2026-09').model_dump_json(),'bills',json.dumps({'text':'原来的分析回答','evidence':{'result':{'month':'2026-09','total':20}}})))
        create_context_schema(c)
        hydrate_legacy_context(c,'default')
        cid=create_conversation(c,True)['conversation_id']
        create_context_schema(c)
        assert c.execute('SELECT COUNT(*) FROM conversation_messages').fetchone()[0]==2
        assert c.execute('SELECT text,conversation_id FROM conversation_messages WHERE message_id=?',('old-agent',)).fetchone()['text']=='原来的分析回答'
        assert c.execute('SELECT conversation_id FROM agent_audit WHERE id=?',('old-agent',)).fetchone()[0]==cid
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT COUNT(*) FROM conversation_topics WHERE conversation_id=?',(cid,)).fetchone()[0]==1


def test_clear_during_model_request_does_not_restore_deleted_context(client,monkeypatch):
    from threading import Event
    h=new_session(client)
    started,release=Event(),Event()
    class Model:
        def invoke(self,_):
            started.set()
            assert release.wait(5)
            return Intent(intent='account')
    monkeypatch.setattr(agent,'intent_client',lambda:Model())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(lambda:client.post('/api/chat',headers=h,json={'message':'等待中的余额查询'}))
        assert started.wait(5)
        client.delete('/api/conversation/messages',headers=h)
        release.set()
        assert future.result().status_code==409
    assert client.get('/api/conversation/messages',headers=h).json()==[]
    assert topics(client,h)==[]


def test_bare_reason_after_old_bill_bubble_does_not_switch_to_cashflow(client,monkeypatch):
    h=new_session(client)
    sequence(monkeypatch,[Intent(intent='bills',month='2026-09'),Intent(intent='products',risk=2)])
    bill=client.post('/api/chat',headers=h,json={'message':'分析9月账单'}).json()
    bill_topic=topics(client,h)[0]['topic_id']
    client.post('/api/chat',headers=h,json={'message':'再看看R2'})
    client.post('/api/conversation/messages',headers=h,json={'id':'focus-bill','role':'agent','text':'餐饮支出结构解读','parent_message_id':bill['id']})
    sequence(monkeypatch,[Intent(intent='explain',context_mode='continue',context_topic_id=bill_topic)])
    body=client.post('/api/chat',headers=h,json={'message':'为什么？'}).json()
    assert body['evidence']['referenced_answer']['tool']=='bills'
    assert 'investment_limit_explanation' not in str(body['evidence'].get('topic'))
    assert not any(a['label']=='查看现金流预测' for a in body['actions'])
    retained=next(t for t in topics(client,h) if t['topic_id']==bill_topic)
    assert retained['kind']=='bills' and retained['slots']['month']=='2026-09'
