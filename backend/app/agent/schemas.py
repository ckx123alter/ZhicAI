from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field


class Intent(BaseModel):
    """Extract intent and explicit slots only. Missing information stays null."""
    model_config = ConfigDict(extra="forbid")
    intent: Literal["bills", "account", "forecast", "products", "performance", "purchase", "redemption", "transfer", "task", "explain", "unknown"]
    context_mode: Literal['auto','new','continue','resume'] = Field(default='auto', description='新话题填new，同一话题追问填continue，恢复之前暂停话题/草案填resume；引用ID只能从后端提供的上下文中选。')
    context_topic_id: str | None = Field(default=None,max_length=80,description='当前追问或恢复所指的话题ID，只能使用上下文中已有ID；新话题为null。')
    context_message_id: str | None = Field(default=None,max_length=100,description='追问某条回答或气泡时，引用其消息ID或evidence_message_id；不能编造ID。')
    include_forecast: bool = Field(default=False, description='产品查询同时问最多能投多少、可投入上限或要求现金流预测时为true；只筛选产品为false。仅现金流问题直接用forecast意图。')
    forecast_focus: Literal['summary', 'investment_limit'] = Field(default='summary', description='追问可投入金额为什么是某数、怎么计算或上限依据时填investment_limit，并使用forecast意图；一般现金流查询填summary。')
    month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    comparison_months: list[Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]] = Field(
        default_factory=list, max_length=12, description='账单对比涉及的全部月份，YYYY-MM；month保留被比较的目标月。追问“和8月9月比”时沿用原目标月，并将8月、9月填入此列表。')
    category: Literal['餐饮', '购物', '出行', '生活用品', '其他'] | None = Field(
        default=None, description='账单分类追问，如拆解餐饮支出时填餐饮；不筛分类则为null。')
    bill_focus: Literal['summary', 'frequent', 'recurring', 'transactions', 'advice'] = Field(
        default='summary', description='账单问题关注点：高频/常去/重复消费填frequent，周期扣费填recurring，明细填transactions，节省建议填advice。')
    amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    execution_day: int | None = Field(default=None, ge=1, le=31)
    risk: int | None = Field(default=None, ge=1, le=5, description='用户明确选择的产品R档；未说明时为null，不能用账户C档或默认R1代替。同话题明确续问时可由后端继承已确认选择。')
    product_id: str | None = Field(default=None, max_length=100)
    payee: str | None = Field(default=None, max_length=30)
    account_number: str | None = Field(default=None, pattern=r'^\d{8,32}$',
        description='完整账号只由后端从用户原话提取；意图模型始终填写null。')
    task_type: Literal["fixed_expense", "investment", "redemption"] | None = None
    frequency: Literal["monthly", "quarterly", "semiannual"] | None = None
    name: str | None = Field(default=None, max_length=60)
    continues_task: bool = Field(default=False, description='用户正在回答最近一个未补齐的定时任务追问时为true；全新任务或其他问题为false。')
    continues_transfer: bool = Field(default=False, description='正在补充最近一个即时转账的金额/收款方时为true，不得将其补充识别为周期任务。')
    recurrence_evidence: str | None = Field(default=None, max_length=120,
        description='从用户当前原话摘取明确要求重复/自动执行的片段，如每月、经常帮我、以后都自动；今天/现在/5号不能作为重复依据。')


class Narrative(BaseModel):
    """Natural-language explanation; cannot provide or alter executable actions."""
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=6000)
    bubble_text: str | None = Field(default=None, max_length=3000)


class Action(BaseModel):
    label: str
    path: Literal["/bills", "/wealth", "/me", "/risk-assessment",'/transfer','/transfer/payees','/transfer/records']
    mode: Literal["products"] | None = None
    modal: Literal["task", "redeem", "buy", "transfer",'payee'] | None = None
    payeeId: str | None = None
    payeeVersion: int | None = None
    manualRecipient: dict[str, str | None] | None = None
    amount: float | None = None
    day: int | None = None
    taskType: Literal["fixed_expense", "investment", "redemption"] | None = None
    taskName: str | None = None
    frequency: Literal["monthly", "quarterly", "semiannual"] | None = None
    payee: str | None = None
    productId: str | None = None
    risk: int | None = None
    month: str | None = None
    bubbleSegments: list[str] | None = None
    resultIds: list[str] | None = None


class AgentResponse(BaseModel):
    text: str
    response_mode: Literal["direct", "navigate", "navigate_with_bubble", "open_action"] = "direct"
    actions: list[Action] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    bubble: dict | None = None
    id: str | None = None
