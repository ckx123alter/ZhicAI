"""Durable conversation state and additive migration; account identity stays server-owned."""
import json
import re
import threading
import uuid
from contextlib import contextmanager
from fastapi import HTTPException

DEFAULT_CONVERSATION = 'default'
_locks = {}
_guard = threading.Lock()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def create_context_schema(c):
    for table, columns in {
        'conversation_messages': {'conversation_id': "TEXT NOT NULL DEFAULT 'default'", 'topic_id': 'TEXT',
                                  'parent_message_id': 'TEXT', 'message_kind': "TEXT NOT NULL DEFAULT 'dialogue'"},
        'agent_audit': {'conversation_id': "TEXT NOT NULL DEFAULT 'default'", 'topic_id': 'TEXT'},
        'operation_previews': {'conversation_id': "TEXT NOT NULL DEFAULT 'default'", 'context_topic_id':'TEXT', 'context_message_id':'TEXT',
                               'owner_user_id':'TEXT', 'account_id':'TEXT', 'command_hash':'TEXT', 'manual_recipient_account':'TEXT'},
    }.items():
        existing = {row['name'] for row in c.execute(f'PRAGMA table_info({table})')}
        for name, declaration in columns.items():
            if name not in existing:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
    c.executescript("""
    CREATE TABLE IF NOT EXISTS conversations (
      id TEXT PRIMARY KEY, active_topic_id TEXT, summary_json TEXT NOT NULL DEFAULT '{}',
      revision INTEGER NOT NULL DEFAULT 0, hydrated INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS conversation_topics (
      id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, kind TEXT NOT NULL, label TEXT NOT NULL,
      state_json TEXT NOT NULL, status TEXT NOT NULL, missing_json TEXT NOT NULL DEFAULT '[]',
      evidence_message_id TEXT, last_sequence INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS ix_messages_conversation ON conversation_messages(conversation_id,sequence);
    CREATE INDEX IF NOT EXISTS ix_audit_conversation ON agent_audit(conversation_id);
    CREATE INDEX IF NOT EXISTS ix_topics_conversation ON conversation_topics(conversation_id,last_sequence);
    CREATE TABLE IF NOT EXISTS context_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
    INSERT OR IGNORE INTO context_metadata VALUES ('legacy_owner','');
    INSERT OR IGNORE INTO conversations(id) VALUES ('default');
    """)
    existing = {row['name'] for row in c.execute('PRAGMA table_info(conversations)')}
    if 'owner_user_id' not in existing:
        c.execute('ALTER TABLE conversations ADD COLUMN owner_user_id TEXT')
    topic_columns = {row['name'] for row in c.execute('PRAGMA table_info(conversation_topics)')}
    if 'plan_json' not in topic_columns:
        c.execute("ALTER TABLE conversation_topics ADD COLUMN plan_json TEXT NOT NULL DEFAULT '{}'")


def require_conversation(c, conversation_id, owner_user_id=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', conversation_id or ''):
        raise HTTPException(422, '会话标识格式不正确')
    row = c.execute('SELECT * FROM conversations WHERE id=?', (conversation_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '会话不存在，请重新打开页面')
    if owner_user_id is not None and row['owner_user_id'] not in (None, owner_user_id):
        raise HTTPException(404, '会话不存在，请重新打开页面')
    return dict(row)


def create_conversation(c, adopt_legacy=False, owner_user_id=None):
    cid = str(uuid.uuid4())
    c.execute('INSERT INTO conversations(id,owner_user_id) VALUES (?,?)', (cid,owner_user_id))
    if adopt_legacy:
        claimed = c.execute("UPDATE context_metadata SET value=? WHERE key='legacy_owner' AND value=''", (cid,)).rowcount
        if claimed:
            for table in ('conversation_messages', 'agent_audit', 'conversation_topics'):
                c.execute(f"UPDATE {table} SET conversation_id=? WHERE conversation_id='default'", (cid,))
            old = require_conversation(c, DEFAULT_CONVERSATION)
            c.execute('UPDATE conversations SET active_topic_id=?,summary_json=?,hydrated=? WHERE id=?',
                      (old['active_topic_id'], old['summary_json'], old['hydrated'], cid))
            c.execute("UPDATE conversations SET active_topic_id=NULL,summary_json='{}',revision=revision+1 WHERE id='default'")
    return {'conversation_id': cid}


@contextmanager
def conversation_lock(conversation_id):
    with _guard:
        lock = _locks.setdefault(conversation_id, threading.RLock())
    with lock:
        yield


def clear_context(c, conversation_id=None):
    if conversation_id:
        require_conversation(c, conversation_id)
        for table in ('conversation_topics', 'agent_audit'):
            c.execute(f'DELETE FROM {table} WHERE conversation_id=?', (conversation_id,))
        c.execute("UPDATE conversations SET active_topic_id=NULL,summary_json='{}',revision=revision+1,hydrated=1 WHERE id=?", (conversation_id,))
    else:
        c.execute('DELETE FROM conversation_topics')
        c.execute("UPDATE conversations SET active_topic_id=NULL,summary_json='{}',revision=revision+1,hydrated=1")


def read_topics(c, conversation_id):
    return [dict(row) for row in c.execute('SELECT * FROM conversation_topics WHERE conversation_id=? ORDER BY last_sequence DESC,rowid DESC', (conversation_id,))]


def topic_label(intent):
    kind = intent.intent
    if kind == 'bills':
        return f"{intent.month or ''}账单"
    if kind == 'task':
        return f"{intent.payee or intent.name or intent.product_id or '待补充'}定时任务"
    if kind == 'transfer':
        return f"给{intent.payee or '待补充收款方'}转账"
    if kind in ('products', 'forecast'):
        return f"R{intent.risk}理财与现金流" if intent.risk else '现金流与投入上限'
    return {'account': '账户余额', 'performance': '持仓收益', 'purchase': '申购草案',
            'redemption': '赎回草案', 'explain': '理财知识'}.get(kind, '功能咨询')


def save_topic(c, cid, intent, response, topic_id=None, sequence=0):
    row = c.execute('SELECT * FROM conversation_topics WHERE id=? AND conversation_id=?', (topic_id, cid)).fetchone() if topic_id else None
    topic_id = row['id'] if row else str(uuid.uuid4())
    evidence = response.evidence
    missing = evidence.get('missing_fields', [])
    status = 'needs_details' if missing else 'awaiting_confirmation' if intent.intent in ('task', 'transfer', 'purchase', 'redemption') and response.actions else 'answered'
    # Resuming a topic preserves its factual slots; authority is never stored here.
    state = intent.model_dump(mode='json', exclude_none=True)
    for field in ('context_topic_id', 'context_message_id', 'context_mode'):
        state.pop(field, None)
    c.execute("""INSERT INTO conversation_topics(id,conversation_id,kind,label,state_json,status,missing_json,evidence_message_id,last_sequence,plan_json)
      VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,label=excluded.label,
      state_json=excluded.state_json,status=excluded.status,missing_json=excluded.missing_json,
      evidence_message_id=excluded.evidence_message_id,last_sequence=excluded.last_sequence,
      plan_json=excluded.plan_json,updated_at=CURRENT_TIMESTAMP""",
      (topic_id, cid, intent.intent, topic_label(intent), dumps(state), status, dumps(missing), response.id, sequence,
       dumps(response.evidence.get('plan',{}))))
    c.execute('UPDATE conversations SET active_topic_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?', (topic_id, cid))
    c.execute('UPDATE agent_audit SET topic_id=? WHERE id=? AND conversation_id=?', (topic_id, response.id, cid))
    c.execute('UPDATE conversation_messages SET topic_id=? WHERE (message_id=? OR parent_message_id=?) AND conversation_id=?',
              (topic_id, response.id, response.id, cid))
    return topic_id


def update_summary(c, cid):
    topics = read_topics(c, cid)
    # Extractive structured summary: no extra model call and no replacement of source records.
    entries = [{'topic_id': t['id'], 'label': t['label'], 'kind': t['kind'], 'status': t['status'],
                'slots': json.loads(t['state_json']), 'missing_fields': json.loads(t['missing_json']),
                'plan': json.loads(t['plan_json']), 'evidence_message_id': t['evidence_message_id']} for t in topics[:24]]
    summary = {'format': 'structured_topic_summary', 'topics': entries, 'topic_count': len(topics)}
    c.execute('UPDATE conversations SET summary_json=? WHERE id=?', (dumps(summary), cid))
    return summary


def hydrate_legacy_context(c, cid):
    from ..agent.schemas import Intent, AgentResponse
    conversation = require_conversation(c, cid)
    if conversation['hydrated']:
        return
    rows = c.execute('SELECT * FROM agent_audit WHERE conversation_id=? ORDER BY rowid', (cid,)).fetchall()
    keys = {}
    for row in rows:
        try:
            intent = Intent.model_validate_json(row['intent_json'])
            response = AgentResponse.model_validate_json(row['result_json'])
            response.id = row['id']
        except (ValueError, TypeError):
            continue  # Non-chat operation audits are not conversation topics.
        key = (intent.intent, intent.month if intent.intent == 'bills' else intent.payee if intent.intent in ('task', 'transfer') else intent.risk)
        message = c.execute('SELECT sequence FROM conversation_messages WHERE message_id=? AND conversation_id=?', (row['id'], cid)).fetchone()
        tid = save_topic(c, cid, intent, response, keys.get(key), message['sequence'] if message else 0)
        keys[key] = tid
    update_summary(c, cid)
    c.execute('UPDATE conversations SET hydrated=1 WHERE id=?', (cid,))


def save_page_message(c, cid, message):
    require_conversation(c, cid)
    if message.role != 'agent' or not message.parent_message_id:
        raise HTTPException(422, '页面解读必须关联知财回答')
    existing = c.execute('SELECT conversation_id FROM conversation_messages WHERE message_id=?', (str(message.id),)).fetchone()
    if existing and existing['conversation_id'] != cid:
        raise HTTPException(409, '消息标识已被使用')
    topic_id = None
    if message.parent_message_id.startswith('risk:'):
        product_id=message.parent_message_id[5:]
        product=c.execute('SELECT risk_level FROM wealth_products WHERE id=?',(product_id,)).fetchone()
        account=c.execute("SELECT risk_capacity_level FROM accounts WHERE id='demo-account'").fetchone()
        if not product or not account or product['risk_level']<=account['risk_capacity_level']:
            raise HTTPException(422,'风险提示与当前产品不符')
        level=account['risk_capacity_level']
        label={1:'谨慎型',2:'稳健型',3:'平衡型',4:'进取型',5:'激进型'}[level]
        expected=f"该产品风险等级为 R{product['risk_level']}，超出你的 C{level} {label}可投资范围，无法申购。"
        if message.text!=expected or message.action!={'label':'重新评估','path':'/risk-assessment','icon':'up-right'} or message.actions:
            raise HTTPException(422,'风险提示内容不符')
    else:
        parent = c.execute('SELECT topic_id FROM conversation_messages WHERE message_id=? AND conversation_id=? AND role=?',
                           (message.parent_message_id, cid, 'agent')).fetchone()
        if parent is None:
            raise HTTPException(422, '关联回答不在当前会话中')
        topic_id = parent['topic_id']
        source=c.execute('SELECT actions_json FROM conversation_messages WHERE message_id=? AND conversation_id=?',(message.parent_message_id,cid)).fetchone()
        actions=json.loads(source['actions_json'] or '[]')
        segments=[segment for action in actions for segment in (action.get('bubbleSegments') or [])]
        if message.text not in segments or message.action or message.actions:
            raise HTTPException(422,'页面解读内容与原回复不符')
    duplicate = c.execute(
        'SELECT 1 FROM conversation_messages WHERE conversation_id=? AND parent_message_id=? AND role=? AND text=? AND message_kind=?',
        (cid, message.parent_message_id, message.role, message.text, 'page_explanation')).fetchone()
    if duplicate:
        return
    c.execute("""INSERT OR IGNORE INTO conversation_messages(message_id,role,text,action_json,actions_json,conversation_id,
      topic_id,parent_message_id,message_kind) VALUES (?,?,?,?,?,?,?,?,?)""",
      (str(message.id), message.role, message.text, dumps(message.action) if message.action else None,
       dumps(message.actions) if message.actions else None, cid, topic_id, message.parent_message_id,
       'page_explanation' if message.parent_message_id else 'dialogue'))
    if topic_id:
        seq=c.execute('SELECT sequence FROM conversation_messages WHERE message_id=?',(str(message.id),)).fetchone()['sequence']
        c.execute('UPDATE conversations SET active_topic_id=?,revision=revision+1 WHERE id=?',(topic_id,cid))
        c.execute('UPDATE conversation_topics SET last_sequence=? WHERE id=? AND conversation_id=?',(seq,topic_id,cid))
        update_summary(c,cid)

