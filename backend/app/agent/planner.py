"""Finite, server-owned workflow plans. A model cannot choose executable tools."""
from pydantic import BaseModel, ConfigDict, Field
from uuid import uuid4
from typing import Literal

from .schemas import Intent, AgentResponse

StepName = Literal[
    "collect_details", "query_bills", "compare_bills", "query_account", "query_forecast",
    "query_products_with_cashflow", "query_products", "calculate_limit", "query_performance", "resolve_recipient",
    "check_schedule", "compose_answer", "create_preview", "await_confirmation",
]
StepStatus = Literal["completed", "pending", "blocked"]


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StepName
    status: StepStatus
    depends_on: list[StepName] = []


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(default_factory=lambda: uuid4().hex)
    conversation_id: str | None = None
    account_id: str | None = None
    revision: int = 0
    goals: list[str] = Field(default_factory=list)
    slots: dict = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    goal: str
    status: Literal["answered", "needs_details", "awaiting_confirmation"]
    steps: list[PlanStep]
    missing_fields: list[str] = []


def compile_plan(intent: Intent, response: AgentResponse, *, conversation_id: str | None = None, account_id: str | None = None, revision: int = 0) -> Plan:
    """Describe the actual guarded workflow, never an LLM-supplied tool list."""
    kind = intent.intent
    metadata = {'conversation_id': conversation_id, 'account_id': account_id, 'revision': revision,
                'goals': ['products', 'forecast'] if kind == 'products' and intent.include_forecast else [kind],
                'slots': intent.model_dump(mode='json', exclude_none=True, exclude={'account_number'}),
                'evidence_refs': [str(item) for item in (response.evidence.get('result') or {}).get('sourceIds', [])] if isinstance(response.evidence.get('result'), dict) else []}
    missing = list(response.evidence.get("missing_fields") or [])
    if missing:
        return Plan(**metadata, goal=kind, status="needs_details", missing_fields=missing,
                    steps=[PlanStep(name="collect_details", status="pending")])
    if kind in ("task", "transfer", "purchase", "redemption"):
        first = "check_schedule" if kind == "task" else "resolve_recipient" if kind == "transfer" else "query_products_with_cashflow"
        steps = [PlanStep(name=first, status="completed")]
        if response.actions:
            steps += [
                PlanStep(name="create_preview", status="pending", depends_on=[first]),
                PlanStep(name="await_confirmation", status="blocked", depends_on=["create_preview"]),
            ]
            return Plan(**metadata, goal=kind, status="awaiting_confirmation", steps=steps)
        return Plan(**metadata, goal=kind, status="answered", steps=steps)
    if kind == "products" and intent.include_forecast:
        steps = [PlanStep(name="query_products", status="completed"),
                 PlanStep(name="query_forecast", status="completed"),
                 PlanStep(name="calculate_limit", status="completed", depends_on=["query_products", "query_forecast"]),
                 PlanStep(name="compose_answer", status="completed", depends_on=["calculate_limit"])]
        return Plan(**metadata, goal="products_and_forecast", status="answered", steps=steps)
    if kind == "bills" and intent.comparison_months:
        steps = [PlanStep(name="query_bills", status="completed"),
                 PlanStep(name="compare_bills", status="completed", depends_on=["query_bills"]),
                 PlanStep(name="compose_answer", status="completed", depends_on=["compare_bills"])]
        return Plan(**metadata, goal="compare_bills", status="answered", steps=steps)
    source = {
        "bills": "query_bills", "account": "query_account",
        "forecast": "query_forecast", "products": "query_products_with_cashflow",
        "performance": "query_performance",
    }.get(kind)
    if source:
        steps = [PlanStep(name=source, status="completed"),
                 PlanStep(name="compose_answer", status="completed", depends_on=[source])]
        return Plan(**metadata, goal="products_and_forecast" if kind == "products" and intent.include_forecast else kind,
                    status="answered", steps=steps)
    return Plan(**metadata, goal=kind, status="answered", steps=[PlanStep(name="compose_answer", status="completed")])


def validate_plan(plan: Plan) -> None:
    if len(plan.steps) > 8:
        raise ValueError("plan_step_budget_exceeded")
    seen: set[str] = set()
    for step in plan.steps:
        if step.name in seen or any(dependency not in seen for dependency in step.depends_on):
            raise ValueError("invalid_plan_dependency")
        seen.add(step.name)


def begin_plan(intent: Intent, conversation_id: str, account_id: str, revision: int,
               previous: dict | None = None) -> Plan:
    """Compile before any business query; resume only the same owned goal."""
    plan = compile_plan(intent, AgentResponse(text="待执行"), conversation_id=conversation_id,
                        account_id=account_id, revision=revision)
    if previous and previous.get("status") in ("needs_details", "awaiting_confirmation") and previous.get("account_id") == account_id and previous.get("goal") == plan.goal:
        plan.plan_id = str(previous.get("plan_id") or plan.plan_id)
    validate_plan(plan)
    for step in plan.steps:
        step.status = "pending" if not step.depends_on else "blocked"
    return plan


def execute_read_steps(plan: Plan, intent: Intent, c, tools: dict, account_id: str):
    """Run at most one registered read tool for a turn; it cannot write funds."""
    kind = intent.intent
    if kind not in tools or kind == "products" and (intent.risk is None or intent.product_id):
        return None
    if kind == "products" and intent.include_forecast:
        from .tools import products as registered_products
        from .tools import query_product_catalog, query_cashflow, calculate_product_limits
        if tools["products"] is not registered_products:
            # Keep an explicitly injected allowlisted adapter as the source of truth.
            result = tools["products"](c, intent.risk, account_id=account_id)
            for step in plan.steps[:3]:
                step.status = "completed"
            return result
        catalog, items = query_product_catalog(c, intent.risk)
        plan.steps[0].status = "completed"
        state, projection = query_cashflow(c, catalog, account_id)
        plan.steps[1].status = "completed"
        result = calculate_product_limits(intent.risk, items, state, projection)
        plan.steps[2].status = "completed"
        return result
    from ..repositories import legacy as db
    from ..services.finance import today
    from ..services.commands import snapshot
    argument = (intent.month or today().strftime("%Y-%m")) if kind == "bills" else (
        intent.risk or snapshot(c, account_id)["riskLevel"])
    if kind == "bills":
        result = tools[kind](c, argument, intent.category, intent.bill_focus,
                             intent.comparison_months, account_id=account_id)
    else:
        result = tools[kind](c, argument, account_id=account_id)
    expected = {"bills": "query_bills", "account": "query_account",
                "forecast": "query_forecast", "products": "query_products_with_cashflow",
                "performance": "query_performance"}[kind]
    step = next((item for item in plan.steps if item.name == expected), None)
    if step is None or any(next((s.status for s in plan.steps if s.name == dep), None) != "completed"
                           for dep in step.depends_on):
        raise ValueError("invalid_plan_dependency")
    step.status = "completed"
    if kind == "bills" and intent.comparison_months:
        comparison_step = next((item for item in plan.steps if item.name == "compare_bills"), None)
        if comparison_step is None or not result.get("comparison"):
            raise ValueError("bill_comparison_missing")
        comparison_step.status = "completed"
    return result


def finish_plan(started: Plan, intent: Intent, response: AgentResponse) -> Plan:
    finished = compile_plan(intent, response, conversation_id=started.conversation_id,
                            account_id=started.account_id, revision=started.revision)
    finished.plan_id = started.plan_id
    validate_plan(finished)
    for step in finished.steps:
        if step.status == "completed" and (step.name.startswith("query_") or step.name in ("calculate_limit", "compare_bills")):
            observed = next((s for s in started.steps if s.name == step.name), None)
            if observed is not None and observed.status != "completed" and not response.evidence.get("result"):
                step.status = "pending"
    completed = {step.name for step in finished.steps if step.status == "completed"}
    for step in finished.steps:
        if step.status == "completed" and any(dep not in completed for dep in step.depends_on):
            step.status = "blocked"
    return finished


def advance_operation_plan(c, conversation_id: str, topic_id: str | None, event: str) -> None:
    """Persist preview/confirmation transitions from the guarded API only."""
    if not topic_id or event not in ("preview", "confirmed"):
        return
    import json
    row = c.execute("SELECT plan_json FROM conversation_topics WHERE id=? AND conversation_id=?",
                    (topic_id, conversation_id)).fetchone()
    if not row:
        return
    try:
        plan = Plan.model_validate_json(row["plan_json"])
    except ValueError:
        return
    steps = {step.name: step for step in plan.steps}
    if event == "preview":
        if "create_preview" not in steps or "await_confirmation" not in steps:
            return
        steps["create_preview"].status = "completed"
        steps["await_confirmation"].status = "pending"
        plan.status = "awaiting_confirmation"
    else:
        if "await_confirmation" not in steps or steps.get("create_preview").status != "completed":
            return
        steps["await_confirmation"].status = "completed"
        plan.status = "answered"
    c.execute("UPDATE conversation_topics SET plan_json=? WHERE id=? AND conversation_id=?",
              (json.dumps(plan.model_dump(mode="json"), ensure_ascii=False), topic_id, conversation_id))
