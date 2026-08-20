# S-7 Cross-layer contract and legacy regression

**绑定**: TO-R1 (AC-1/AC-3), TO-A6 (AC-6)
**方式**: deterministic contract + full-surface smoke
**状态**: PASS（backend/frontend contract and regression smoke）

1. 运行 backend public snapshot contract tests 与 frontend normalization/typecheck，断言字段名、status 单位、`root_run_id`/event identity、`context_visibility=exclude` 在两层一致。
2. 运行 full-surface smoke，至少覆盖旧消息流、工具轨迹、Artifact 卡、权限卡、停止/空闲状态和 Session 切换；legacy V3/缺 activity 字段必须可渲染。
3. 打开/关闭 Inspector 前后比较 conversation history 与 provider payload digest。
   预期：仅 UI projection 改变，history/payload 不新增 timeline、Inspector、diagnostic、correlation 或 hidden reasoning。

证据要求：gate `record-run --exec` 自动保存命令 stdout/stderr；primary 日志写入 `.local-test-evidence/<date>/S-7/`，失败项列出实际字段/场景和 digest。
