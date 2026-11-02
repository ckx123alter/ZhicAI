"""Read-only tool allowlist. Account identity never comes from model output."""
from ..repositories import legacy as db
from ..services.commands import snapshot
from ..services.finance import forecast, performance
from ..services.investment_limits import investment_limit
from ..services.bills import category_breakdown, frequent_consumption, monthly_insights
from datetime import date, timedelta


def bills(c, month, category=None, focus='summary', comparison_months=None, account_id=db.DEMO_ACCOUNT_ID):
    rows = db.bills_from_db(c, month)
    result = {"month": month, "total": round(sum(x["amount"] for x in rows), 2),
            "categories": db.categories_from_db(c, month), "sourceIds": [x["id"] for x in rows],
            "recurring": [x for x in db.recurring_from_db(c) if x["date"][:7] <= month],
            "frequent": [x for x in rows if x["merchant"] == "瑞幸咖啡" and x["amount"] <= 30]}
    if category is not None:
        result['category_detail'] = category_breakdown(rows, category)
    result['frequency_detail'] = frequent_consumption(rows, category)
    if focus == 'transactions':
        selected = [row for row in rows if category is None or row['category'] == category]
        result['transactions'] = sorted(selected, key=lambda row: (row['date'], row['id']), reverse=True)[:30]
        result['transaction_count'] = len(selected)
        result['shown_count'] = len(result['transactions'])
    result['focus'] = focus
    if focus == 'summary' and category is None:
        previous_month = (date.fromisoformat(month+'-01')-timedelta(days=1)).strftime('%Y-%m')
        result['insights'] = monthly_insights(rows, db.bills_from_db(c, previous_month), previous_month)
    if comparison_months:
        summaries = []
        for target in dict.fromkeys([month, *comparison_months]):
            all_rows = rows if target == month else db.bills_from_db(c, target)
            selected = [row for row in all_rows if category is None or row['category'] == category]
            amounts = {}
            for row in selected:
                amounts[row['category']] = round(amounts.get(row['category'], 0) + row['amount'], 2)
            summaries.append({'month': target, 'available': bool(all_rows),
                              'total': round(sum(row['amount'] for row in selected), 2),
                              'count': len(selected), 'categories': amounts,
                              'sourceIds': [row['id'] for row in selected]})
        baseline = summaries[0]
        differences = []
        for other in summaries[1:]:
            if not baseline['available'] or not other['available']:
                continue
            change = round(baseline['total'] - other['total'], 2)
            differences.append({'compared_month': other['month'], 'change': change,
                                'change_pct': round(change / other['total'] * 100, 2) if other['total'] else None,
                                'category_changes': {name: round(baseline['categories'].get(name, 0) - other['categories'].get(name, 0), 2)
                                                     for name in sorted(baseline['categories'].keys() | other['categories'].keys())}})
        result['comparison'] = {'baseline_month': month, 'category': category, 'months': summaries,
                                'missing_months': [item['month'] for item in summaries if not item['available']],
                                'differences': differences}
    return result


def query_product_catalog(c, risk):
    catalog = db.products_from_db(c)
    items = sorted((p for p in catalog if p["open"] and p["risk"] == risk),
                   key=db.product_advantage_sort_key)
    return catalog, items


def query_cashflow(c, catalog, account_id):
    state = snapshot(c, account_id)
    return state, forecast(state, catalog)


def calculate_product_limits(risk, items, state, projection):
    return {"products": items, "risk": risk, "capacity": state["riskLevel"],
            "cashflow": investment_limit(projection, items),
            "investment_limits": [{**investment_limit(projection, [p]), 'product_id': p['id'], 'name': p['name']} for p in items]}


def products(c, risk, account_id=db.DEMO_ACCOUNT_ID):
    catalog, items = query_product_catalog(c, risk)
    state, projection = query_cashflow(c, catalog, account_id)
    return calculate_product_limits(risk, items, state, projection)


def _account(c, _, account_id=db.DEMO_ACCOUNT_ID):
    return snapshot(c, account_id)


def _forecast(c, _, account_id=db.DEMO_ACCOUNT_ID):
    return forecast(snapshot(c, account_id), db.products_from_db(c))


def _performance(c, _, account_id=db.DEMO_ACCOUNT_ID):
    return performance(snapshot(c, account_id), db.products_from_db(c))


TOOLS = {"bills": bills, "account": _account,
         "forecast": _forecast,
         "products": products,
         "performance": _performance}
