"""Isolated browser-test server. Fake intent client is confined to this module."""
import os
from pathlib import Path
import tempfile
import uvicorn
from backend.main import app
from backend.app.repositories import legacy as db
from backend.app.agent import chat
from backend.app.agent.schemas import Intent
from backend.app.agent import narrative
from backend.app.agent.schemas import Narrative
import json
import argparse


class TestWriter:
    def invoke(self,messages):
        context=json.loads(messages[-1][1])['context']
        return Narrative(text=context['required_text'],bubble_text=context['required_bubble'])


def interpret(message, history, products, bill_context=None, task_context=None, transfer_context=None, forecast_context=None, conversation_context=None, payee_index=None):
    if message=='给妈妈转500元':
        return Intent(intent='transfer',payee='妈妈',amount=500)
    if '给陌生人' in message:
        return Intent(intent='transfer',payee='陌生人',amount=50)
    if '每月5号给测试房东交房租' in message:
        return Intent(intent='task',task_type='fixed_expense',frequency='monthly',execution_day=5,payee='测试房东',name='房租')
    if '回到刚才的房租' in message:
        topic=next(t for t in conversation_context['topics'] if t['kind']=='task' and '测试房东' in t['label'])
        return Intent(intent='task',amount=3000,context_mode='resume',context_topic_id=topic['topic_id'])
    if '为什么是' in message:
        return Intent(intent='explain')  # Exercise the audited-context routing guard.
    if '今天给妈妈转钱' in message:
        # Deliberately wrong classification: production guard must recover.
        return Intent(intent='task',task_type='fixed_expense',payee='妈妈',amount=3000,
                      frequency='monthly',execution_day=5)
    if transfer_context and message=='500元':
        return Intent(intent='transfer',amount=500,continues_transfer=True)
    if '账单' in message:
        return Intent(intent='bills',month='2026-10')
    if '房东' in message:
        return Intent(intent='task',task_type='fixed_expense',amount=3000,execution_day=5,
                      frequency='monthly',payee='房东',name='租金')
    if '实际年化' in message:
        return Intent(intent='performance')
    if '最多能投多少' in message:
        return Intent(intent='products',risk=2,include_forecast=True)
    return Intent(intent='products',risk=2)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=18000)
    args=parser.parse_args()
    os.environ['ZHICAI_LLM_MODEL']=''
    with tempfile.TemporaryDirectory(prefix='zhicai-browser-') as folder:
        db.DATABASE_PATH=Path(folder)/'browser.sqlite3'
        db.initialize_database()
        from backend.app.services.commands import create_command_schema,seed_account
        from backend.app.services.payees import PayeeInput,save_payee,seed_demo_payees
        from contextlib import closing
        with closing(db.connect_db()) as c,c:
            create_command_schema(c)
            seed_account(c)
            seed_demo_payees(c)
            save_payee(c,PayeeInput(nickname='测试房东',holder_name='测试房东（演示）',
                                    bank_name='演示银行',account_number='0000000000009998'))
            from backend.app.security.auth import create_auth_schema,provision_user
            create_auth_schema(c)
            provision_user(c,'demo-user','demo-password-123','account_user')
            provision_user(c,'demo-admin','demo-admin-password-123','admin')
        chat.interpret=interpret
        narrative.narrative_client=lambda:TestWriter()
        uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')
