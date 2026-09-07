# 记忆类型提议的失败与取消审计

最后更新：2026-09-06。后继源码 `c39b2569`，必要验证通过；源码经Dirac限定ACCEPT，913b3d76的证据亦经Dirac限定ACCEPT。

原先只有成功路由保留模型的长期类型及短期选择；后继在实际 Host 路由账本写入处统一生成严格枚举/布尔投影，失败、超时和提交路由前的召回取消也保留该投影。非法提案只记录原提案哈希及稳定验证码，不复制未知字段、查询原文或 Provider 异常文本。取消保持 CancelledError，不转成成功路由。

初版 `bafb0cbf` 已运行新增8项与受影响的原模型选择、路由邻居，共36 passed / 5.13s。运行器为145资源入口，默认共享锁、2GiB/180秒；PGID31632，资源耗时5.589s，峰113760KiB，退出0且无剩余进程。独审随后指出 asyncio.wait_for 的2秒只是发出取消的截止时间，仍须等待 SQLite 回滚/关闭，不能称物理硬限额。后继为取消审计调用单独设置 busy_timeout=0，避免正常5秒写锁排队；其他调用保留既有等待策略，并仍等待自有连接清理。

在b34阶段新增真实第二连接持写锁的竞争检查及提交后审计失败边界检查，当时后继测试尚未运行；后继c39实际结果见下节。原36项不冒充后继测试。

边界：身份获取前没有可绑定的 Run/effect；进程强杀、磁盘故障、取消发生在路由提交阶段和 Service 非持久流不由本改动闭合。取消审计失败会输出安全的 coverage-unavailable 元数据，不能称其记录已持久化。路由决策与调用审计仍是两次事务，后者失败时前者可能已提交；不能由 rejected 审计推断所有路由决策不存在。本改动不是全 Agent 操作已记录、SDK standalone、真实 Provider 或原生 UI 证明。

原始证据留本树 `.local-test-evidence/2026-09-06/recall-failure-audit/r1/{command.log,resource.json}`，不提交原始日志。固定后继结果与哈希见下节。

## 后继验证与修复结果

固定c39b2569必要复验 **32 passed / 3.94s**：12项本功能与连接故障检查，加原路由与Context账本邻居。包含真实取消、二次取消、超时、审计存储故障、真实第二连接持写锁、实际SQLite连接/初始化PRAGMA期间取消与worker关闭。没有重跑未受影响的首轮全部36项，也未将两个批次累加成新全量。

第二次独审发现连接初始化在调用者进入finally前取消会失去连接归属。现在Host先持有aiosqlite连接与连接任务，等待已启动的物理连接完成，初始化失败/取消时在内部关闭；重复取消不会脱离自有cleanup。真实aiosqlite0.22.1 worker门控反例分别覆盖连接已打开但尚未交付、foreign_keys已执行但初始化未结束两个窗口，验证真实close及线程结束。测试私有线程/connector仅用于故障注入，产品不依赖这些属性。

r2使用相同145资源入口与默认共享锁、2GiB/180秒；PGID32152，峰99584KiB，耗时4.513s，exit0，无残留/cleanup error。没有调用真实Provider、模型或原生UI。旧18ec原生bundle仍只有既有构建及启动前阻断证据，未因本后端补丁重标为已测。

已执行r2子命令（外层统一资源入口，PYTHONPATH仅Host backend，PYTHONDONTWRITEBYTECODE=1与PYTEST_DISABLE_PLUGIN_AUTOLOAD=1）：
`<本树primary-candidate venv>/bin/python -m pytest backend/tests/sdk_adapters/test_recall_selection_failure_audit.py backend/tests/sdk_adapters/test_context_route_tool.py backend/tests/sdk_adapters/test_context_authority_primitives.py -q -p no:cacheprovider -p pytest_asyncio.plugin`。

| 本树忽略目录相对证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-06/recall-failure-audit/r1/command.log` | `06fbd260e6dad3d7a55a891bcfbc8beb60af33a65059b81f3e834095a2e7c944` |
| `.local-test-evidence/2026-09-06/recall-failure-audit/r1/resource.json` | `67b4c005358122c05e2c3e983630675773cbfa89514d4db5176fd852b37bd2bb` |
| `.local-test-evidence/2026-09-06/recall-failure-audit/r2/command.log` | `d3dd2a4ebde232940a4351af66f653f1d85365e74cd0bd0ccb09100db3033863` |
| `.local-test-evidence/2026-09-06/recall-failure-audit/r2/resource.json` | `4fa98a14b8ea71765a447595e25bf3ca96a1e955df31518275e1475b59ca39be` |
