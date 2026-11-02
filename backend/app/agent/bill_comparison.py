"""Resolve explicit comparison months without treating chat history as ledger data."""
import re
from ..services.finance import today


def resolve_bill_comparison(intent, message):
    if intent.intent != 'bills':
        return intent
    months = list(intent.comparison_months)
    if re.search(r'对比|比较|相比|比起|区别|差异|变化|比', message):
        year = int(intent.month[:4]) if intent.month else today().year
        for match in re.finditer(r'(?:(\d{4})\s*[年-]\s*)?(1[0-2]|0?[1-9])\s*月', message):
            if match.group(1):
                year = int(match.group(1))
            months.append(f'{year:04d}-{int(match.group(2)):02d}')
    months = list(dict.fromkeys(months))
    return intent.model_copy(update={'comparison_months': months[:12]})
