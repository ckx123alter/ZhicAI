from contextlib import closing
from fastapi import APIRouter, HTTPException, Header, Request, Depends
from collections import defaultdict, deque
from threading import Lock
from time import monotonic
from pydantic import BaseModel, Field, ConfigDict
from ..repositories import legacy as db
from ..services.commands import Command, preview, confirm, snapshot, issue_challenge
from ..services.finance import forecast, performance
from ..agent.chat import chat
from ..agent.planner import advance_operation_plan
from ..agent.schemas import AgentResponse
from ..services.conversations import create_conversation, require_conversation
from ..services.payees import PayeeInput,list_payees,get_payee,save_payee,archive_payee,transfer_history,recent_payees
from ..security.policy import require_account_user

router = APIRouter(prefix="/api", dependencies=[Depends(require_account_user)])
_chat_hits = defaultdict(deque)
_chat_hits_lock = Lock()


def enforce_chat_quota(address: str) -> None:
    now = monotonic()
    with _chat_hits_lock:
        hits = _chat_hits[address]
        while hits and hits[0] <= now - 86400:
            hits.popleft()
        if len(hits) >= 300 or sum(at > now - 60 for at in hits) >= 20:
            raise HTTPException(429, '对话请求过于频繁，请稍后再试')
        hits.append(now)


@router.get('/payees')
def payees(request: Request):
    with closing(db.connect_db()) as c:
        return list_payees(c, account_id=request.state.actor.account_id)


@router.get('/payees/{payee_id}')
def payee_detail(payee_id: str, request: Request):
    with closing(db.connect_db()) as c:
        return get_payee(c, payee_id, account_id=request.state.actor.account_id)


@router.post('/payees')
def add_payee(body: PayeeInput, request: Request):
    with closing(db.connect_db()) as c,c:
        c.execute('BEGIN IMMEDIATE')
        return save_payee(c, body, account_id=request.state.actor.account_id)


@router.put('/payees/{payee_id}')
def edit_payee(payee_id: str, body: PayeeInput, request: Request):
    with closing(db.connect_db()) as c,c:
        c.execute('BEGIN IMMEDIATE')
        return save_payee(c, body, payee_id, account_id=request.state.actor.account_id)


@router.delete('/payees/{payee_id}')
def remove_payee(payee_id: str, request: Request):
    with closing(db.connect_db()) as c,c:
        c.execute('BEGIN IMMEDIATE')
        return archive_payee(c, payee_id, account_id=request.state.actor.account_id)


@router.get('/transfers')
def transfers(request: Request):
    with closing(db.connect_db()) as c:
        return transfer_history(c, account_id=request.state.actor.account_id)


@router.get('/transfers/recent')
def recent_transfer_payees(request: Request):
    with closing(db.connect_db()) as c:
        return recent_payees(c, account_id=request.state.actor.account_id)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)
    reply_to_message_id: str | None = Field(default=None,max_length=100)


class ConversationCreate(BaseModel):
    adopt_legacy: bool = False


@router.post('/conversations')
def new_conversation(body: ConversationCreate, request: Request):
    with closing(db.connect_db()) as c,c:
        c.execute('BEGIN IMMEDIATE')
        return create_conversation(c,body.adopt_legacy,request.state.actor.user_id)


@router.get('/conversation/context')
def conversation_context(request: Request, x_conversation_id: str = Header(default='default')):
    with closing(db.connect_db()) as c:
        row=require_conversation(c,x_conversation_id,request.state.actor.user_id)
        import json
        return {'conversation_id':row['id'],'active_topic_id':row['active_topic_id'],
                'summary':json.loads(row['summary_json']),'revision':row['revision']}


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool = Field(strict=True)
    buffer_acknowledged: bool = Field(default=False, strict=True)
    challenge_token: str | None = Field(default=None, max_length=200)


class ChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


@router.post("/chat", response_model=AgentResponse)
def post_chat(body: ChatRequest, request: Request, x_conversation_id: str = Header(default='default')):
    if not body.message.strip():
        raise HTTPException(422, "消息不能为空")
    enforce_chat_quota(request.client.host if request.client else 'unknown')
    with closing(db.connect_db()) as c:
        require_conversation(c,x_conversation_id,request.state.actor.user_id)
    return chat(body.message,x_conversation_id,body.reply_to_message_id,account_id=request.state.actor.account_id)


@router.get("/finance/overview")
def overview(request: Request):
    with closing(db.connect_db()) as c:
        c.execute("BEGIN")
        state, products = snapshot(c, request.state.actor.account_id), db.products_from_db(c)
        return {"state": state, "cashflow": forecast(state, products), "performance": performance(state, products)}


@router.get("/tasks/runs")
def task_runs():
    with closing(db.connect_db()) as c:
        return [dict(r) for r in c.execute("SELECT * FROM task_runs ORDER BY run_date DESC, task_id LIMIT 100")]


@router.get("/agent/audit")
def audit(request: Request, x_conversation_id: str = Header(default='default')):
    with closing(db.connect_db()) as c:
        require_conversation(c,x_conversation_id,request.state.actor.user_id)
        return [dict(r) for r in c.execute("SELECT * FROM agent_audit WHERE conversation_id=? ORDER BY rowid DESC LIMIT 100",(x_conversation_id,))]


@router.post("/operations/preview")
def operation_preview(body: Command, request: Request, x_conversation_id: str = Header(default='default'), x_context_message_id: str | None = Header(default=None)):
    with closing(db.connect_db()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        require_conversation(c,x_conversation_id,request.state.actor.user_id)
        topic_id=None
        if x_context_message_id:
            source=c.execute('SELECT topic_id FROM conversation_messages WHERE conversation_id=? AND message_id=?',(x_conversation_id,x_context_message_id)).fetchone()
            if source is None:
                raise HTTPException(422,'操作来源不在当前会话中')
            topic_id=source['topic_id']
        result=preview(c,body,account_id=request.state.actor.account_id)
        c.execute('UPDATE operation_previews SET conversation_id=?,context_topic_id=?,context_message_id=?,owner_user_id=? WHERE id=?',
                  (x_conversation_id,topic_id,x_context_message_id,request.state.actor.user_id,result['preview_id']))
        advance_operation_plan(c,x_conversation_id,topic_id,'preview')
        return result


@router.post("/operations/{preview_id}/challenge")
def operation_challenge(preview_id: str, body: ChallengeRequest, request: Request, x_conversation_id: str = Header(default='default')):
    with closing(db.connect_db()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        require_conversation(c,x_conversation_id,request.state.actor.user_id)
        return issue_challenge(c, preview_id, request.state.actor.user_id, body.password,
                               account_id=request.state.actor.account_id)


@router.post("/operations/{preview_id}/confirm")
def operation_confirm(preview_id: str, body: Confirmation, request: Request, x_conversation_id: str = Header(default='default')):
    if not body.confirmed:
        raise HTTPException(422, "需要用户明确确认")
    with closing(db.connect_db()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        require_conversation(c,x_conversation_id,request.state.actor.user_id)
        row=c.execute('SELECT context_topic_id,context_message_id FROM operation_previews WHERE id=? AND conversation_id=? AND owner_user_id=?',
                      (preview_id,x_conversation_id,request.state.actor.user_id)).fetchone()
        if row is None:
            raise HTTPException(404,'操作预览不存在或不在当前会话中')
        result=confirm(c,preview_id,body.buffer_acknowledged,account_id=request.state.actor.account_id,challenge_token=body.challenge_token)
        if row['context_topic_id']:
            changed=c.execute("UPDATE conversation_topics SET status='completed',missing_json='[]' WHERE id=? AND conversation_id=? AND evidence_message_id=? AND status='awaiting_confirmation'",(row['context_topic_id'],x_conversation_id,row['context_message_id'])).rowcount
            if changed:
                advance_operation_plan(c,x_conversation_id,row['context_topic_id'],'confirmed')
                c.execute('UPDATE conversations SET revision=revision+1 WHERE id=?',(x_conversation_id,))
            from ..services.conversations import update_summary
            update_summary(c,x_conversation_id)
        return result
