"""Conversation-local topic resolution and budgeted context assembly, without LangGraph."""
import json
import re
from langchain_core.messages.utils import count_tokens_approximately
from ..services.conversations import read_topics, require_conversation
from .schemas import Intent, AgentResponse


def search_terms(message):
    terms = re.findall(r'\d[\d,]*(?:\.\d+)?|[A-Za-z]+\d*', message)
    for phrase in re.findall(r'[\u4e00-\u9fff]+', message):
        terms.extend(phrase[i:i+2] for i in range(len(phrase)-1))
    stop = {'为什么', '为什', '什么', '怎么', '帮我', '一下', '回到', '刚才', '之前', '这个', '那个', '多少'}
    return list(dict.fromkeys(term for term in terms if term not in stop))[:18]


def topic_score(topic, message):
    state = json.loads(topic['state_json'])
    score = 0
    for field in ('payee', 'name', 'product_id', 'month'):
        value = state.get(field)
        if value and str(value) in message:
            score += 15
    month = re.search(r'(?<!\d)(\d{1,2})月', message)
    if month and state.get('month', '').endswith('-'+month.group(1).zfill(2)):
        score += 12
    label = topic['label']
    score += sum(2 for term in search_terms(message) if term in label)
    return score


def _message(row, relevant=False):
    return {'id': row['message_id'], 'role': row['role'], 'text': row['text'],
            'sequence': row['sequence'], 'topic_id': row['topic_id'],
            'parent_message_id': row['parent_message_id'], 'message_kind': row['message_kind'],
            '_relevant': relevant}


def load_context(c, cid, message, reply_to=None):
    conversation = require_conversation(c, cid)
    topics = read_topics(c, cid)
    ranked = sorted(topics, key=lambda t: (topic_score(t, message), t['last_sequence']), reverse=True)
    active = next((t for t in topics if t['id'] == conversation['active_topic_id']), None)
    candidates = []
    for topic in ([active] if active else []) + ranked[:6] + [t for t in topics if t['status'] in ('needs_details','awaiting_confirmation')][:6]:
        if topic and topic['id'] not in {t['id'] for t in candidates}:
            candidates.append(topic)
    rows = c.execute('SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY sequence DESC LIMIT 40', (cid,)).fetchall()
    if rows and rows[-1]['role']!='user':
        anchor=c.execute("SELECT sequence FROM conversation_messages WHERE conversation_id=? AND role='user' AND sequence<=? ORDER BY sequence DESC LIMIT 1",(cid,rows[-1]['sequence'])).fetchone()
        if anchor:
            rows=c.execute('SELECT * FROM conversation_messages WHERE conversation_id=? AND sequence>=? ORDER BY sequence DESC',(cid,anchor['sequence'])).fetchall()
    history = {row['sequence']: _message(row) for row in rows}
    related = []
    terms = search_terms(message)
    if terms:
        where = ' OR '.join('instr(text,?)>0' for _ in terms)
        related.extend(c.execute(f'SELECT * FROM conversation_messages WHERE conversation_id=? AND ({where}) ORDER BY sequence DESC LIMIT 12', (cid, *terms)).fetchall())
    refs = [t['evidence_message_id'] for t in ranked[:3] if topic_score(t, message)>0 and t['evidence_message_id']]
    if reply_to:
        refs.insert(0, reply_to)
    for ref in refs:
        row = c.execute('SELECT * FROM conversation_messages WHERE conversation_id=? AND message_id=?', (cid, ref)).fetchone()
        if row:
            related.append(row)
    evidence_refs = []
    for row in related:
        history[row['sequence']] = _message(row, True)
        source_id = row['parent_message_id'] or row['message_id']
        if source_id not in evidence_refs:
            evidence_refs.append(source_id)
        source = c.execute('SELECT * FROM conversation_messages WHERE conversation_id=? AND message_id=?', (cid, source_id)).fetchone()
        anchor = source['sequence'] if source else row['sequence']
        question = c.execute("SELECT * FROM conversation_messages WHERE conversation_id=? AND role='user' AND sequence<=? ORDER BY sequence DESC LIMIT 1", (cid, anchor)).fetchone()
        if source:
            history[source['sequence']] = _message(source, True)
        if question:
            history[question['sequence']] = _message(question, True)
    def describe(t):
        return {'topic_id': t['id'], 'label': t['label'], 'kind': t['kind'], 'status': t['status'],
                'paused': t['id']!=conversation['active_topic_id'] and t['status'] in ('needs_details','awaiting_confirmation'),
                'slots': json.loads(t['state_json']), 'missing_fields': json.loads(t['missing_json']),
                'evidence_message_id': t['evidence_message_id']}
    summary = json.loads(conversation['summary_json'])
    # The full summary remains in SQLite; load only relevant entries into the prompt.
    entries = summary.get('topics', [])
    candidate_ids = {t['id'] for t in candidates}
    compact_summary = [entry for entry in entries if entry['topic_id'] not in candidate_ids][:4]
    pack = {'active_topic_id': conversation['active_topic_id'], 'topics': [describe(t) for t in candidates],
            'older_topic_summary': compact_summary, 'reply_to_message_id': reply_to,
            'summary_policy': 'structured_extracts; facts must be re-queried or loaded from referenced audit'}
    pack['related_answers']=[]
    for mid in evidence_refs[:6]:
        row=c.execute("SELECT message_id,topic_id,text FROM conversation_messages WHERE conversation_id=? AND message_id=? AND role='agent'",(cid,mid)).fetchone()
        if row:
            pack['related_answers'].append({'message_id':row['message_id'],'topic_id':row['topic_id'],'excerpt':row['text'][:180]})
    return {'conversation_id': cid, 'revision': conversation['revision'], 'topics': topics,
            'active': active, 'history': sorted(history.values(), key=lambda m:m['sequence']),
            'pack': pack, 'evidence_refs': evidence_refs}


def assemble_messages(system, history, current, budget=8000, data_context=None):
    """Count estimated tokens with LangChain; keep complete message turns and original text."""
    groups = []
    for item in history:
        if not groups or item['role']=='user':
            groups.append([])
        groups[-1].append(item)
    chosen = []
    extra = [('human', data_context)] if data_context else []
    base = [('system', system), *extra, ('human', current)]
    remaining = max(0, budget-count_tokens_approximately(base, chars_per_token=1.6))
    priorities = sorted(range(len(groups)), key=lambda i: (i>=len(groups)-2, any(x.get('_relevant') for x in groups[i]), i), reverse=True)
    for index in priorities:
        messages = [('human' if x['role']=='user' else 'ai', x['text']) for x in groups[index]]
        cost = count_tokens_approximately(messages, chars_per_token=1.6)
        if cost <= remaining:
            chosen.append(index)
            remaining -= cost
    selected = [x for index in sorted(chosen) for x in groups[index]]
    return [('system', system)] + [('human' if x['role']=='user' else 'ai', x['text']) for x in selected] + extra + [('human', current)]


def select_topic(c, intent, message, context):
    topics = context['topics']
    cid = context['conversation_id']
    selected = None
    if context['pack']['reply_to_message_id'] and not intent.context_message_id and intent.context_mode!='new':
        intent=intent.model_copy(update={'context_message_id':context['pack']['reply_to_message_id']})
    if intent.context_message_id:
        row = c.execute('SELECT topic_id FROM conversation_messages WHERE conversation_id=? AND message_id=?',
                        (cid, intent.context_message_id)).fetchone()
        if row is None:
            return intent, None, AgentResponse(text='请说明你指的是哪条回答，我再继续分析。', evidence={'reply_source':'context_rule'})
        selected = next((t for t in topics if t['id']==row['topic_id']), None)
    if intent.context_topic_id:
        selected = next((t for t in topics if t['id']==intent.context_topic_id), None)
        if selected is None:
            return intent, None, AgentResponse(text='请说明你想继续哪个话题，我再接着处理。', evidence={'reply_source':'context_rule'})
    if intent.context_mode=='new':
        return intent, None, None
    continuation = intent.continues_task or intent.continues_transfer or intent.context_mode in ('continue','resume')
    if selected is None and continuation:
        same = [t for t in topics if t['kind']==intent.intent or intent.context_mode=='resume' and t['status']=='needs_details']
        ranked = sorted(same, key=lambda t:topic_score(t,message), reverse=True)
        if ranked and topic_score(ranked[0],message)>0 and (len(ranked)==1 or topic_score(ranked[0],message)>topic_score(ranked[1],message)):
            selected = ranked[0]
        elif context['active'] in same:
            selected = context['active']
        elif len(same)==1:
            selected = same[0]
        elif len(same)>1:
            labels = '、'.join(t['label'] for t in same[:4])
            return intent, None, AgentResponse(text=f'你想继续哪一项：{labels}？', evidence={'reply_source':'context_rule'})
    if selected is None and intent.intent not in ('task','transfer','purchase','redemption','unknown'):
        matching = [t for t in topics if t['kind']==intent.intent or t['kind'] in ('products','forecast') and intent.intent in ('products','forecast')]
        if intent.intent=='bills' and intent.month:
            matching = [t for t in matching if json.loads(t['state_json']).get('month')==intent.month]
        selected = matching[0] if matching else None
    if selected and selected['kind'] in ('task','transfer'):
        if selected['status']=='completed' and intent.context_mode=='resume':
            return intent, selected, AgentResponse(text='这项操作已经确认完成。如需再操作，请说明新的金额和执行要求。', evidence={'reply_source':'context_rule'})
        state = Intent.model_validate_json(selected['state_json'])
        if intent.payee and state.payee and intent.payee!=state.payee:
            return intent, None, None
        if intent.context_mode=='resume' and selected['status'] in ('needs_details','awaiting_confirmation'):
            intent = intent.model_copy(update={'intent': selected['kind']})
        if continuation and intent.intent==selected['kind'] and selected['status'] in ('needs_details','awaiting_confirmation'):
            field = 'continues_task' if intent.intent=='task' else 'continues_transfer'
            intent = intent.model_copy(update={field:True})
    if selected and intent.intent == 'products' and selected['kind'] == 'products' and selected['status'] == 'needs_details':
        previous = json.loads(selected['state_json'])
        intent = intent.model_copy(update={'include_forecast': intent.include_forecast or previous.get('include_forecast', False)})
    if selected and intent.intent in ('products','forecast') and (intent.context_mode in ('continue','resume') or intent.context_message_id or intent.context_topic_id):
        previous = json.loads(selected['state_json'])
        risk = previous.get('risk')
        if risk and intent.risk is None:
            intent = intent.model_copy(update={'risk':risk})
    return intent, selected, None


def attach_referenced_evidence(c, cid, intent, response, selected):
    if intent.context_mode=='new' or intent.intent in ('task','transfer','purchase','redemption','unknown'):
        return
    if not intent.context_message_id and not intent.context_topic_id and intent.context_mode not in ('continue','resume'):
        return
    mid=intent.context_message_id or (selected['evidence_message_id'] if selected else None)
    if not mid:
        return
    row=c.execute('SELECT parent_message_id FROM conversation_messages WHERE conversation_id=? AND message_id=?',(cid,mid)).fetchone()
    if row and row['parent_message_id']:
        mid=row['parent_message_id']
    audit=c.execute('SELECT tool,result_json FROM agent_audit WHERE conversation_id=? AND id=?',(cid,mid)).fetchone()
    if audit:
        original=json.loads(audit['result_json']).get('evidence',{})
        response.evidence['referenced_answer']={'id':mid,'scope':'historical_snapshot','tool':audit['tool'],
            'evidence':{k:v for k,v in original.items() if k in ('tool','result','topic','previous_basis')}}

