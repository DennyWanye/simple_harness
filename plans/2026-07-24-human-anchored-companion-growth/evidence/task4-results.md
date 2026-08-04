# Task 4 验证结果：双层 PreferenceResolver

> 日期：2026-07-25
> 结论：自动化门通过；Companion 偏好分支完整但保持 dormant，生产仍为 legacy authority。

## 实现事实

- 新增 typed `PreferencePolicy` 与 `PreferenceResolver`。解析顺序为 request scope 优先；
  durable 层内部为显式长期纠正、晋升长期、近期隐式、模型假设。
- 默认晋升阈值来自 `[companion.growth]`，固定为三个独立 `context_key`；重复、衰减、
  tombstone 和 conflict evidence 不计数，存在 live conflict 时阻止晋升。
- `CompanionStore.apply_preference_event()` 在一个 `BEGIN IMMEDIATE` 内提交
  GrowthEvent、preference evidence、状态 CAS、可查询 policy value/hash 的 transition
  audit 和 detail version。同 event 重放不重复增版，异 immutable facts fail closed。
- 解析后的 Run dependency 只包含实际 winner 的 preference key/state version/content
  hash/evidence ids。request-scoped override 以当前 Run 的 version/hash、零 evidence 写入
  growth snapshot，不污染长期状态。
- 测试组合中的 `ProductTurnPreparer` 会真实创建
  `run_growth_snapshots/run_growth_dependency_items/run_growth_dependency_evidence`，并把
  row ref/hash 传给 `PreparedRunContextV1`。
- forget 在原 evidence tombstone 与 lineage invalidation 事务中重算、降级或撤销偏好，
  并以稳定 outbox id 通知；重放不会重复 transition/outbox。
- Companion Facts 分支对 preference 只生成 observation/evidence；legacy FactsStore 默认
  写入行为不变。
- legacy `preference_memory.json` importer 只允许
  `legacy_local_profile/generation=1`，按内容稳定 hash 幂等导入；本 Task 不在 startup 调用。
- `main.py` 只注册 `companion_preference_resolver_dormant`。生产
  `GrowthAuthorityRouter(... companion=None)`、legacy JSON writer 和现有偏好读取路径均未
  切换；正式 cutover 仍属于 Task 13。

## 自动化证据

从仓库根目录运行：

```text
python -m pytest backend/tests/companion/test_preferences.py \
  backend/tests/test_preference_profile_component.py \
  backend/tests/companion/test_growth_authority_router.py -q
72 passed in 5.53s

python -m pytest backend/tests/companion \
  backend/tests/test_preference_profile_component.py \
  backend/tests/test_preference_memory.py \
  backend/tests/harness_simplification/test_product_turn_components.py \
  backend/tests/harness_simplification/test_product_venue_chain.py -q
178 passed in 21.16s

python -m pytest backend/tests/test_deskpet_memory_facts.py \
  backend/tests/test_goal_decision_facts.py -q
62 passed in 15.81s

python -m pytest backend/tests/test_memory_facts_integration.py \
  backend/tests/test_factextractor_content_dedup.py -q
21 passed in 8.71s
```

`git diff --check` 与 Python compile 检查通过。

## 真人 E2E 边界

Task 4 按计划没有生产 cutover：新 resolver 只在测试组合读取/写入，主消息页仍必须使用
legacy preference authority。因此本阶段没有可由主消息 UI 触发的新 Companion 偏好行为；
沿用 Task 3 已验证的真实源码 Tauri 主消息链与 legacy authority 作为生产不变性证据。
Task 13 cutover 后，Task 14/15 必须在主消息页用真实点击验证本次偏好、下一次偏好、遗忘重算
和重启恢复，不能以协议注入替代。

## 清理

- 最终测试命令无残留 pytest/python 子进程。
- backend/Vite 端口 `8100/5173` 无监听。
- 一次过宽 Memory/Facts 组合测试在 120 秒超时且无失败输出；已按精确命令树清理
  PID `18900/21688`，确认 survivor=0，释放 private memory `897,429,504` bytes。
