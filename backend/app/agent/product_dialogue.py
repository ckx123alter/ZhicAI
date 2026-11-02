"""Start unqualified product searches with a clarification, not an assumed risk."""
import json
import re

from .schemas import AgentResponse, Action
from .tools import products
from ..services.commands import snapshot
from ..repositories import legacy as db


def reset_unspecified_product_search(intent, message):
    if intent.intent != 'products':
        return intent
    new_search = re.search(r'(?:想|要|打算|准备|帮我|给我).*理财|推荐.*理财', message)
    risk_or_reference = re.search(r'R\s*[1-5]|风险|稳健|保守|激进|平衡|低一点|刚才|之前|这些|这种|继续|同样|同类', message, re.I)
    if new_search and not risk_or_reference:
        return intent.model_copy(update={'risk': None, 'context_mode': 'new',
                                         'context_topic_id': None, 'context_message_id': None})
    return intent


def route_risk_only_purchase(intent, message):
    """A risk band alone does not identify a product or authorize a purchase."""
    match = re.search(r'(?<![A-Za-z0-9])R\s*([1-5])\s*(?:的)?(?:理财|产品)', message, re.I)
    if not match or not re.search(r'想|要|买|购买|申购|推荐|看看', message):
        return intent
    if intent.intent not in ('products', 'purchase', 'explain', 'unknown') or intent.product_id:
        return intent
    return intent.model_copy(update={'intent': 'products', 'risk': int(match.group(1)), 'product_id': None})


def route_named_product(intent, message, catalog):
    """Resolve a complete product name against the trusted local catalog."""
    if intent.intent not in ('products', 'purchase', 'explain', 'unknown'):
        return intent
    if not re.search(r'买|购买|申购|看看|看下|了解|查询|介绍|推荐|收益|风险|起购|能投|怎么样|适合', message):
        return intent
    compact = re.sub(r'\s+', '', message)
    matches = [product for product in catalog
               if re.sub(r'\s+', '', product['name']) in compact]
    if not matches:
        return intent
    product = max(matches, key=lambda item: len(item['name']))
    kind = 'purchase' if intent.intent == 'purchase' and intent.amount is not None else 'products'
    return intent.model_copy(update={'intent': kind, 'risk': product['risk'],
                                     'product_id': product['id'],
                                     'context_mode': 'new', 'context_topic_id': None,
                                     'context_message_id': None})


def route_named_product_followup(intent, message, active, catalog):
    """Keep the last named product for an elliptical purchase question."""
    if intent.intent not in ('products', 'purchase', 'forecast', 'explain', 'unknown'):
        return intent
    compact = re.sub(r'\s+', '', message)
    if any(re.sub(r'\s+', '', item['name']) in compact for item in catalog):
        return intent
    if not active or active['kind'] != 'products':
        return intent
    if re.search(r'R\s*[1-5]|其他|别的|换一款', message, re.I):
        return intent
    if not re.search(r'买|购买|申购|能投', message):
        return intent
    if not re.search(r'现在|这款|这个|刚才|之前|重新评估|评估后|可以|能|吗|么|[？?]', message):
        return intent
    product_id = json.loads(active['state_json']).get('product_id')
    product = next((item for item in catalog if item['id'] == product_id), None)
    if product is None:
        return intent
    kind = 'purchase' if intent.intent == 'purchase' and intent.amount is not None else 'products'
    return intent.model_copy(update={'intent': kind, 'risk': product['risk'],
                                     'product_id': product['id'], 'context_mode': 'continue',
                                     'context_topic_id': active['id'],
                                     'context_message_id': None})


def insists_on_risk(message):
    return bool(re.search(r'就是|还是|仍然|非要|一定要|坚持|就要|就想', message))


def suitability_response(c, requested_risk, previous_blocked_risk=None, message='', account_id=db.DEMO_ACCOUNT_ID):
    capacity = snapshot(c, account_id)['riskLevel']
    if requested_risk <= capacity:
        return None
    evidence = {'reply_source': 'suitability_rule', 'requested_risk': requested_risk,
                'capacity': capacity, 'eligible_risk': capacity}
    if previous_blocked_risk == requested_risk and insists_on_risk(message):
        return AgentResponse(
            text=f'你目前的风险承受能力是 C{capacity}，仍无法申购 R{requested_risk} 产品。如果想购买，请先重新评估风险承受能力。',
            response_mode='navigate', actions=[Action(label='重新评估', path='/risk-assessment')],
            evidence=evidence)

    alternatives = products(c, capacity, account_id=account_id)['products'][:2]
    text = f'你的承受能力是 C{capacity}，R{requested_risk} 产品无法申购。'
    actions = []
    if alternatives:
        lines = [f"{p['name']}，成立以来年化 {p['rate']:.2f}%，¥{p['min']:,.0f} 起购"
                 + (f"，{p['advantage']}" if p['advantage'] else '') for p in alternatives]
        text += f'\n\n可以看看这{len(alternatives)}款 R{capacity} 可申购产品：\n' + '\n'.join(lines)
        actions = [Action(label='查看理财产品', path='/wealth', mode='products', risk=capacity)]
    return AgentResponse(text=text, response_mode='navigate' if actions else 'direct',
                         actions=actions, evidence={**evidence, 'product_ids': [p['id'] for p in alternatives]})
