# 知财后端实现说明

对应《知财-Agent后端架构方案.md》，本地单一虚构账户版本。

模型接入补充：现已选择 deepseek-v4-pro，使用 langchain-deepseek 的 ChatDeepSeek，服务地址 https://api.deepseek.com，非思考模式。第一阶段结构化提取意图，第二阶段直接生成自然语言，不强制回复满足JSON结构或重复参考答案的全部数字。允许证据中的自然数字，兼容证据占位符；发现无法核实数字时最多修正一次，再失败则给出业务查询结果，并显式标记evidence_fallback。通常两次API，修正时最多三次。这限制数字来源，不等同于完整语义事实验证。金融操作和用户确认继续由后端控制。

## 模块与责任

- `main.py`：启动入口，兼容项目根目录 `backend.main:app` 与 backend 目录 `main:app`。
- `app/config.py`、`llm_client.py`：加载 backend/.env 和 YAML profile，构造 LangChain `ChatOpenAI.with_structured_output(Intent)`。
- `app/agent/schemas.py`：Pydantic 意图槽位、响应模式、动作白名单。
- `app/agent/chat.py`：统一会话上下文调度、参数追问、结果组装、消息及审计事务化持久化。
- `app/agent/context.py`：话题解析、暂停草案恢复、相关历史/回答召回、按估算Token预算组装完整消息轮次。
- `app/services/conversations.py`：会话/话题状态、结构化摘要、消息来源关联、旧表增量迁移、会话并发控制。
- `app/agent/tools.py`：账单、账户、现金流、产品筛选、当前全部持仓收益五个只读工具。
- `app/services/finance.py`：Decimal 金额、上海日期、月底日程、30天现金流、本金加权持有天数与年化。
- `app/services/commands.py`：操作 schema、预览、确认、风险/余额/产品校验、申赎及任务授权、事务内写账。
- `app/jobs/scheduler.py`：每30秒检查授权任务；服务端运行即有效，与网页无关；遗漏周期记录 skipped，本期失败记录 failed，均不补扣。
- `app/repositories/legacy.py`：原数据库表、导入与兼容数据路由保留在此。产品源刷新使用 upsert，已下架且仍被持仓引用的产品关闭申购并保留。

## 新接口

转账模块：底部导航为首页、账单、理财、转账、我的。`/transfer`提供转账记录/常用收款人两项入口与最近转账对象，子页为`/transfer/records`、`/transfer/payees`。联系人属于本地演示账户，在同账户不同会话间共享。

`app/services/payees.py`管理saved_payees、transfer_records及定时任务的payee_id/payee_version。GET/POST `/api/payees`、GET/PUT/DELETE `/api/payees/{id}`支持保存、编辑与移除；列表只返回账号尾号，编辑详情按需读取完整账号。GET `/api/transfers`读取最近200条实际账本转账快照，GET `/api/transfers/recent`按时间去重返回最近12个对象。历史姓名/银行/尾号快照独立保留，编辑或移除收款人不改写已发生记录。旧转账从原账本迁入，不猜测缺失账户或创建虚假联系人。

智能转账先匹配保存的名称/账号/尾号，再由后端构造含payeeId/payeeVersion的确认动作。未保存收款人提供添加入口，重名/尾号不唯一时要求用户选择，多账号消息不猜目标。保存联系人本身不执行转账，首次向该账户转账在确认框提示核对。预览绑定具体联系人版本；确认或定时执行时再次验证，变更/移除后原预览不可继续。开户信息更新或移除会暂停关联运行中任务，需重新授权；旧的姓名任务没有绑定账户时不执行，页面提示补充。昵称修改不改变账户版本。

转账输入中的连续长账号和常见分组卡号在进入意图模型前脱敏，模型索引仅包含姓名、银行和尾号；是否允许付款以服务端保存的具体账户为准。联系人记录一致性校验在本地完成，尚未接银行实名核验或真实汇款接口。

会话相关请求携带 `X-Conversation-Id`。页面首次调用 `POST /api/conversations`，将返回ID保存在localStorage，刷新继续使用。`adopt_legacy=true`只允许首次建立的会话接收旧的default历史；后续会话从空历史开始。兼容未带Header的旧客户端，使用default会话。本地仍为单一演示账户，Header不是身份认证或跨用户授权机制。

`GET /api/conversation/context`返回当前话题、结构化摘要和revision。`POST /api/chat`支持可选reply_to_message_id；模型可输出context_mode（new/continue/resume）和上下文中已有的context_topic_id/context_message_id，所有引用在本会话内校验。多个暂停草案无法区分时由后端询问，不猜金额或授权。待确认操作可恢复核对卡片，确认成功的对应草案标记completed。

消息和审计按conversation_id隔离。页面解读仍是正式agent消息，parent_message_id关联原回答，topic_id关联话题；用户查看较早气泡时更新当前话题。历史审计保留原计算快照，referenced_answer仅代表当时结果，本轮工具结果代表当前查询；账户状态改变时分别解释。

上下文包含当前话题、相关/暂停话题、结构化摘要、近期完整消息轮次和按中文片段/金额/实体召回的较早回答。使用LangChain count_tokens_approximately按中文保守系数估算，默认8000 Token总输入目标，优先保留最近完整轮次和相关历史。必要系统规则/工具证据优先，过大时压缩历史而不裁切事实；这不是DeepSeek精确分词或输入长度的硬上限。摘要由话题槽位、缺失字段和证据引用确定性生成，保存最近24个话题摘要；更早话题和原消息仍可检索，不额外调用摘要LLM，不依赖LangGraph或向量数据库。

同会话请求进程内串行，写入时校验revision；清空、来源气泡切换或确认操作使状态变化，过期请求不覆盖新状态。SQLite中的conversations/conversation_topics及扩展列保存状态，重启可恢复；不把对话文本或摘要视为金融操作授权。

| 方法/路径 | 用途 |
|---|---|
| POST `/api/chat` | `{message}`；返回 text、response_mode、actions、evidence |
| GET `/api/finance/overview` | 同一快照的账户、现金流、收益分析 |
| POST `/api/operations/preview` | 校验操作并保存五分钟有效的预览 |
| POST `/api/operations/{preview_id}/confirm` | `{confirmed:true, buffer_acknowledged:false}` |
| GET `/api/tasks/runs` | 最近100条 executed / failed / skipped 执行记录 |
| GET `/api/agent/audit` | 最近100条意图、工具结果与操作回执 |

操作 kind 支持 purchase、redemption、transfer、task、cancel_task、risk。传输金额单位为元，最多两位小数；内部业务计算用 Decimal，兼容数据库原有 REAL 列。重置接口只恢复服务端固定种子，忽略客户端账户内容；旧 `PUT /api/account-state` 返回410，禁止前端快照覆盖服务端账本。初始化也只使用服务端种子。

## 确认与幂等

预览存储规范化命令、账户与产品快照哈希、过期时间和低缓冲警告。确认使用 `BEGIN IMMEDIATE`，重新读取并校验数据；参数绑定预览，不能在确认接口替换。账户或产品变化时409，需重新预览。单次申购低于警戒线时要求卡片再次确认；超风险、关闭、余额不足、低于起购金额始终拒绝。

preview_id 同时是幂等键；成功回执与账户更新在同一事务提交，重复请求返回原回执。任务用 `(task_id, run_date)` 唯一约束和同一写锁去重。前端保存待重试预览，避免网络故障后直接重新创建扣款。

赎回减少批次本金，按比例实现收益，本金与已实现收益一起增加余额；清仓后不残留持仓收益。演示初始持仓保留固定持有期覆盖值，新批次使用服务端买入日期。定时预测采用预设产品可赎回状态，演示版无真实结算延迟/净值更新。

## 模型与运行

复制 `backend/.env.example` 为 `backend/.env`，在 `backend/config/llm_profiles.yaml` 填实际模型名和服务地址，再设置 profile 与对应密钥。配置指定但不存在、密钥缺失或参数不合法时启动失败；未选择 profile 时数据/操作接口可用，聊天503。不存在规则替代模型的隐藏回退。

模型输出只用于意图槽位，最终金融数值与页面气泡由工具结果填充。函数调用格式的兼容性需用实际服务验证；也可在 profile 中明确选择 json_schema。未调用真实模型的自动测试不等同于模型提供方兼容性验证。

根目录运行：`python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`。后台调度随该进程启动/停止；后端关闭期间不执行，恢复后记录错过的周期。当前无真实银行、登录、多用户或共享部署配置。

## 验证

`python -m pytest backend/tests -q` 使用临时数据库，不改演示账户；覆盖确认幂等/并发、状态过期、资金缓冲、风险、现金流样例、收益入账、任务去重、模型失败、参数追问、产品源更新。

浏览器回归使用 `backend.tests.demo_server`，在测试模块内注入假的结构化意图；单独端口18000/15180、临时数据库。测试逻辑位于 `backend/tests/browser_flow.py`。真实聊天需配置模型后单独验收。
