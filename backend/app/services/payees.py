"""Saved recipients and immutable transfer snapshots for the local demo account."""
import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..repositories import legacy as db

ACCOUNT_ID = db.DEMO_ACCOUNT_ID


class PayeeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    nickname: str = Field(default='',max_length=30)
    holder_name: str = Field(min_length=1,max_length=40)
    bank_name: str = Field(min_length=2,max_length=60)
    account_number: str | None = Field(default=None, pattern=r'^\d{8,32}$')
    version: int | None = Field(default=None,ge=1)

    @field_validator('nickname','holder_name','bank_name',mode='before')
    @classmethod
    def trim(cls,value):
        return value.strip() if isinstance(value,str) else value

    @field_validator('account_number',mode='before')
    @classmethod
    def normalize_number(cls,value):
        if isinstance(value,str) and not value.strip():
            return None
        return re.sub(r'[\s-]','',value) if isinstance(value,str) else value


class OneTimeRecipient(BaseModel):
    model_config = ConfigDict(extra='forbid')
    holder_name: str = Field(min_length=1,max_length=40)
    bank_name: str | None = Field(default=None,min_length=2,max_length=60)
    account_number: str = Field(pattern=r'^\d{8,32}$')

    @field_validator('holder_name','bank_name',mode='before')
    @classmethod
    def trim(cls,value):
        return value.strip() if isinstance(value,str) else value

    @field_validator('account_number',mode='before')
    @classmethod
    def normalize_number(cls,value):
        return re.sub(r'[\s-]','',value) if isinstance(value,str) else value


def one_time_summary(recipient):
    return {'display_name':recipient.holder_name,'holder_name':recipient.holder_name,
            'bank_name':recipient.bank_name,'account_masked':'**** '+recipient.account_number[-4:],
            'first_transfer':True}


def create_payee_schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS saved_payees (
      id TEXT PRIMARY KEY, account_id TEXT NOT NULL, nickname TEXT NOT NULL, holder_name TEXT NOT NULL,
      bank_name TEXT NOT NULL, account_number TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
      active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE UNIQUE INDEX IF NOT EXISTS ix_active_payee_account ON saved_payees(account_id,bank_name,account_number) WHERE active=1;
    CREATE TABLE IF NOT EXISTS transfer_records (
      id TEXT PRIMARY KEY, account_id TEXT NOT NULL, payee_id TEXT, payee_version INTEGER,
      display_name TEXT NOT NULL, holder_name TEXT, bank_name TEXT, account_masked TEXT,
      amount REAL NOT NULL, source TEXT NOT NULL, occurred_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS ix_transfer_record_time ON transfer_records(account_id,occurred_at DESC);
    """)
    columns={row['name'] for row in c.execute('PRAGMA table_info(scheduled_tasks)')}
    for key,definition in (('payee_id','TEXT'),('payee_version','INTEGER')):
        if key not in columns:
            c.execute(f'ALTER TABLE scheduled_tasks ADD COLUMN {key} {definition}')
    # Preserve real ledger history; old name-only entries have no inferred account.
    rows=c.execute("""SELECT t.*,p.command_json,s.payee AS task_payee FROM account_transactions t
      LEFT JOIN operation_previews p ON p.id=t.id LEFT JOIN scheduled_tasks s ON s.id=t.task_id
      WHERE t.transaction_type='transfer' AND NOT EXISTS(SELECT 1 FROM transfer_records r WHERE r.id=t.id)""").fetchall()
    for row in rows:
        try:
            command=json.loads(row['command_json']) if row['command_json'] else {}
        except (ValueError,TypeError):
            command={}
        name=command.get('payee') or row['task_payee'] or '历史收款人'
        occurred=row['occurred_at']
        try:
            parsed=datetime.fromisoformat(occurred)
            occurred=(parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo('Asia/Shanghai'))).isoformat()
        except ValueError:
            pass
        c.execute('INSERT OR IGNORE INTO transfer_records(id,account_id,display_name,amount,source,occurred_at) VALUES (?,?,?,?,?,?)',
                  (row['id'],row['account_id'],name,row['amount'],'task' if row['task_id'] or row['id'].startswith('task:') else 'instant',occurred))


def get_payee(c,pid,include_number=False,account_id=ACCOUNT_ID):
    row=c.execute('SELECT * FROM saved_payees WHERE id=? AND account_id=? AND active=1',(pid,account_id)).fetchone()
    if row is None:
        raise HTTPException(404,'收款人不存在或已移除')
    item=dict(row)
    item['display_name']=item['nickname'] or item['holder_name']
    item['account_last4']=item['account_number'][-4:]
    item['account_masked']='**** '+item['account_last4']
    count=c.execute('SELECT COUNT(*) FROM transfer_records WHERE account_id=? AND payee_id=? AND payee_version=?',(account_id,pid,item['version'])).fetchone()[0]
    item['first_transfer']=count==0
    if not include_number:
        item.pop('account_number')
    item.pop('account_id')
    return item


def list_payees(c, account_id=ACCOUNT_ID):
    return [get_payee(c,row['id'],account_id=account_id) for row in c.execute('SELECT id FROM saved_payees WHERE account_id=? AND active=1 ORDER BY updated_at DESC,rowid DESC',(account_id,))]


def save_payee(c,body,pid=None,account_id=ACCOUNT_ID):
    old=get_payee(c,pid,True,account_id=account_id) if pid else None
    if old and body.version != old['version']:
        raise HTTPException(409,'收款人资料已更新，请重新打开后编辑')
    account_number=body.account_number or (old['account_number'] if old else None)
    if not account_number:
        raise HTTPException(422,'请填写收款账号')
    duplicate=c.execute('SELECT id FROM saved_payees WHERE account_id=? AND active=1 AND bank_name=? AND account_number=? AND id<>?',
                        (account_id,body.bank_name,account_number,pid or '')).fetchone()
    if duplicate:
        raise HTTPException(409,'这个收款账户已经保存了')
    if old:
        changed=old['holder_name']!=body.holder_name or old['bank_name']!=body.bank_name or old['account_number']!=account_number
        c.execute('UPDATE saved_payees SET nickname=?,holder_name=?,bank_name=?,account_number=?,version=version+?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND account_id=?',
                   (body.nickname,body.holder_name,body.bank_name,account_number,int(changed),pid,account_id))
        if changed:
            c.execute("UPDATE scheduled_tasks SET status='已暂停' WHERE account_id=? AND payee_id=? AND status='运行中'",(account_id,pid))
    else:
        pid=str(uuid.uuid4())
        c.execute('INSERT INTO saved_payees(id,account_id,nickname,holder_name,bank_name,account_number) VALUES (?,?,?,?,?,?)',
                   (pid,account_id,body.nickname,body.holder_name,body.bank_name,account_number))
    return get_payee(c,pid,account_id=account_id)


def seed_demo_payees(c, account_id=ACCOUNT_ID):
    """Add initial fictional recipients without replacing edited or archived entries."""
    recipients = (
        ('妈妈', '李春梅', '演示银行', '0000000000001001'),
        ('爸爸', '陈建国', '演示银行', '0000000000001002'),
        ('房东', '王明远', '演示银行', '0000000000001003'),
    )
    added = []
    for nickname, holder_name, bank_name, account_number in recipients:
        existing = c.execute(
            'SELECT 1 FROM saved_payees WHERE account_id=? AND (nickname=? OR (bank_name=? AND account_number=?)) LIMIT 1',
            (account_id, nickname, bank_name, account_number),
        ).fetchone()
        if existing:
            continue
        save_payee(c, PayeeInput(nickname=nickname, holder_name=holder_name,
                                 bank_name=bank_name, account_number=account_number), account_id=account_id)
        added.append(nickname)
    return added


def archive_payee(c,pid,account_id=ACCOUNT_ID):
    get_payee(c,pid,account_id=account_id)
    c.execute('UPDATE saved_payees SET active=0,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=? AND account_id=?',(pid,account_id))
    c.execute("UPDATE scheduled_tasks SET status='已暂停' WHERE account_id=? AND payee_id=? AND status='运行中'",(account_id,pid))
    return {'status':'removed'}


def normalized(value):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',value or '')).lower()


def find_payees(c,query,account_id=ACCOUNT_ID):
    query=normalized(query)
    if not query:
        return []
    tail=re.search(r'(?:尾号|后四位|末四位)(\d{4})$',query)
    if tail:
        name=query[:tail.start()]
        suffix=tail.group(1)
    else:
        name,suffix=query,query if re.fullmatch(r'\d{4}',query) else None
    matches=[]
    for row in c.execute('SELECT id,nickname,holder_name,account_number FROM saved_payees WHERE account_id=? AND active=1',(account_id,)):
        names={normalized(row['nickname']),normalized(row['holder_name'])}-{''}
        matched=(query==row['account_number'] or name in names or suffix and row['account_number'].endswith(suffix) and (not name or name==suffix or name in names))
        if matched:
            matches.append(get_payee(c,row['id'],account_id=account_id))
    return matches


def bind_command(c,command,require_bound=False,account_id=ACCOUNT_ID):
    if not (command.kind=='transfer' or command.kind=='task' and command.task_type=='fixed_expense'):
        return command
    if require_bound and (not command.payee_id or command.payee_version is None):
        raise HTTPException(409,'这项定时转账还没有绑定收款账户，请补充账户后重新授权')
    if command.payee_id:
        try:
            payee=get_payee(c,command.payee_id,account_id=account_id)
        except HTTPException:
            raise HTTPException(409,'收款人已移除，请重新选择') from None
    else:
        matches=find_payees(c,command.payee,account_id=account_id)
        if not matches:
            raise HTTPException(409,'还没保存这位收款人，请先添加并核对账户信息')
        if len(matches)>1:
            raise HTTPException(409,'找到多个收款账户，请选择具体账户')
        payee=matches[0]
    if command.payee_version is not None and command.payee_version!=payee['version']:
        raise HTTPException(409,'收款账户已更新，请重新核对后再转账')
    return command.model_copy(update={'payee':payee['display_name'],'payee_id':payee['id'],'payee_version':payee['version']})


def record_transfer(c,command,event,account_id=ACCOUNT_ID):
    if command.manual_recipient is not None:
        recipient=one_time_summary(command.manual_recipient)
        c.execute('''INSERT OR IGNORE INTO transfer_records(id,account_id,payee_id,payee_version,display_name,holder_name,bank_name,account_masked,amount,source,occurred_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(event['id'],account_id,None,None,recipient['display_name'],recipient['holder_name'],
          recipient['bank_name'],recipient['account_masked'],event['amount'],'instant',datetime.now(timezone.utc).isoformat()))
        return
    payee=get_payee(c,command.payee_id,account_id=account_id)
    c.execute('''INSERT OR IGNORE INTO transfer_records(id,account_id,payee_id,payee_version,display_name,holder_name,bank_name,account_masked,amount,source,occurred_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(event['id'],account_id,payee['id'],payee['version'],payee['display_name'],payee['holder_name'],payee['bank_name'],payee['account_masked'],
      event['amount'],'task' if event['id'].startswith('task:') else 'instant',datetime.now(timezone.utc).isoformat()))


def transfer_history(c, account_id=ACCOUNT_ID):
    return [dict(row) for row in c.execute('SELECT id,payee_id,payee_version,display_name,holder_name,bank_name,account_masked,amount,source,occurred_at FROM transfer_records WHERE account_id=? ORDER BY julianday(occurred_at) DESC,rowid DESC LIMIT 200',(account_id,))]


def recent_payees(c, account_id=ACCOUNT_ID):
    result=[]
    seen=set()
    for row in c.execute('SELECT id,payee_id,payee_version,display_name,holder_name,bank_name,account_masked,amount,source,occurred_at FROM transfer_records WHERE account_id=? ORDER BY julianday(occurred_at) DESC,rowid DESC',(account_id,)):
        record=dict(row)
        key=record['payee_id'] or 'legacy:'+record['display_name']
        if key in seen:
            continue
        seen.add(key)
        current=None
        if record['payee_id']:
            try:
                current=get_payee(c,record['payee_id'],account_id=account_id)
            except HTTPException:
                pass
        result.append({**record,'display_name':current['display_name'] if current else record['display_name'],'payee':current,'account_changed':bool(current and current['version']!=record['payee_version'])})
        if len(result)>=12:
            break
    return result


def mask_account_text(text):
    text=re.sub(r'(?<!\d)(?:\d{4}[ -]){2,7}\d{3,4}(?!\d)|(?<!\d)\d{12,32}(?!\d)',lambda m:'尾号'+re.sub(r'[ -]','',m.group())[-4:],text)
    return re.sub(r'((?:账号|帐号|账户|卡号)[:：]?\s*)(\d{8,11})(?!\d)',lambda m:m.group(1)+'尾号'+m.group(2)[-4:],text)
