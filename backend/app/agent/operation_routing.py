"""Keep one-off requests separate from recurring instructions and draft continuations."""
import re

REPEAT = re.compile(r'每(?:个)?(?:月|天|日|周|星期|年|季度|季|半年|次|隔)|每(?:隔)?[一二两三四五六七八九十\d]+个?月|每逢|总是|经常|定期|定时|定投|周期|循环|重复|按(?:月|周|季|年)|以后.{0,8}(?:都|自动)|(?:新建|创建|设置).{0,12}任务')
NEGATED_REPEAT = re.compile(r'(?:不要|不用|无需|别|不想|不需要|不是|取消|不设置).{0,5}?(?:定时任务|定时|定期|自动|周期|重复|每(?:个)?(?:月|天|周|年))')
ONE_OFF = re.compile(r'只(?:转|汇|打)?(?:这|本|此)?一次|就(?:这|本|此)一次|一次性')
FRESH_MOVEMENT = re.compile(r'(?:今天|现在|立即|这次|本次).{0,20}(?:给|转|汇|买|申购|赎回)|只转(?:这)?一次|一次性')
MONEY_MENTION = re.compile(r'[¥￥]\s*\d|\d[\d,]*(?:\.\d+)?\s*(?:元|块)|(?:转|汇|打款|买入|申购|赎回)\s*[\d一二三四五六七八九十百千万两零]+')


def recurrence_marker(message):
    positive = NEGATED_REPEAT.sub('', message)
    if ONE_OFF.search(positive):
        return None
    match = REPEAT.search(positive)
    return match.group(0) if match else None


def explicit_frequency(message):
    if re.search(r'每半年|每(?:隔)?(?:6|六)个?月|按半年', message):
        return 'semiannual'
    if re.search(r'每(?:个)?季度|每季|每(?:隔)?(?:3|三)个?月|按季', message):
        return 'quarterly'
    if re.search(r'每(?:个)?月|每隔(?:1|一)个?月|按月|每逢(?:月初|月底|月末)', message):
        return 'monthly'
    return None


def route_operation(intent, message, task_draft=None, transfer_draft=None):
    if intent.intent not in ('task', 'transfer'):
        return intent
    marker = recurrence_marker(message)
    fresh = bool(FRESH_MOVEMENT.search(message))
    if not marker and not fresh:
        if task_draft and task_draft.recurrence_evidence and intent.continues_task:
            return intent.model_copy(update={'intent': 'task', 'recurrence_evidence': task_draft.recurrence_evidence})
        if transfer_draft and (intent.continues_transfer or intent.intent == 'transfer'):
            return intent.model_copy(update={'intent': 'transfer', 'continues_transfer': True,
                'continues_task': False, 'recurrence_evidence': None, 'task_type': None,
                'frequency': None, 'execution_day': None})
    if marker and intent.intent == 'task':
        frequency = explicit_frequency(message)
        # Vague habit words prove repetition, not a monthly cycle or execution day.
        return intent.model_copy(update={'intent': 'task', 'task_type': intent.task_type or 'fixed_expense',
            'recurrence_evidence': marker, 'frequency': frequency,
            'execution_day': intent.execution_day if frequency or re.search(r'\d{1,2}\s*[号日]', message) else None,
            'continues_transfer': False, 'continues_task': intent.continues_task and not fresh})
    if intent.intent == 'task':
        kind = {'fixed_expense': 'transfer', 'investment': 'purchase', 'redemption': 'redemption'}.get(intent.task_type)
        if kind is None and (intent.payee or re.search(r'转|汇|打款', message)):
            kind = 'transfer'
        if kind is None:
            # No evidence of repetition: ask what kind of execution the user wants.
            return intent.model_copy(update={'recurrence_evidence': None, 'frequency': None,
                'execution_day': None, 'continues_task': False})
        intent = intent.model_copy(update={'intent': kind})
    updates = {'recurrence_evidence': None, 'frequency': None, 'execution_day': None,
               'task_type': None, 'continues_task': False}
    if fresh:
        updates['continues_transfer'] = False
        # A previous task's amount must not leak into a new one-off request.
        if not MONEY_MENTION.search(message):
            updates['amount'] = None
    return intent.model_copy(update=updates)
