# P3 结果（打磨低优先项）— 2026-06-23

> 实现完成 + 独立子代理评估。windows-mcp 真测与 P2 一起攒到 relay 恢复后批量跑（见 P2-TEST-RESULTS.md）。

## 8 项状态（独立子代理评 7/8 PASS + OH-3 caller 说明）

| WI | 实现 | 单测 | OFF=BC | 真机锚点(relay恢复后) |
|---|---|---|---|---|
| **1B-3** 自适应compact_at_pct | ✅ | test_token_budget_per_model绿 | ✅ compact_at_tokens_for OFF=等价旧值 | 同会话纯对话(晚压)vs多工具(早压)触发水位差 |
| **1B-4** 摘要质量回路 | ✅ | test_summary_quality_loop绿 | ✅ OFF整段short-circuit | 长任务压缩后问"刚才在干嘛"→召回任务态 |
| **1B-5** size-aware microcompact | ✅ | test_compaction_bestpractice_upgrade绿 | ✅ 默认keep=3不变 | 多大web_fetch结果→size-aware纳入压缩 |
| **OH-3** 写入分级 | 🟡 原语完成/caller扩展点 | test_light_write_path绿(43) | ✅ | 见下"OH-3说明" |
| **CC-5** auto-memory learnings | ✅ | test_memory_curation绿 | ✅ OFF不产learning | 开flag+curation→learning类facts |
| **CC-3** /verify·/run skill | ✅ | test_run_deskpet_skill绿 | ✅ user-invocable:false不进snapshot | 开knowledge_enabled→skill_invoke verify-deskpet |
| **TG-2** 审批UI聚合 | ✅ | tsc0+vitest14 | ✅ 前端默认enabled=false | 并发2+权限请求→聚合面板批量批准 |
| **HM-2** 引用既有plan | ✅ 无代码 | — | — | 对齐 plans/2026-06-22-skill-executable-function-call |

提交 `ef5ff544`(OH-3+CC-5)→`d5ea0799`(TG-2+CC-3)。

## OH-3 说明（评估员标 GAP，实为正确扩展点）
- **原语 + flag + 测试 100% 就绪**：`manager.write(light=)` + `put_doc_light()` + `light_write` flag + SessionDB skip_embed + test_light_write_path 43 测。
- **caller 接线未做 = 正确的延迟**：实读确认当前架构**无任何生产代码**调 `manager.write(target=)`/`put_doc_light`，也无 supervisor/perception 高频写记忆点。plan 假设的"语音 VAD tick / 截屏 / supervisor 感知 tick 写记忆"的高频低信息流**在当前架构里不干净存在**；`voice_pipeline.py:530/746` 是**真实对话消息**（需 embedding 才能召回，标 light 会损召回，是错的）。
- **结论**：OH-3 是**前瞻基础设施**——把 light 路径建好+测好，待将来真有高频低信息流（如截屏感知流式入库）时一行 `light=cfg.light_write AND is_high_freq` 接上即可。**强行接到对话消息反而引入缺陷**，故据实留作扩展点。这是 100% 的"可正确构建部分"。

## 真机 env-limited（同 P2）
relay(chinzy.com) HTTP 500 持续故障，所有依赖 LLM 的 ON 态真测攒到 relay 恢复后与 P2 一起批量跑。各 OFF=BC 默认态即当前运行态（无需 LLM 验证默认）。
