"""One-off transfer clarification/confirmation; no model prose or account mutations."""
from .schemas import Intent, AgentResponse, Action
from .payee_dialogue import recipient_guard
from ..services.payees import find_payees, ACCOUNT_ID


def merge_transfer_draft(intent, previous):
    if intent.intent != 'transfer' or not intent.continues_transfer or previous is None:
        return intent
    return intent.model_copy(update={field: getattr(previous, field) for field in ('amount', 'payee', 'account_number')
                                    if getattr(intent, field) is None and getattr(previous, field) is not None})


def transfer_response(intent,c=None,manual_recipient=None,account_id=ACCOUNT_ID):
    if intent.account_number and c is not None:
        saved=find_payees(c,intent.account_number,account_id=account_id)
        if saved:
            # 账号命中常用收款人时以账号为准，直接用保存的称呼；
            # 用户口头写的名字可能与昵称/户名不同（“给妈妈”“给王秀兰”），不必提示核对。
            recipient=saved[0]
            intent=intent.model_copy(update={'payee':recipient['display_name']})
        else:
            missing=[]
            if not intent.payee:missing.append('收款人开户姓名')
            if intent.amount is None:missing.append('本次转账金额')
            evidence={'reply_source':'transfer_rule','operation_draft':intent.model_dump(mode='json',exclude_none=True),
                      'missing_fields':missing,'transfer_status':'needs_details' if missing else 'awaiting_confirmation'}
            if missing:
                return AgentResponse(text='请告诉我'+ '和'.join(missing)+'，我会生成一次性转账确认信息供你核对。',evidence=evidence)
            manual_recipient={'holder_name':intent.payee,'account_number':intent.account_number}
            return AgentResponse(text='请核对本次转账信息，确认后立即执行。',response_mode='open_action',
                actions=[Action(label='确认转账',path='/transfer',modal='transfer',amount=float(intent.amount),
                                payee=intent.payee,manualRecipient=manual_recipient)],evidence=evidence)
    missing = []
    if not intent.payee or not intent.payee.strip():
        missing.append('收款方')
    if intent.amount is None:
        missing.append('本次转账金额')
    evidence = {'reply_source': 'transfer_rule', 'operation_draft': intent.model_dump(mode='json', exclude_none=True),
                'missing_fields': missing, 'transfer_status': 'needs_details' if missing else 'awaiting_confirmation'}
    recipient=None
    if intent.payee and c is not None and manual_recipient is None:
        recipient,blocked=recipient_guard(c,intent,evidence,account_id=account_id)
        if blocked:
            return blocked
    if missing:
        if missing == ['本次转账金额']:
            text = f'这次给{intent.payee}转多少钱？'
        elif missing == ['收款方']:
            text = '这笔转账的收款方是谁？'
        else:
            text = '请告诉我这笔转账的收款方和金额。'
        return AgentResponse(text=text, evidence=evidence)
    if manual_recipient:
        evidence['recipient']={'display_name':manual_recipient['holder_name'],
                               'account_masked':'**** '+manual_recipient['account_number'][-4:],
                               'one_time':True}
    return AgentResponse(text='请核对本次转账信息，确认后立即执行。', response_mode='open_action',
        actions=[Action(label='确认转账', path='/transfer', modal='transfer', amount=float(intent.amount), payee=recipient['display_name'] if recipient else intent.payee,
                        payeeId=recipient['id'] if recipient else None,payeeVersion=recipient['version'] if recipient else None,
                        manualRecipient=manual_recipient)],
        evidence=evidence)
