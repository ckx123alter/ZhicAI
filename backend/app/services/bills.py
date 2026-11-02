"""Auditable category breakdowns from the selected month's ledger rows."""
from decimal import Decimal, ROUND_HALF_UP
from .finance import money


def monthly_insights(rows, previous_rows, previous_month):
    """Comparisons describe this ledger only; no population benchmark is available."""
    total = sum((money(row['amount']) for row in rows), Decimal(0))
    previous_total = sum((money(row['amount']) for row in previous_rows), Decimal(0))
    groups = {}
    for row in rows:
        item = groups.setdefault(row['category'], {'amount': Decimal(0), 'count': 0})
        item['amount'] += money(row['amount'])
        item['count'] += 1
    categories = [dict(name=name, amount=float(item['amount']), count=item['count'],
                       share_pct=float(money(item['amount']/total*100)) if total else 0)
                  for name, item in groups.items()]
    categories.sort(key=lambda item: (-item['amount'], item['name']))
    return {'comparison_scope': 'own_recorded_transactions', 'peer_comparison_available': False,
            'transaction_count': len(rows), 'average': float(money(total/len(rows))) if rows else 0,
            'average_whole_yuan': int((total/len(rows)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if rows else 0,
            'categories_ranked': categories,
            'top_two_share_pct': float(money(sum((money(item['amount']) for item in categories[:2]), Decimal(0))/total*100)) if total else 0,
            'largest_transaction': max(rows, key=lambda row: row['amount']) if rows else None,
            'previous_month': previous_month, 'previous_transaction_count': len(previous_rows),
            'previous_total': float(previous_total) if previous_rows else None,
            'month_change': float(money(total-previous_total)) if previous_rows else None,
            'month_change_abs': float(money(abs(total-previous_total))) if previous_rows else None,
            'month_change_pct': float(money((total-previous_total)/previous_total*100)) if previous_total else None,
            'month_change_abs_pct': float(money(abs(total-previous_total)/previous_total*100)) if previous_total else None}


def category_breakdown(rows, category):
    selected = [row for row in rows if row['category'] == category]
    total = sum((money(row['amount']) for row in selected), Decimal(0))
    month_total = sum((money(row['amount']) for row in rows), Decimal(0))
    grouped = {}
    for row in selected:
        group = grouped.setdefault(row['merchant'], {'amount': Decimal(0), 'count': 0, 'sourceIds': []})
        group['amount'] += money(row['amount'])
        group['count'] += 1
        group['sourceIds'].append(row['id'])
    merchants = [dict(merchant=name, amount=float(value['amount']), count=value['count'],
                      average=float(money(value['amount']/value['count'])), sourceIds=value['sourceIds'])
                 for name, value in grouped.items()]
    merchants.sort(key=lambda item: (-item['amount'], item['merchant']))
    return {'category': category, 'total': float(total), 'count': len(selected),
            'average': float(money(total/len(selected))) if selected else 0,
            'share_pct': float(money(total/month_total*100)) if month_total else 0,
            'merchant_count': len(merchants), 'merchants': merchants,
            'top_transactions': sorted(selected, key=lambda row: (-row['amount'], row['date'], row['id']))[:5],
            'sourceIds': [row['id'] for row in selected]}


def frequent_consumption(rows, category=None, minimum_count=3):
    selected = [row for row in rows if category is None or row['category'] == category]
    grouped = {}
    for row in selected:
        group = grouped.setdefault(row['merchant'], {'amount': Decimal(0), 'count': 0, 'dates': set(), 'sourceIds': []})
        group['amount'] += money(row['amount'])
        group['count'] += 1
        group['dates'].add(row['date'])
        group['sourceIds'].append(row['id'])
    merchants = [dict(merchant=name, amount=float(item['amount']), count=item['count'],
                      average=float(money(item['amount']/item['count'])), active_days=len(item['dates']),
                      sourceIds=item['sourceIds'])
                 for name, item in grouped.items() if item['count'] >= minimum_count]
    merchants.sort(key=lambda item: (-item['count'], -item['amount'], item['merchant']))
    return {'category': category, 'minimum_count': minimum_count, 'merchant_count': len(merchants),
            'transaction_count': sum(item['count'] for item in merchants),
            'total': float(sum((money(item['amount']) for item in merchants), Decimal(0))),
            'merchants': merchants}
