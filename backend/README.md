# 知财 Agent 后端

实现细节与接口契约见 [ARCHITECTURE.md](ARCHITECTURE.md)。范围为本地单一虚构账户。

## 启动

根目录双击 `启动知财.cmd`，或先安装 `python -m pip install -r backend/requirements.txt`，再从根目录运行：

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

前端通过 Vite 代理调用 API。再次运行启动器时，若后端源码、模型配置或 .env 比当前 API 进程更新，启动器会自动重启旧进程。Excel 源位于 data/，默认数据库 data/zhicai_demo.sqlite3；ZHICAI_DATABASE_PATH 可指定隔离数据库，兼容旧 ZHICAO_DATABASE_PATH。

## 配置模型

已选择 DeepSeek V4 Pro：profile 与模型标识均为 deepseek-v4-pro（兼容旧profile deepseek），服务地址 https://api.deepseek.com。在 backend/.env 填入 DEEPSEEK_API_KEY 后重启。使用 LangChain ChatDeepSeek；非思考模式分别完成结构化意图和自然语言回复生成。密钥不传入前端或日志。

未选 profile 时数据与模拟交易可用，聊天返回503。指定未知配置、无密钥或配置不合法时启动明确失败；不会切换模型或回退正则聊天。

每轮聊天通常调用模型两次：结构化提取意图 → 后端只读工具 → 直接生成自然语言回复。模型无权生成可执行动作；动作由后端白名单构造。回复可以围绕追问选择事实，不要求重复整月汇总、全部数字或固定字段词；允许写证据里的自然数字，也兼容证据占位符。

定时任务是单独的固定流程：每条消息只调用一次LLM做意图/槽位提取，后端检查用途、金额、周期、执行日及收款方/产品。缺字段就由后端反问，不显示授权按钮；明确继续补充时合并上一份未完成草案，另开新任务不串用旧字段。资料齐全后精确返回“请核对定时任务信息，授权后开始执行。”，下方动作标签“授权定时任务”（前端显示右箭头）。任务追问和确认都跳过文案模型，reply_source=task_rule。点击入口只打开预填卡片；授权确认仍走操作预览/确认接口，聊天不创建任务、不扣款。

即时转账与定时任务分流：今天/现在/本次转账没有重复执行依据，不因日期进入任务流程。“今天给妈妈转钱”先固定追问本次金额，补齐后提供“确认转账”入口并预填即时转账卡片；聊天不扣款。转账追问与确认只调用意图模型，reply_source=transfer_rule。只有明确重复/定期执行意图才进入任务流程；“经常/总是”不推定为每月，继续追问具体周期和执行日。后端对模型误判作分流保护，即时与周期草案分别保存，否定重复或只转一次不能生成任务授权。

超出功能范围：意图识别为unknown后，后端固定回复“抱歉，知财暂不支持这类请求。”，reply_source=unsupported_rule；不再调用文案模型，不附操作入口。简单问候、功能咨询和相关但缺数据/参数的提问不应归为离题。

发现无法核实的数字/引用时，最多再调用一次模型修正；仍不可靠或回复阶段调用失败时，返回明确标注来源的业务查询结果，reply_source=evidence_fallback，用户仍能获得已核实信息，而不是整条校验503。正常回复source为llm_grounded，修正后为llm_grounded_repaired。此数值来源检查不等同于完整的语义事实验证。意图阶段不可用仍明确报错，不切换模型。

账单追问支持高频消费、分类、周期扣款证据、明细和节省建议。未指定新月份时沿用最近成功账单月份。高频按所选月份/分类内同一商户至少3笔计算，覆盖所有商户，不再只以固定咖啡品牌为条件。金融概念等相关问题支持explain；数据不足时说明范围并追问。账户写入、交易风险及用户确认校验保持严格。

官方配置依据：https://api-docs.deepseek.com/ 、https://api-docs.deepseek.com/guides/tool_calls/ 。自动测试中的 HTTP mock 不等同于真实 API 联调。

填好密钥后可在项目根目录运行 `python -m backend.check_llm`。该命令实际调用所选模型两次（消耗API额度），验证意图识别和证据回复，不读写数据库或执行交易。只有返回 PASS 才表示这次真实连接检查通过。

修复对话类 bug 时，还需用真实 DeepSeek 跑对应的多轮业务样例，核对落库意图、最终回复和动作；仅运行上述连通检查或模拟模型单测不算完成。产品名追问回归可运行 `python -m backend.live_product_dialogue`，它使用临时 SQLite 和虚构账户，不改动演示账本，也不确认交易。

具体产品的购买咨询会先核对账户风险等级，再以该产品的赎回方式计算本轮现金流建议上限。若起购金额高于上限，知财直接提示两者及差额，并提供现金流预测入口。现金流上限是建议值；余额足够且产品开放、起购额与风险等级合规时，用户仍可在余额风险警示后继续申购。金额超过本地高风险阈值时还须完成登录密码验证；前端会保留同一预览的警示及验证要求。仅重新评估风险不会自动提高现金流上限。

## API

新增转账页，位于底部导航“我的”左侧。顶部两个入口为“转账记录”“常用收款人”，下方展示真实账本产生的最近转账对象；支持收款人增改删、按姓名/尾号查找、选择后转账和查看记录。陌生收款人先添加，重名先选择；每笔转账仍需核对具体账户和金额。历史记录按发生时账户信息保留，不随联系人编辑改写。

联系人更新开户资料会使旧预览失效，并暂停相关定时转账；旧的仅姓名定时任务需补充账户后重新授权。数据迁移只新增表/列与历史快照，不向用户数据库注入测试联系人或假转账。仍使用本地单一模拟账户，不提供真实银行实名核验或真实汇款。

- GET/POST /api/payees，GET/PUT/DELETE /api/payees/{id}：常用收款人。
- GET /api/transfers：转账记录；GET /api/transfers/recent：最近转账对象。

已接入统一上下文管理，继续使用LangChain，不引入LangGraph。页面自动建立并持久保存conversation_id，聊天/历史/审计按会话隔离；旧历史由首次页面会话接收。支持跨话题恢复未完成任务或转账、按相关性召回较早回答、关联气泡与原回答证据、区分历史计算和当前查询。历史按估算Token预算保留完整轮次，较早话题形成结构化摘要，原始记录保留；不增加常规模型调用次数。接口与边界见ARCHITECTURE.md。本地演示账户仍共享，尚未接入真实用户认证。

- POST /api/conversations：创建会话，可选adopt_legacy。
- GET /api/conversation/context：读取本会话话题状态与结构化摘要。
- 会话请求Header为X-Conversation-Id；POST /api/chat可选reply_to_message_id，页面解读可选parent_message_id关联原回答。

复合理财查询：产品筛选同时询问可投入上限/现金流时，意图提取设置products及include_forecast=true，后端按顺序返回“查看理财产品”和“查看现金流预测”两个动作。两者复用同一次产品工具查询中的现金流结果，不增加模型调用；仅产品或仅预测的问题保留对应单入口，动作列表在聊天历史中完整持久化。

- GET /api/health、/api/bootstrap、/api/products、/api/products/recommend、/api/bills、/api/categories：兼容数据查询。
- GET /api/account-state：账户、持仓批次、任务、回执。
- GET /api/finance/overview：账户、统一现金流与全部当前持仓收益。
- POST /api/chat：结构化意图、只读工具、追问与白名单动作。
- POST /api/operations/preview：创建操作预览。
- POST /api/operations/{preview_id}/confirm：明确确认、状态复查、事务写账和幂等回执。
- GET /api/tasks/runs、/api/agent/audit：执行记录与审计。
- POST /api/account-state/reset：恢复服务端演示种子并清空聊天、账户变动、预览和执行记录；不删 Excel 数据。
- GET/POST/DELETE /api/conversation/messages：聊天及页面气泡持久化。
- PUT /api/account-state：410，旧客户端不得覆盖服务端账本。

操作示例：

```json
{"kind":"purchase","product_id":"D","amount":"1000.00"}
```

先发送至 preview，再用返回的 preview_id 调 confirm：

```json
{"confirmed":true,"buffer_acknowledged":false}
```

服务端返回现金警戒提示时，卡片再次确认才传 buffer_acknowledged=true。同一 preview_id 重试不会重复扣款；状态变化或五分钟过期需要重新预览。

## 数据和调度

保留 wealth_products、bill_transactions、conversation_messages、accounts、investment_holding_lots、scheduled_tasks、account_transactions、source_state；新增 operation_previews、task_runs、agent_audit。定时任务由后端每30秒检查，网页关闭仍执行；后端停机期间遗漏的周期记录并跳过，不补扣。

C1–C5 限制最高 R1–R5。申购、赎回、转账、任务授权和风险评估均走服务端命令；风险问卷必须完整提交10题。赎回同时结算相应收益。只使用虚构产品、账户和账单，无真实银行接口或用户登录。

## 验证

回复语气：默认先说发现，再给一项具体建议，使用日常中文。后台的证据/校验限制不逐条复述；只有信息缺口影响当前判断时才简短说明。账单气泡和现金流模板已去掉常规防御性尾句，模型不沿用旧回复的生硬套话；计算追问简短解释数字关系后给入口。语气通过模板和提示词调整，不对输出机械删句，也不增加单独的润色模型调用。数字来源、执行授权及用户要求的固定确认文案继续由后端控制。

投入上限追问：在理财推荐后问“为什么是7400”“怎么算的”，使用forecast+forecast_focus=investment_limit。后端从最近审计中选取可核实的历史上限计算依据，并在本轮重新查询现金流，解释最低预测余额扣除预留线的计算过程，附“查看现金流预测”入口。若当前状态已变化，区分上次计算与当前重算，不用新数字替换旧金额原因。模型回复失败时也保留计算依据与入口。纯概念、账单或无相关历史的提问不会仅因存在旧上限而被强制改为现金流查询。

账单页面气泡按消费结构、高频/最大单笔、本人环比与建议分段解读，最后保留明细入口。后端计算分类占比、前两类集中度、笔数、精确/整元均价和本人上月记录变化；LLM在同一次回复中润色首段，未提供解读或模型不可用时保留计算结果生成的分析。没有同类人群基准，不能把支出占比表述成人群排名；本人环比需核对两月导入范围。缺上月非零基数或本月记录时明确说明，不推断变化。

`python -m pytest backend/tests -q`；测试依赖见 requirements-dev.txt。浏览器回归见 tests/browser_flow.py 与测试专用 tests/demo_server.py。浏览器测试注入结构化意图假对象，不代表真实模型调用通过。
