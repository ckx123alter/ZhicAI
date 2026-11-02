# 知财 Agent 后端测试

本目录是比赛提交的**后端测试用例合集**，全部使用隔离的临时 SQLite 数据库与虚构数据，不读写 `data/` 下的演示账本，不调用真实模型付费接口（真实模型另有连接检查，见下文）。

## 目录结构

| 文件 | 类型 | 覆盖内容 |
| --- | --- | --- |
| `conftest.py` | 共享 fixture | `client`（登录账户用户 + CSRF）、`isolated_narrative`（自动注入确定性文案模型，测试不调真模型） |
| `helpers.py` | 共享辅助函数 | `operation`（预览→二次验证→确认）、`add`、`new_session`、`topics`、`sequence` |
| `test_backend.py` | pytest 单元/集成 | 意图解析、资金预览/确认/幂等/过期、低缓冲二次确认、风险评估、赎回收益、现金流、日历与定时调度、模型失败兜底 |
| `test_context.py` | pytest 集成 | 会话隔离、上下文检索、话题恢复、跨话题草稿、历史快照与当前查询区分、并发与清空 |
| `test_payees.py` | pytest 集成 | 收款人增删改查/脱敏/重复校验、陌生与重名拦截、一次性银行账号转账、定时转账账户绑定 |
| `test_security.py` | pytest 集成 | 权限矩阵（未登录 401、账户用户禁 `/admin`、管理员禁账户操作）、CSRF、登录限速、二次身份验证、定时授权撤销、预览属主隔离 |
| `test_planning_injection.py` | pytest 单元/集成 | 注入回归（角色伪装不越权、聊天不写账、索取提示词兜底）与步骤规划编译 |
| `payee_seed.py` | 辅助 | 测试用虚构收款人种子数据 |
| `demo_server.py` | 辅助 | 浏览器回归专用测试服务（注入固定结构化意图，不调真模型） |
| `browser_*.py` | Playwright 端到端 | 需要先启动 `demo_server.py` 与前端 Vite，验证真实页面链路 |

## 如何运行

后端 pytest（隔离测试库，不调真模型）：

```bash
# 在项目根目录 D:/个人AI项目/银行agent/代码 下
python -m pytest backend/tests -q
```

> 若本机 `%TEMP%` 目录权限受限导致 `PermissionError`，可显式指定可写临时目录：
>
> ```bash
> python -m pytest backend/tests -q --basetemp="D:/个人AI项目/银行agent/代码/.test-runs/pytest-tmp"
> ```

浏览器端到端测试需要额外启动服务，见 `demo_server.py` 与各 `browser_*.py` 的说明，不随 pytest 自动收集。

## 测试设计说明

- **假模型 vs 真模型**：pytest 用例通过 monkeypatch 注入写死的结构化意图与确定性文案，用于验证后端边界（权限、资金确认、证据校验、注入防御）且可复现、不消耗 API 额度。修复对话问题时须同时运行 `python -m backend.check_llm` 和对应真实业务样例；产品名追问运行 `python -m backend.live_product_dialogue`。真实样例用临时 SQLite，不碰演示账本。
- **隔离数据**：每个用例基于 `tmp_path` 建临时 SQLite，用例结束即销毁，不触碰 `data/zhicai_demo.sqlite3`。
- **隐私**：测试只使用虚构账户、虚构账单与虚构收款人，不读取或发送任何真实用户数据。

## 覆盖对照（完整技术方案 §10.1）

1. 权限：`test_security.py` 覆盖 401/403、角色隔离、CSRF、跨属主预览。
2. 资金：`test_backend.py`、`test_payees.py` 覆盖确认前不写账、金额/账户/产品/角色变化后拒绝执行、幂等回执、定时授权撤销。
3. 事实：`test_backend.py`、`test_planning_injection.py` 覆盖错误金额/占比/执行声明被拦截、确定性兜底。
4. 注入：`test_planning_injection.py`、`test_security.py` 覆盖角色伪装、聊天不写账、越权边界。
5. 规划：`test_planning_injection.py` 覆盖缺参/复合/待确认的计划编译；百条真实样例评测见真实模型验收流程。
6. 页面：`browser_*.py` 覆盖登录、导航、转账、理财、任务与审计的浏览器回归。
