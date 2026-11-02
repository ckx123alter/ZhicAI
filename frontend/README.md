# 知财前端

Vue 3 / TypeScript / Vite / Pinia / Vue Router / TDesign Mobile Vue / ECharts。

从项目根目录启动 `启动知财.cmd`。独立开发时先启动 FastAPI，再在 frontend 运行 `npm ci`、`npm run dev -- --port 5180`。生产构建：`npm run build`。

前端负责输入、页面、角色动画、卡片与确认；意图理解、金融计算、申赎、风险档案、任务执行与持久化由后端负责。SQLite 为持久化来源；聊天与气泡使用后端同一结构化结果。后台每30秒执行授权任务，前端每分钟刷新账户视图。

模型配置见 backend/README.md。未配置模型时聊天明确提示不可用，手动查看账户、产品和模拟申赎仍可使用。网页数据均为虚构演示信息。

申赎与任务通过预览/确认接口；卡片改金额或操作对象后重新预览。单笔申购低于现金警戒线需再次确认；风险超限、余额不足、起购额和产品关闭由服务端硬拦截。风险问卷10题评分在服务端确认后更新。

Vite 默认代理 /api 至 http://127.0.0.1:8000；ZHICAI_API_TARGET 可覆盖测试后端地址。测试脚本 backend/tests/browser_flow.py 使用隔离服务，不应连接用户正在使用的演示库。

