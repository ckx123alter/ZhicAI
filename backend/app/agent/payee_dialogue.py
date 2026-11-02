"""Recipient lookup is server-owned; the model cannot invent payment accounts."""
from ..services.payees import find_payees,mask_account_text,ACCOUNT_ID
from .schemas import AgentResponse,Action


def recipient_guard(c,intent,evidence,task=False,account_id=ACCOUNT_ID):
    matches=find_payees(c,intent.payee,account_id=account_id)
    if len(matches)==1:
        evidence['recipient']=matches[0]
        return matches[0],None
    evidence['missing_fields']=['已保存的收款账户']
    evidence['task_status' if task else 'transfer_status']='needs_details'
    kwargs=dict(amount=float(intent.amount) if intent.amount else None,payee=mask_account_text(intent.payee or ''),
                day=intent.execution_day,taskType=intent.task_type,taskName=intent.name,frequency=intent.frequency)
    if not matches:
        text=f'还没保存“{mask_account_text(intent.payee or "这位收款人")}”的收款账户。请先添加并核对账户信息。'
        return None,AgentResponse(text=text,response_mode='open_action',
            actions=[Action(label='新增常用收款人',path='/transfer/payees',modal='payee',**kwargs)],evidence=evidence)
    evidence['recipient_candidates']=matches
    actions=[]
    if intent.amount is not None:
        for payee in matches[:4]:
            actions.append(Action(label=f"{payee['display_name']} · {payee['bank_name']} 尾号{payee['account_last4']}",
                path='/me' if task else '/transfer',modal='task' if task else 'transfer',
                payeeId=payee['id'],payeeVersion=payee['version'],**kwargs))
    else:
        actions=[Action(label='选择收款账户',path='/transfer/payees')]
    return None,AgentResponse(text='找到了多个收款账户，请选择本次要转入的账户。',response_mode='open_action',actions=actions,evidence=evidence)
