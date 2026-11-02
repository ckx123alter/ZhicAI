"""Server-generated references to the facts returned by read-only tools."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvidenceFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str
    fact_type: str
    value: str | int | float | bool | None
    unit: str | None = None
    source: str
    source_record_ids: list[str] = Field(default_factory=list)
    observed_at: str
    account_id: str
    snapshot_id: str


def _unit(path: str) -> str | None:
    key = path.rsplit(".", 1)[-1].lower()
    if key.endswith(("_pct", "percent", "percentage")) or key in ("share", "rate"):
        return "percent"
    if any(word in key for word in ("amount", "balance", "expense", "income", "principal", "profit", "total", "limit")):
        return "CNY"
    if key.endswith(("count", "frequency")):
        return "count"
    return None


def collect_facts(evidence: dict[str, Any], account_id: str, *, max_facts: int = 256) -> dict[str, Any]:
    """Flatten this turn's business result; never accept IDs supplied by a model."""
    result = evidence.get("result")
    snapshot_id = uuid.uuid4().hex
    observed_at = datetime.now(timezone.utc).isoformat()
    source = str(evidence.get("tool") or "business_rule")
    facts: list[dict[str, Any]] = []

    def visit(value: Any, path: str, source_ids: list[str], depth: int) -> None:
        if len(facts) >= max_facts or depth > 12:
            return
        if isinstance(value, dict):
            ids = value.get("sourceIds")
            if isinstance(ids, list):
                source_ids = [str(item) for item in ids[:100] if isinstance(item, (str, int))]
            elif isinstance(value.get("id"), (str, int)):
                source_ids = [str(value["id"])]
            for key, item in value.items():
                if key in ("sourceIds", "id") or "account_number" in key.lower() or "token" in key.lower():
                    continue
                visit(item, f"{path}.{key}" if path else key, source_ids, depth + 1)
        elif isinstance(value, list):
            for index, item in enumerate(value[:100]):
                visit(item, f"{path}[{index}]", source_ids, depth + 1)
        elif value is None or isinstance(value, (str, int, float, bool)):
            raw = json.dumps([path, value], ensure_ascii=False, sort_keys=True)
            fact_id = hashlib.sha256((snapshot_id + raw).encode()).hexdigest()[:24]
            facts.append(EvidenceFact(fact_id=fact_id, fact_type=path, value=value,
                                      unit=_unit(path), source=source,
                                      source_record_ids=source_ids, observed_at=observed_at,
                                      account_id=account_id, snapshot_id=snapshot_id).model_dump())

    if isinstance(result, dict):
        holdings = result.get("holdings")
        if isinstance(holdings, list):
            market_value = round(sum(float(item.get("principal", 0)) + float(item.get("profit", 0))
                                     for item in holdings if isinstance(item, dict)), 2)
            visit(market_value, "derived.holdings_market_value",
                  [str(item["id"]) for item in holdings if isinstance(item, dict) and item.get("id")], 0)
        products = result.get("products")
        if isinstance(products, list):
            visit(len(products), "derived.products_count",
                  [str(item["id"]) for item in products if isinstance(item, dict) and item.get("id")], 0)
    if result is not None:
        visit(result, "result", [], 0)
    return {"snapshot_id": snapshot_id, "observed_at": observed_at, "facts": facts,
            "truncated": len(facts) >= max_facts}


def bind_claims(text: str, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach server fact IDs to each numeric or named business assertion."""
    import re
    from decimal import Decimal, InvalidOperation
    number = re.compile(r"\d{4}-\d{2}(?:-\d{2})?|[+-]?\d[\d,]*(?:\.\d+)?%?")

    def normalized(raw: str):
        if re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", raw):
            return ("date", raw)
        try:
            return ("number", Decimal(raw.replace(",", "").rstrip("%")))
        except InvalidOperation:
            return ("text", raw)

    facts = snapshot.get("facts", [])
    by_value: dict[tuple, list[str]] = {}
    for fact in facts:
        value = fact.get("value")
        if isinstance(value, bool) or value is None:
            continue
        key = normalized(str(value))
        by_value.setdefault(key, []).append(fact["fact_id"])
    bindings = []
    for index, sentence in enumerate(re.split(r"(?<=[。！？；])", text)):
        sentence = sentence.strip()
        if not sentence:
            continue
        numbers = [normalized(match.group(0)) for match in number.finditer(sentence)]
        ids = list(dict.fromkeys(fid for key in numbers for fid in by_value.get(key, [])))[:32]
        if not numbers:
            ids = [fact["fact_id"] for fact in facts
                   if isinstance(fact.get("value"), str) and len(fact["value"]) >= 3
                   and fact["value"] in sentence][:32]
        bindings.append({"claim_id": hashlib.sha256((snapshot["snapshot_id"]+str(index)).encode()).hexdigest()[:24],
                         "fact_ids": ids, "verified": bool(ids) and all(key in by_value for key in numbers)})
    return bindings
