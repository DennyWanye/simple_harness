# 原生 r11：Procedure 使用链重跑（Host a8734fbf，M0.6.25）——步 1–4 通过，步 5 被"中转超时后 Run 停摆"阻塞

2026-09-07 深夜。bundle `SimpleHarness Memory Verify a8734fbfp18120.app`（含 `after` 可选 + 参数拒绝不致 history 不可核验两处修复），installed H0.7.10 / M0.6.25 / S0.3.13，全新 userdata，gpt-5.6-luna，auto 模式，System Events 驱动、无截图。

| 步 | 结果 |
|---:|---|
| 1–2 | 「松柏记录」active/unbound、「云杉归档」draft 落库 ✅（首进程窗口在步 2 后消失，进程仍在、分析照常落库；改用同 userdata 重启继续） |
| 3 | 普通改写直接回答，无模型驱动召回 ✅ |
| 4 | `procedure_discover` 真实返回草稿并拒绝执行 ✅（operation-audit `discover_procedure_drafts` 累计 8 次，`tool_arguments.missing` 0 次——r10 的 schema 缺陷已消除） |
| 5 | `context_route(create_new)` 建「松柏九月」✅ → 模型 3 次工具调用后，一次 provider 请求 **transport_timeout 240s**（`product_provider_attempt_failed … provider_timeout`）→ SDK `reconcile.unknown_settled` + `provider_attempt.degraded` → Run 停在 RUNNING，20+ 分钟零事件；再次冷启动后 `reconcile.recovered` 1 次但仍不推进（5 分钟）。**BLOCKED（产品缺陷 + 环境）**：无 `procedure_use`、无文件 |

结论：r10 的两个缺陷已修并在 r11 验证消失；Procedure 使用链仍未走通，新阻塞点是 **provider 传输超时后的 Run 恢复**（记 F06：SDK 把未知结果的 provider 调用降级后，Host 前台运行时既不重试也不终止，重启后的恢复也不续推）。已交独立分析子代理查根因并给最小修复；修后 r12 重跑。

| 原始证据 | SHA-256 |
|---|---|
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/native-a8734fbf/primary-ui-00vc5gul/native.log` | d703c430d4e90f21bd2a91873ff99038ad1fc59d148abd4d663e941cb63108fd |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/native-a8734fbf/primary-ui-ouryr2xf/native.log` | a5d3cde3516f52c250f439b3742a265543ad0a4e25e647e1d8b26380d7a49aa6 |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/native-a8734fbf/primary-ui-h1m07zwa/native.log` | 289fbe34d8de6ffd86485955afd99780860e8df5d898c4d46e30517396700e49 |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/native-a8734fbf/primary-ui-00vc5gul/userdata/data/human_memory_v7.db` | 43b83fdf2b6c040e3fd237092b9f84f29b8849bb3290a43b22e45ff3fe147ff0 |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/native-a8734fbf/primary-ui-00vc5gul/userdata/data/state.db` | 295f5b1f410ebc1a709f8d576a4f20c92353b9554a504066fa0dd1f14b558393 |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/native-a8734fbf/primary-ui-00vc5gul/userdata/data/operation-audit.db` | 6025df63aa3aceb5e91e100ea6c6abd002ddbd3182e238f3c270156b4bfebe90 |
