# Black-box fixtures and environment contract

> 这些 fixture 只定义公开行为，不规定实现文件、数据库表名或私有 API。实际 Adapter 必须通过计划中公开的 SDK Port/ConformanceHost 接入。

## Fixture F1 — read-only project summary Tool

- stable name：`project_summary_read`
- input：空对象；额外字段拒绝。
- public output：`{"project":"fixture-project","focus":["tests","release"],"status":"active"}`。
- private canary：由 Host 保存在 Tool 私有执行上下文，只用于 redaction 扫描，绝不能进入模型消息或 evidence 正文。
- counter：记录 handler invocation 与 effect settlement 次数，用于 SDK-S2/S6/S7。

## Fixture F2 — Personal bounded catalog

- `weekly_work_planner`：职责是项目优先级、周计划与复盘。
- `fitness_training_coach`：职责是训练安排与恢复。
- 两者必须各有 stable candidate ID、generation、fingerprint；graph/owner/version 只在 Host 私有绑定中存在。

## Fixture F3 — safe capability gap

- catalog 初始无 `fixture.text.normalize`。
- 在 SDK-S5 的 Product Session 历史中先明确目标是“把临时文本规范化为空白稳定、大小写保持的结果”；required root 的 exact input 仍使用 acceptance 冻结文本，避免靠实现关键词直接触发路由。
- 构建目标只处理本地临时文本，禁止 shell/网络/系统目录写入。
- current-stamp search miss、source hash、build、test、install、activate、rollback 都产生公开 receipt ref。
- 成功后同 Run catalog refresh 可见；重复安装不能产生第二份 active package。

## Fixture F4 — controlled Provider variants

- direct text、single Tool、multi Tool、workflow_spawn 三类正常响应。
- unknown Tool/Profile、missing/extra/wrong-type/enum、stale generation、forged binding。
- duplicate、out-of-order、oversize、refuse-tool、401/402/429/5xx、timeout/cancel。
- 每个 variant 记录物理请求数与脱敏 response-shape hash，不保存完整 body 或 secret。

## Fixture F5 — durable evidence sink

- 可查询 run/session/request/call/effect identity、terminal、child link、provider invocation、effect outcome、delivery outcome。
- 支持 delivery 第一次失败后成功，以及 `confirmed_not_started/completed/still_unknown` reconciliation observation。
- evidence 导出必须是只读、脱敏、JSON-safe；路径仅允许 `.local-test-evidence/...`。

## Environment ownership

- SDK wheel/CLI/runtime：exact release artifact。
- Product Session、消息、UI、Tool handler、权限体验、Presenter：Simple Harness consumer。
- 登录：复用已有有效开发态；不清数据、不截取登录凭据。
- SQLite：每个自动化场景使用独立显式临时路径；UI 使用隔离的 `DESKPET_USER_DATA_DIR`，但不得手动启动 backend。
