# 原生 r10：Procedure 使用链（Memory 0.6.25 发现面）——步 1–4 通过，步 5 FAIL

2026-09-07 深夜，taiwan Mac。bundle `SimpleHarness Memory Verify 23b6c823p18120.app`（Host main 23b6c823），installed H0.7.10 / **M0.6.25** / S0.3.13，隔离 userdata，gpt-5.6-luna 主通道未回退，auto 模式。驱动：System Events（无 computer-use，无截图），证据以 SDK 库、Host state DB、operation-audit.db、native.log 为准。脚本按 `DECISION-PROCEDURE-USE-CHAIN.md` §3.4。

| 步 | 输入 | 结果 |
|---:|---|---|
| 1 | 「请记住我以后常用的一个流程，名字叫“松柏记录”…以后我说按松柏记录做，就按这两步做。」 | v6 提案落库：procedure「松柏记录」**active / `unbound:procedure-applicability:v2`**；向量世代 active（vector_count 含该 procedure） |
| 2 | 「…叫“云杉归档”…先记着，别执行」 | procedure「云杉归档」**draft**（不进向量世代） |
| 3 | 「帮我把“收到材料以后，我们再开始进行校对工作”改得简洁些。」 | 直接回答「收到材料后再校对。」，无模型驱动召回（typed_recall_requests 非 analysis 计数 0） |
| — | osascript quit → 同 userdata 冷启动 | 首进程 parent 0 / remaining=[]；第二进程 startup complete |
| 4 | 「查一下我以前存的“云杉归档”流程草稿，现在是什么状态？只查，不要执行。」 | 模型调 `procedure_discover`（operation-audit `discover_procedure_drafts` 2 次，无 task_scope_search），回答「草稿（draft），第 1 版，低风险，尚未获得执行授权」并列出两步；`procedure_uses` 0 行、无文件效果 ✅ |
| 5 | 「新建一个本地任务叫“松柏九月”…按“松柏记录”流程做一遍，做完把两个文件读出来核对内容一致。」 | `context_route(create_new)` 创建 TaskScope「松柏九月」✅ → 模型调 `procedure_discover` 时省略 required 参数 `after` → `tool_arguments.missing`、tool_attempt.failed → 下一次 provider attempt 被 `PrimaryHistoryDisclosureRejected: Current history dependencies cannot be verified` 拒绝 → `run.fail`。**FAIL**：无 procedure_use、无文件 |
| 6/7 | 未执行 | — |

判定：**S3 Task 3 / HM-AC-5 Procedure 使用链仍未通过**；但 0.6.25 发现面修复有效（步 4 是 r24/r25 从未达到的：真实 `procedure_discover` 非空并正确区分草稿与执行）。

## 步 5 暴露的两个缺陷

1. Host `procedure_discover` schema 把首页游标 `after` 设为 required，模型自然省略 → 工具失败。已修（main a95dced9：`after` 可选、运行时默认空游标，单测 1 项）。
2. 工具失败后的下一次 provider attempt 被 `PrimaryHistoryDisclosureRejected` 拒绝并终止整个 Run（与语料 run-01 C05-07 同错误码）。已交独立分析子代理复现根因并给最小修复（`DECISION-HISTORY-DISCLOSURE-R10.md`）。修后 r11 重跑步 5–7。

| 原始证据 | SHA-256 |
|---|---|
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/native-23b6c823/primary-ui-idyatccf/launch.json` | a3c241918662ad59ba6fa1f8cc82c11f515fc474ff1c518cb6b446e75e80edfc |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/native-23b6c823/primary-ui-idyatccf/native.log` | 56b5d755cbd7281e03bced5a2435d1bbf6380a673a12309d42a865579a8513db |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/native-23b6c823/primary-ui-_7uaiasu/launch.json` | 98ab7dc99ea452e66f4d8d28b01b34cb80da3501cdd1aa8f1a333f4072bee8e0 |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/native-23b6c823/primary-ui-_7uaiasu/native.log` | a64c62f7c18e71c6afed6f88a737dff72537a6a91baf4464f72cfac1133bb815 |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/native-23b6c823/primary-ui-idyatccf/userdata/data/human_memory_v7.db` | 0cb1c65b7a4da21440bfb9836662122c3176c987989daffba44fae1686e99f64 |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/native-23b6c823/primary-ui-idyatccf/userdata/data/state.db` | f914c082764061a14cd4d2ceede511eabf36c6f1406822a3d0fb5a83b49f1d99 |
| `.local-test-evidence/2026-09-07/native-23b6c823/resource-r10/resource.json` | b0b30d6e25b0a7e1a5e8bea5cfed779227d535d26703fa9ece1f22ed3bc30ead |
| `.local-test-evidence/2026-09-07/native-23b6c823/resource-r10b/resource.json`（0 [] 1932752 1089.731） | c7d52c0f2acd919fcb5d34ac12ce14279e33af70e26c72e9f88c3a417da64966 |
