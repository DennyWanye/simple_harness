# 绿色基线（2026-07-27）

## 工作树边界

- 当前工作树在本 slice 开始前已有 Harness observability、context usage history、
  frontend Harness Inspector 等用户修改。
- 本 slice 必须以窄 patch 合并，不能覆盖这些 dirty 文件中的既有改动。
- 本计划新增文件：
  `acceptance.md` 的 AC-IT section、`ARCHITECTURE/ARCHITECTURE.md` 的 §17.4、
  `plans/2026-07-27-single-run-context-and-authorization/`。

## 聚焦测试基线

命令：

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/harness_simplification/test_model_workflow_spawn.py `
  backend/tests/harness_simplification/test_wi5_react_driver.py `
  backend/tests/harness_simplification/test_product_turn_components.py `
  backend/tests/harness_simplification/test_execution_projector.py `
  backend/tests/companion/test_growth_signals.py `
  backend/tests/companion/test_turn_authority.py `
  backend/tests/test_tool_capability_resolver.py -q
```

结果：

```text
164 passed in 25.13s
```

## 旧耦合快照

在以下范围搜索：

```text
IntentTriage|IntentCard|problem_pipeline|chat_v2_intent|
chat_v2_contradiction|pipeline_problem_type|pipeline_needs_investigation
```

当前命中 165 行。实现完成后的目标不是机械清空所有历史文字，而是：

- 生产代码不再导入/构造 IntentTriage/IntentCard；
- 不再产生旧 websocket 事件和 prompt 注入；
- AgentLoop 不再以旧 pipeline 字段决定取证/自检；
- 保留历史 migration/plan 中不可执行的事实记录不算生产耦合。

