# 任务书 B 执行记录：全宿主统一模型并发门

日期：2026-10-31（Asia/Taipei）。

## 1. 结论与交付身份

共享并发门源码已实现、提交，并通过新增 13 项测试及独立审查。**任务书第 6 节的整体全绿标准未达成**：指定原有回归共 11 项失败，其中 2 项在原 main 同样失败，另外 9 项由源码变化后正式部署清单失配引起；全仓库 Ruff/格式检查存在基线问题。按第 8 节把问题写明、完成车道内工作，没有等待用户处理，也没有越车道修复清单或旧测试。

- 基线：当前 main `169363812531cecc7184318175cba9751b5dadf7`，已合并任务书 A；覆盖任务书原来的旧基线。
- 分支：`sol/B-shared-admission`。
- 工作树：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-B`。
- 代码与新增测试提交：`cf4d113bc07ace3144ff282b7255c8bcf17495fa`。
- 本记录另作文字提交；记录提交号可用 `git log -1 -- plans/2026-10-30-agentos-main-session-contract/记录-任务书B-Sol.md` 查询。
- 未推送、未合并、未发版、未改默认数值、未改指纹常量、未改工具说明或提示词、未碰密钥。

## 2. 改了什么

以下 SDK 路径均相对于工作树的 `sdk/simple-harness-sdk/`。

| 文件 | 变更与原因 |
|---|---|
| `src/agent_orchestrator/host/host.py` | 首次组装创建唯一 `LocalProviderAdmission`；宿主配置透传给任务池。重建保留同一对象，关闭宿主后清除宿主持有的引用。 |
| `src/agent_orchestrator/host/chat.py` | 仅修改 `open()` 中的组装调用，显式传入宿主并发门；旧聊天、主 Session、子 Session 均经过该调用。 |
| `src/agent_orchestrator/runtime/assembly.py` | 增加不进入序列化或比较的宿主门参数；把既有任务准入与共享门组合。`admission_accepts` 与持久意图核对逻辑原样保留。 |
| `src/simple_harness/execution/provider_admission.py` | 增加共享容量与原准入的组合；原准入拒绝、取消或调用结束时释放共享许可。进程内票据按对象身份记账，避免不同库的相同调用号覆盖；强引用防止对象地址复用，重复 observe 不重复释放。 |
| `tests/orchestrator/host/test_shared_admission.py` | 新增 13 项自动化测试，计数器包在 provider 的真实 `invoke` 入口，观察真实在途量。 |
| `docs/orchestration-host.md` §2b | 增加三行说明全宿主上限、生命周期及需要新数据根。 |

当前 main 的任务池实际已经有 `ProviderBudgetGuard`，不是任务书背景描述的单纯池内信号量。直接换掉它会丢失持久预算、任务状态与取消核对。因此保留各池原有 guard 与其指纹，组装时在它前面共享同一个宿主容量对象；聊天池直接使用该对象。先获得宿主名额，再进行原 guard 准入；handoff、observe、recover 仍调用原 guard。槽位数没有进入任何准入指纹。

## 3. 先红后绿与逐项验收

修改实现前，三路聊天测试已红：两个 Session 各 `send` 加一次旧 `host.chat`，上限为 1 和 2 时峰值均为 **3**，两项失败。提交后把相同测试加载到未修改的 main 源码又复现两项失败（6.03 秒），证明用例确实能检出旧缺陷。

独立审查另找出一个真实缺陷：旧聊天与 Session 使用相同会话号时，不同执行库可能产生相同 run/调用号。原共享实现按调用号记账会覆盖占用，门为 2 时两个回合结束后只剩 1 个许可。修复后原复现恢复 2 个许可，新回归还验证第二轮能再次同时进入 2 路。

| 必须行为 | 测试/证据 | 状态 |
|---|---|---|
| 两个 Session 加旧聊天，上限 1 | 三次申请均抵达门后，实际峰值恰为 1，三个回合全部 committed | PASS |
| 同样三路，上限 2 | 实际观察到峰值恰为 2，三个回合全部 committed | PASS |
| 取消在途调用释放许可 | 旧聊天取消后另一 Session 能进入，峰值始终 1 | PASS |
| 取消排队回合不出站、不漏许可 | 被取消回合未调 provider，后续回合仍可进入 | PASS |
| 失败后释放 | provider 拒绝、任务准入在 handoff 前拒绝，其他 runtime 后续均可进入 | PASS |
| 宿主关闭与重新打开 | 不放行 held 调用直接关闭；新宿主新回合可进入，未继承占用 | PASS |
| Session 关闭不销毁宿主门 | 其他 Session 与旧聊天继续使用原对象 | PASS |
| 任务池与聊天池共享 | 真正创建任务并驱动到 COMPLETED，与聊天并行时峰值 1；原预算 guard 与指纹保留 | PASS，测试清单前置见下文 |
| 重建期间保留共享容量 | 一个 Session held 时重建，旧聊天与新任务池仍使用原宿主门 | PASS |
| 跨库同调用号安全 | 两轮各观察到 2 路进入，不漏许可 | PASS |
| 池编号不变 | 与 main 中基线字符串比对：`deepseek-native-600k-chat-v1`、`deepseek-native-600k-chat-v3-c2-9f49f4fc1de3` | PASS |
| 旧空准入指纹拒开 | 持久意图的指纹为 null 时，原组装检查抛出 identity differs | PASS |

测试只替换 OS 本进程身份查询端口，做法与现有 `test_session.py` 一致；不外推真实进程身份查询或跨进程恢复验收。

两个任务路径测试在临时目录复制原清单上游信息，按当前真实源码重新计算文件哈希与 deployment_id，标为 **NOT_RUN**、清空验收证据。建任务和恢复流程都仍走原 `InstalledHtnWiringAcceptance._read`，仅把测试读取器的 manifest 路径指到同一临时文件。没有跳过清单校验，没有复用旧 VALIDATED 结论，没有修改正式清单。

## 4. 测试命令与通过数

除明确说明外，cwd 为工作树 `sdk/simple-harness-sdk/`；没有运行全量回归。

| 命令 | 结果 |
|---|---|
| `uv run --frozen python -m pytest tests/orchestrator/host/test_shared_admission.py -q` | 已提交、干净工作树 `cf4d113bc`：**13 passed / 47.45 秒** |
| 独立审查同一条新增测试命令 | **13 passed / 47.71 秒**，源码与上述提交一致 |
| `uv run --frozen python -m pytest tests/orchestrator/host/test_chat.py -q` | **8 passed / 2 failed / 115.44 秒**；main 对照同样 8 passed / 2 failed / 116.06 秒 |
| `uv run --frozen python -m pytest tests/orchestrator/host/test_host_sessions.py tests/orchestrator/host/test_session.py -q` | **35 passed / 9 failed / 359.53 秒**；其中 host_sessions 为 14/1，session 为 21/8；原 main 两文件合计 **44 passed / 118.20 秒** |
| `uv run --frozen ruff check src tests` | FAIL：1766 项；对 main 与当前 `--output-format json` 按相对文件、诊断码和消息逐条比较，新增 0、移除 0 |
| `uv run --frozen ruff format --check` | FAIL：50 个文件需要格式化；与 main 相同，新增文件已经格式化 |
| 对 chat.py、assembly.py、provider_admission.py、新测试执行 `ruff check` 和 `ruff format --check` | 全绿；host.py 仍有 main 既有的一条 E501 和 `open_session` 签名格式问题，本次新增行无新增诊断或格式差异 |
| `git diff --check` | PASS |

main 对照测试使用同一工作树解释器，cwd 切回原仓库 SDK，并设置 `PYTHONPATH=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/sdk/simple-harness-sdk/src`，确保导入 main 源码，未修改原仓库。

### 指定回归未通过的明细

聊天两项在 main 也失败，未修改它们：

- `test_cancel_takes_effect_before_the_next_model_call`：旧断言期望取消状态 pending，实际 cancelled。
- `test_closing_the_host_mid_turn_settles_and_the_session_survives`：旧聊天同一会话中途关闭后恢复回合超时；伴随已关闭数据库上的旧回合结账日志。

当前源码使正式源清单哈希失配，下面 9 项失败；main 对照全部通过：

- `test_two_host_sessions_isolate_history_tasks_and_scope`
- `test_real_control_command_creates_one_mission_without_domain_tool`
- `test_real_mission_result_inbox_next_turn_and_cold_events`
- `test_control_reconciliation_uses_durable_turn_binding`
- `test_combined_inbox_releases_every_result_allowance`
- `test_process_exit_and_real_mission_recovery[input_committed]`
- `test_process_exit_and_real_mission_recovery[delegation_recorded]`
- `test_process_exit_and_real_mission_recovery[mission_created]`
- `test_process_exit_and_real_mission_recovery[inbox_written]`

原错误为 `SourceUnavailable: taskgraph_deployed_source_unverified`；子进程分支可能先等待委派而超时，未到预定 exit 77。正式 `taskgraph_deployment_manifest.json` 不在允许车道，保持原样。因此本交付不宣称正式默认宿主可启动、任务默认部署可执行或已完成发版。

## 5. 旧库、审查与证据

旧持久意图的 `provider_admission_fingerprint` 为 null，而新聊天池注入 `base-agent-local-admission-v1` 时，原 `_check_intent_contexts` 会拒开，**需要新数据根**；不加兼容、不迁移指纹。严格来说，这个拒绝由“存在匹配持久意图且其指纹为空”触发，不把没有这类意图的所有旧目录都描述成必然拒开。

独立 reviewer 已复跑新增测试和原同号碰撞复现，结论为共享门代码范围 PASS，无未关闭 P0/P1/P2。审查报告在 `.local-test-evidence/2026-10-31/shared-admission/review.md`；最终独立测试日志为同目录 `review_final_tests_pass.log`。该结论不替代第 6 节整体验收。

原始日志与 Ruff JSON 都保存在上述被 Git ignore 的本机证据目录，不提交原始证据；末尾索引列出最终文件哈希。

## 6. 拿不准的与处理决定

1. **任务书背景与当前任务池不同**：保留 `ProviderBudgetGuard`，仅组合共享容量，不移除预算或生命周期核对。该选择已由真实任务与聊天并发测试验证。
2. **配置透传入口**：Orchestrator 构造器没有可直接注入外部准入对象的公开参数；为不修改禁区文件，在允许的 assembly 配置上透传进程内对象。它不进入 `to_json`、持久身份或配置比较，不改变 admission_accepts。
3. **恢复性能边界**：组合对象不再是 `isinstance(ProviderBudgetGuard)`，现有迟到结账对它调用完整 `recover(uow)`，没有使用按 mission 收窄的优化；原预算恢复语义保留。没有为性能扩大车道。
4. **正式清单更新**：源码变更使包清单失配是确定事实；不在车道内自行改清单，不伪造 VALIDATED。新增任务测试用真实读取器加临时 NOT_RUN 清单验证接线；正式清单重生成与完整部署验收留给审核人处理。
5. **既有取消/旧聊天恢复测试和全仓库静态门**：保留基线失败，未改旧测试、默认数值或无关格式以凑全绿。
6. **仓库通用规则与专门任务书冲突**：任务书限定只修改指定文件、完成记录只写本文件；因此没有追加 ARCHITECTURE、额外 plan、机器账本或 retro 文件。执行、验收、自我批评均合并在本记录中。

执行方式为单会话串行实现，另由独立 reviewer 只读审查。验收属于确定性的 SDK 并发/生命周期逻辑，使用可复跑自动化测试；不涉及 UI 或真实模型质量评估。未请求任何额外授权。

自我批评：决定性三路并发测试与独立碰撞复现有效拦住了缺陷；任务测试前置未一开始同时覆盖建任务和恢复两个清单读取入口，导致两轮无效超时。后续同类任务应先核对所有清单消费点，再跑长恢复用例。

完成检查：车道内实现、新增用例、文档、独立审查和提交态复验已完成；指定旧回归与全仓库静态门仍 FAIL。完成判定依据本记录与逐项 DoD，没有机器 receipt，不宣称任务书整体完成。

## 7. 本机证据索引

以下路径相对于 `.local-test-evidence/2026-10-31/shared-admission/`，原始文件均未提交。

| 文件 | SHA-256 |
|---|---|
| `shared-red-main.log` | `204d465ee304d1d259d5e1c17faf7d14142299b50fcb39e7e017a4067d6192ab` |
| `shared-committed.log` | `72f055e6ec185ce6b2b26952e7b841881cf2e926b25cc77090e6b71d2595db19` |
| `required-sessions.log` | `c4a0a3a852fba869e1908f80fe4963bd720af42244c844f9ea54e2cfbb104ee3` |
| `main-sessions.log` | `f99c8324377da41de9b48b61ab6878de31ec58ed2fd8f43648e5f9764ece86c8` |
| `required-chat.log` | `9094a92d74c7864d9631889bde0168aa5ca26349041791eae36970110d5755a6` |
| `main-chat.log` | `df21afab925d9ce288577b91672238f52e2d317eadb750218ebf1f9344dba26f` |
| `main-ruff.json` | `b8efe7f0af38f97c0cc7c3d6b9b6a925de82f6ccffd339f5e8d141a44ed04efc` |
| `current-ruff.json` | `178342868174e3d3afcc97b331fc08168d5c00b0281903c538e562731f69d537` |
| `main-format.log` | `62fce7a133971f32afdafaa5da6698df9935dbb710ae1183fccc3a0d0e660f31` |
| `current-format.log` | `afa8260bcd4430eae4787a48e19fa5dee271082029a180f0e7713edb1b844f96` |
| `review.md` | `d4c217ffbf0260477876a7d28c429e98ef5ce7ba6b36d940708bddf38aa64cc4` |
| `review_final_tests_pass.log` | `0658e6c8e125602c5ed4d60cb9e831dee77c07be0bfc91e67b0df39669cfd83e` |
| `review_collision.py` | `ee09a76ea07f618aa4e6beae74b8583b967411748df0c261ddc495f078369265` |
| `review_collision.log` | `d4a9a0d9ed117abb9eb342934067bc2ce1e14e7e4b5d56c943150ae588b1b5bf` |
| `review_collision_fixed.log` | `4810f25fb8b52970d5c4a0514f7c6cbdc306bdc49dc32c7038afb4e94563a771` |

VERDICT: BLOCKED — 任务书 B 完整验收：共享门源码及新增 13 项 PASS，旧聊天 2 项既有失败、正式源清单导致 9 项失败、全仓库静态门既有失败 — 2026-10-31 — cf4d113bc07ace3144ff282b7255c8bcf17495fa
