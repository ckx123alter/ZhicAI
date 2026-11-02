"""Resolve investment-limit follow-ups using audited calculations, not chat prose."""
import json
import re
from decimal import Decimal, InvalidOperation
from .schemas import AgentResponse, Action

NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
REASON = re.compile(r'为什么|为何|怎么.*(?:算|得)|怎么算|依据|原因')
BARE_REASON = re.compile(r'^(?:为什么|为何|怎么算的|怎么得出来的|依据是什么)[？?。！!]*$')


def forecast_basis(result):
    try:
        minimum, reserve, cap = [Decimal(str(result[key])) for key in ('minimum', 'reserve', 'cap')]
        deducted = Decimal(str(result.get('deducted_reserve', reserve)))
        if not all(value.is_finite() for value in (minimum, reserve, cap)) or reserve < 0:
            return None
        if not deducted.is_finite() or deducted < 0 or cap != max(Decimal(0), minimum-deducted):
            return None
        points = result.get('points') or []
        return {'minimum': float(minimum), 'reserve': float(reserve), 'cap': float(cap),
                'deducted_reserve': float(deducted), 'liquidity_policy': result.get('liquidity_policy', 'buffered'),
                'product_ids': result.get('product_ids', []),
                'days': len(points)-1 if len(points) > 1 else 30,
                'start_date': points[0].get('date') if points else None}
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None


def recent_forecast_context(c, message, conversation_id='default', reference_ids=()):
    recent = c.execute('SELECT id,tool,result_json FROM agent_audit WHERE conversation_id=? ORDER BY rowid DESC LIMIT 10', (conversation_id,)).fetchall()
    referenced = []
    for mid in dict.fromkeys(reference_ids):
        row=c.execute('SELECT id,tool,result_json FROM agent_audit WHERE id=? AND conversation_id=?',(mid,conversation_id)).fetchone()
        if row:
            referenced.append(row)
    rows = referenced + [row for row in recent if row['id'] not in {r['id'] for r in referenced}]
    candidates = []
    for row in rows:
        if row['tool'] not in ('products', 'forecast'):
            continue
        try:
            evidence = json.loads(row['result_json']).get('evidence', {})
            result = evidence.get('result', {})
            current = forecast_basis(result.get('cashflow', {}) if row['tool'] == 'products' else result)
            alternatives = [forecast_basis(item) for item in result.get('investment_limits', [])]
            for basis in (current, *alternatives, forecast_basis(evidence.get('previous_basis', {}))):
                if basis and basis not in candidates:
                    candidates.append(basis)
        except (TypeError, ValueError):
            continue
    if not candidates:
        return None
    mentioned = {Decimal(match.group().replace(',', '')) for match in NUMBER.finditer(message)}
    matched = next((basis for basis in candidates if Decimal(str(basis['cap'])) in mentioned), None)
    return {'basis': matched or candidates[0], 'quoted_amount_matched': matched is not None,
            'direct_previous': bool(recent and recent[0]['tool'] in ('products', 'forecast'))}


def route_forecast_followup(intent, message, context):
    if context is None or intent.intent not in ('explain', 'products', 'forecast'):
        return intent
    reason = bool(REASON.search(message))
    referenced = (context['quoted_amount_matched'] or
                  context['direct_previous'] and (BARE_REASON.fullmatch(message.strip()) or
                                                re.search(r'这个(?:金额|上限|数)|可投(?:入)?上限|投入上限', message)))
    if intent.forecast_focus == 'investment_limit' or reason and referenced:
        return intent.model_copy(update={'intent': 'forecast', 'forecast_focus': 'investment_limit',
                                         'include_forecast': False})
    return intent


def investment_limit_response(result, context=None):
    current = forecast_basis(result)
    basis = context['basis'] if context else current
    prefix = '上次建议的投入上限' if context else '建议投入上限'
    daily = basis.get('liquidity_policy') == 'daily_redeemable'
    text = (f"{prefix} ¥{basis['cap']:.2f}，依据是未来{basis['days']}天最低预测余额"
            f" ¥{basis['minimum']:.2f}。")
    if daily:
        text += '本次产品支持每日可赎回，推荐额度不再扣除预留警戒线。'
    else:
        text += f"扣除预留警戒线 ¥{basis['reserve']:.2f}。"
        text += ('两者差额为可投入金额。' if basis['cap'] > 0 else '预测余额未超过预留线，因此可投入上限取零。')
    if context and any(current.get(key) != basis.get(key) for key in ('cap', 'minimum', 'reserve', 'deducted_reserve')):
        text += (f"当前重新计算的最低预测余额为 ¥{current['minimum']:.2f}，预留 ¥{current['reserve']:.2f}，"
                 f"建议上限已更新为 ¥{current['cap']:.2f}，请以最新预测为准。")
    text += '点“查看现金流预测”，就能看到每天的余额和收支安排。'
    return AgentResponse(text=text, response_mode='navigate',
        actions=[Action(label='查看现金流预测', path='/me')],
        evidence={'tool': 'forecast', 'topic': 'investment_limit_explanation', 'result': result,
                  'previous_basis': basis if context else None,
                  'formula': 'max(0, minimum - deducted_reserve)'})
