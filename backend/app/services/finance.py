"""All financial arithmetic and calendar rules live here, not in the model/UI."""
import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from fastapi import HTTPException


def today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def money(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise HTTPException(422, "金额必须为有限数字")
    return result.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def monthly(task):
    return money(task["amount"]) / {"monthly": 1, "quarterly": 3, "semiannual": 6}.get(task["frequency"], 1)


def occurs(task, on):
    if task.get("status") != "运行中" or not task.get("authorized") or task["frequency"] == "weekly":
        return False
    anchor = date.fromisoformat(task["authorizedAt"])
    interval = {"monthly": 1, "quarterly": 3, "semiannual": 6}[task["frequency"]]
    requested = task.get("executionDay") or anchor.day
    first = anchor.replace(day=min(requested, calendar.monthrange(anchor.year, anchor.month)[1]))
    months = (on.year-anchor.year)*12 + on.month-anchor.month
    if first < anchor:
        months -= interval
    return on >= anchor and months >= 0 and months % interval == 0 and on.day == min(requested, calendar.monthrange(on.year, on.month)[1])


def forecast(state, products, start=None, days=30):
    start = start or today()
    active = [t for t in state["tasks"] if t.get("status") == "运行中" and t.get("authorized") and t["frequency"] != "weekly"]
    fixed = sum((monthly(t) for t in active if t["type"] == "fixed_expense"), Decimal(0))
    daily = max(Decimal(0), money(state["monthlyExpenses"])-fixed)/30
    balance = money(state["balance"])
    projected = {h["productId"]: money(h["principal"]) for h in state["holdings"]}
    catalog = {p["id"]: p for p in products}
    points = []
    for offset in range(days+1):
        on = start + timedelta(days=offset)
        income, events = Decimal(0), []
        if offset:
            balance -= daily
            if on.day == min(state["salaryDay"], calendar.monthrange(on.year, on.month)[1]):
                income = money(state["monthlySalary"])
                balance += income
        for task in active:
            if not occurs(task, on) or task.get("lastRun") == on.isoformat():
                continue
            value = money(task["amount"])
            if task["type"] == "fixed_expense":
                balance -= value
                events.append({"name": task["name"], "amount": float(value)})
            elif task["type"] == "investment":
                p = catalog.get(task.get("productId"))
                if p and p["open"] and p["risk"] <= state["riskLevel"] and value >= money(p["min"]):
                    balance -= value
                    projected[p["id"]] = projected.get(p["id"], Decimal(0))+value
            else:
                ids = sorted(projected) if task.get("productId") == "all" else [task.get("productId")]
                for pid in ids:
                    held = projected.get(pid, Decimal(0))
                    if not catalog.get(pid, {}).get("redeemable"):
                        continue
                    take = min(held, value) if task.get("productId") == "all" else value if held >= value else Decimal(0)
                    balance += take
                    projected[pid] = held-take
                    value -= take
        points.append({"day": offset, "date": on.isoformat(), "balance": float(money(balance)),
                       "income": float(income), "fixedExpensesToday": events})
    minimum = min(p["balance"] for p in points)
    reserve = state["reserveAmount"]
    return {"points": points, "minimum": minimum, "reserve": reserve,
            "cap": float(max(Decimal(0), money(minimum)-money(reserve))),
            "gap": float(max(Decimal(0), money(reserve)-money(minimum))),
            "fixedMonthly": float(money(fixed)),
            "recurring": float(money(sum((monthly(t) for t in active if t["type"] == "investment"), Decimal(0))))}


def performance(state, products, on=None):
    on = on or today()
    catalog = {p["id"]: p for p in products}
    result = []
    for h in state["holdings"]:
        if h["principal"] <= 0:
            continue
        capital_days = sum(money(l["principal"])*max(0, l.get("holdingDays", (on-date.fromisoformat(l["purchaseDate"])).days)) for l in h["lots"])
        result.append({"productId": h["productId"], "name": catalog[h["productId"]]["name"],
                       "principal": h["principal"], "profit": h["profit"],
                       "days": float(capital_days/money(h["principal"])),
                       "annualized": float(money(h["profit"])/capital_days*365*100) if capital_days else None})
    return result
