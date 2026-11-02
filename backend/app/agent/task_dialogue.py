"""Deterministic clarification and confirmation for extracted task drafts.

This module never invokes a model and never creates/authorizes a scheduled task.
"""
from .schemas import Intent, AgentResponse, Action
from .payee_dialogue import recipient_guard
from ..services.payees import ACCOUNT_ID

TASK_CONFIRMATION = '请核对定时任务信息，授权后开始执行。'
TASK_ACTION_LABEL = '授权定时任务'
TASK_FIELDS = ('amount', 'task_type', 'frequency', 'execution_day', 'product_id', 'payee', 'name', 'risk')


def merge_task_draft(intent, previous):
    if intent.intent != 'task' or not intent.continues_task or previous is None:
        return intent
    if intent.task_type and previous.task_type and intent.task_type != previous.task_type:
        return intent
    updates = {field: getattr(previous, field) for field in TASK_FIELDS
               if getattr(intent, field) is None and getattr(previous, field) is not None}
    return intent.model_copy(update=updates)


def task_response(c, intent, account_id=ACCOUNT_ID):
    if not intent.recurrence_evidence:
        return AgentResponse(text='你希望本次执行，还是按固定周期重复执行？', evidence={
            'reply_source': 'task_rule', 'task_status': 'needs_details', 'missing_fields': ['执行方式'],
            'task_draft': intent.model_dump(mode='json', exclude_none=True)})
    required = ['task_type', 'frequency', 'execution_day', 'amount']
    if intent.task_type == 'fixed_expense':
        required.append('payee')
    elif intent.task_type in ('investment', 'redemption'):
        required.append('product_id')
    labels = {'task_type': '任务用途（固定开支、申购或赎回）', 'frequency': '执行周期（每月、每季度或每半年）',
              'execution_day': '执行日', 'amount': '每次金额', 'payee': '收款方', 'product_id': '具体理财产品'}
    missing = [field for field in required if getattr(intent, field) is None or
               isinstance(getattr(intent, field), str) and not getattr(intent, field).strip()]
    if intent.product_id and intent.task_type in ('investment', 'redemption'):
        valid_all = intent.task_type == 'redemption' and intent.product_id == 'all'
        if not valid_all and not c.execute('SELECT 1 FROM wealth_products WHERE id=?', (intent.product_id,)).fetchone():
            missing.append('product_id')
    evidence = {'reply_source': 'task_rule', 'task_draft': intent.model_dump(mode='json', exclude_none=True),
                'missing_fields': [labels[field] for field in dict.fromkeys(missing)],
                'task_status': 'needs_details' if missing else 'awaiting_authorization'}
    if missing:
        return AgentResponse(text='还需要确认' + '、'.join(evidence['missing_fields']) + '，请补充一下。', evidence=evidence)
    recipient=None
    if intent.task_type=='fixed_expense':
        recipient,blocked=recipient_guard(c,intent,evidence,task=True,account_id=account_id)
        if blocked:
            return blocked
    name = intent.name or ('其他' if intent.task_type == 'fixed_expense' else
                           '申购理财' if intent.task_type == 'investment' else '赎回理财')
    action = Action(label=TASK_ACTION_LABEL, path='/wealth' if intent.task_type == 'investment' else '/me',
                    mode='products' if intent.task_type == 'investment' else None, modal='task',
                    amount=float(intent.amount), day=intent.execution_day, taskType=intent.task_type,
                    taskName=name, frequency=intent.frequency, payee=intent.payee,
                    productId=intent.product_id, risk=intent.risk)
    if recipient:
        action.payeeId=recipient['id']
        action.payeeVersion=recipient['version']
    return AgentResponse(text=TASK_CONFIRMATION, response_mode='open_action', actions=[action], evidence=evidence)
