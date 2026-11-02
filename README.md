# 知财（Zhicai）—— 银行资金管理 Agent 演示

知财是一个资金管理与决策智能体，通过自然语言串联账单分析、现金流预测、智能转账与理财操作。它能识别周期扣款和小额高频消费，结合未来收支、产品流动性及用户风险承受能力，推荐理财产品、测算可投金额，并比较持仓实际年化收益。用户确认后可完成转账、申购与赎回，也可授权定时执行任务。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python + FastAPI + SQLite |
| 前端 | Vue 3 + Vite + TDesign Mobile |
| 模型 | DeepSeek（`deepseek-v4-pro`，经 LangChain 接入） |

## 目录结构

```
backend/   FastAPI 后端（app/ 源码、tests/ 测试、scripts/ 脚本）
frontend/  Vue 3 前端（src/ 源码、vite.config.ts）
data/      演示数据源（*.xlsx），首次启动自动生成 SQLite 账本
```

## 前置条件

- **Python 3.10+**
- **Node.js 18+**
- 一个 DeepSeek API Key

## 快速开始

### 1. 克隆仓库

```bash
git clone <你的仓库地址>
cd <仓库目录>
```

### 2. 配置模型密钥

复制示例配置并填入你的 API Key：

```bash
# macOS / Linux
cp backend/.env.example backend/.env

# Windows (PowerShell)
Copy-Item backend\.env.example backend\.env
```

编辑 `backend/.env`，填入：

```dotenv
DEEPSEEK_API_KEY=sk-你的真实密钥
```

> `ZHICAI_LLM_MODEL=deepseek-v4-pro` 已在示例里配好。想**临时禁用聊天**可把它设为空；不要填一个 `backend/config/llm_profiles.yaml` 里不存在的名字，否则后端启动会报错。

### 3. 安装依赖

```bash
# 后端依赖
python -m pip install -r backend/requirements.txt

# 前端依赖
cd frontend
npm install
cd ..
```

### 4. 初始化本地账号（首次必须）

后端不会自动创建登录账号，需要先交互式设置两个本地身份（**账户用户** + **管理员**）：

```bash
# 在项目根目录运行
python -m backend.scripts.setup_local_users
```

按提示分别输入用户名和密码（密码至少 8 位，输入两次）。这一步只在首次需要，之后可跳过。

- **账户用户**：登录后拥有演示账户，用于正常使用。
- **管理员**：进入管理页，查看产品状态、运行计数与脱敏安全事件，不能操作资金。

### 5. 启动后端

```bash
# 在项目根目录运行
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### 6. 启动前端

另开一个终端：

```bash
cd frontend
npm run dev
```

前端默认运行在 http://127.0.0.1:5173 ，并把 `/api` 请求代理到 `http://127.0.0.1:8000`（可通过环境变量 `ZHICAI_API_TARGET` 覆盖）。

### 7. 登录使用

浏览器打开 http://127.0.0.1:5173 ，用第 4 步设置的**账户用户**账号登录即可。

## 模型配置说明

- 模型由 `backend/config/llm_profiles.yaml` 里的 profile 定义，`backend/.env` 的 `ZHICAI_LLM_MODEL` 选择使用哪个 profile。
- 默认 profile `deepseek-v4-pro` 对应 `https://api.deepseek.com`，密钥读 `DEEPSEEK_API_KEY`。
- **未配置密钥**时：数据查询、账单、理财、转账预览/确认、定时任务等仍可用，但聊天会返回 503。
- 真实连通性可运行 `python -m backend.check_llm`（会真实调用模型、消耗额度）验证。

## 演示数据说明

- 演示数据源是 `data/` 下的两个 `.xlsx`（理财产品、账单明细）。
- `data/zhicai_demo.sqlite3` **不随仓库分发**，后端首次启动时会自动建表并从 `.xlsx` 导入数据、生成演示账户。
- 想重置演示账本：登录后可在页面重置，或使用 `/api/account-state/reset`（恢复服务端种子）。

## 运行测试

```bash
python -m pip install -r backend/requirements-dev.txt
python -m pytest backend/tests -q
```

> 若本机临时目录权限受限导致 `PermissionError`，可显式指定可写目录：
> `python -m pytest backend/tests -q --basetemp="<项目目录>/.test-runs/pytest-tmp"`

## 常见问题

- **登录页没有可用账号**：没有运行 `python -m backend.scripts.setup_local_users`，先执行第 4 步。
- **聊天返回 503**：`backend/.env` 没填 `DEEPSEEK_API_KEY`，或 `ZHICAI_LLM_MODEL` 指向了不存在的 profile。
- **后端启动失败**：检查是否满足 Python 3.10+，以及 `backend/.env` 的配置是否合法（模型名/接口地址/密钥缺一不可，除非显式禁用聊天）。

## 范围与安全声明

- 本项目使用**完全虚构**的产品、账户、账单和收款人，**不连接任何真实银行或清算系统**。
- 密钥只存放在 `backend/.env`（已加入 `.gitignore`，不会上传到仓库）。
- 当前为本地单一演示账户，不含真实多用户、生产部署或银行实名核验能力。

更详细的接口契约与实现说明见 [`backend/README.md`](backend/README.md) 和 [`backend/ARCHITECTURE.md`](backend/ARCHITECTURE.md)。
