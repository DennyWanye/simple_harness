# V1.4（去除 NanoJev）：Operation 补遗评估与完成判定裁定请求

日期：2026-09-21。对象：Plan Agent。范围：对 V14-OP-SEAMS-1.0 做增量裁定，不重开 H1–H8 规划。

## 1. 结论

补遗已解决原 D1/D2/D3 的大部分架构问题。建议只保留 **1 个核心待决项：准备贡献、动作效果义务与最终完成判定之间的权威绑定合同**。下文三个问题是同一条链的不同环节，不是三个独立 blocker。

D3 无需追加裁定。实际 Host 接线位置、迁移编号、预算 reservation subject 命名、严格 codec、原子回滚及幂等实现属于实施工作，不能继续用这些事项等待 Plan Agent。

本轮结论来自当前 dirty 源码静态核对和两项独立审阅；没有运行生产完成判定反例，也没有证明真实 Mission 已提前完成。发现的是新补遗与当前合同之间尚需明确的语义接缝，不能把它写成已发生的线上故障。

## 2. 当前版本和资料

- Host：`/Users/denny/projects/simple_harness`，HEAD `6c457908e49757035c21c6dc2b252415107a494c`，保留未提交改动。
- SDK 候选：`/Users/denny/projects/simple-harness-sdk-h1h-impl`，分支 `codex/h1h-impl`，HEAD `102ad3dfa2db38d575ea929d39ec5ed1561a71da` **加未提交改动**。不能只 checkout HEAD 后声称审阅了本轮版本。
- SDK 主树：`/Users/denny/projects/simple-harness-sdk`，本轮未修改。
- 新补遗：本目录 `V1.4-D1-D2-D3-Operation补遗-2026-09-21.md`，SHA-256 `a2657120e1c44479a268ca19e0ab0b3da1eb65d7b3db6512df7ef2e3d1520725`。
- 附件：本目录 `simpleharness-v14-operation-seams-2026-09-21.zip`，SHA-256 `3fbd3f3b5e3b634bcf128a3cf6441e5613401341c3642c28b647fcabc03c4237`。
- 原请求：本目录 `PLAN-AGENT-架构裁定请求-2026-09-21.md`。
- 原 AER：`simpleharness-full-target-1.4/annex/aer-1.0/design.zh-CN.md` §6.3、§14。
- 接手仍先读 Host `ARCHITECTURE/index.md`。不 reset/clean，不覆盖 dirty SDK，不合并、不安装、不推送。
- 总体范围仍是 V1.4 H1–H8，去除 NanoJev。应用模型 DeepSeek v4.1 Flash；子代理仅 GPT-5.6 系列。

## 3. 已明确，可以直接实施的内容

| 项目 | 本轮判断 |
|---|---|
| D1 生产入口 | 已明确：固定 Principal 的 submit/status；USER_COMMAND 或有正式来源的 AUTHORIZED_SLOT |
| D1 顺序与事务 | 已明确：accept 候选 → T0 冻结与正式 ACTION_PROPOSAL Review → T1 envelope/action/link/receipt 原子落库 → 原审批 → handoff |
| D2 payload 与解析 | 已明确：三个 CAS payload、独立 hash 域、精确 resolver、实际 connector profile、严格拒绝规则 |
| D2 未应用证明 | 已明确：真实、限定作用范围的回执；UNKNOWN 不自动重发，不能把布尔值或 lease 到期当证明 |
| D3 defer/resume | 已明确：纯 preview 后先 fence；持久 continuation；原 raw/request/decision 冷恢复；版本变化 stale；超时 MANUAL_REQUIRED |
| Review 预算 | 已明确资金与责任边界：真实 Task 的原预算体系、真实预留、禁止复用已结算假账户；具体接线由实施 Agent 完成 |

D3 当前源码的旧 proposal-text 重放、提前取消、pending 后 Mission 失败，需要按协议版本迁移；补遗已经给出目标语义，这些差异无需重新询问。

## 4. 唯一待决项：Operation 完成合同

### 4.1 准备 Acceptance 在什么范围内有效

补遗第 47 行、§3.1 要求准备贡献 Acceptance 不完成真实动作义务；§3.5 要求达到指定 milestone 后才完成相应义务。这些原则明确，但当前生产读者没有准备贡献与动作效果的类型区分：

- SDK `orchestrator/commit_service.py:4854–4870`：原接受结果路径完成 Attempt，并把 Task 更新为 COMPLETED。
- `contracts/resolution.py:1187–1205`：Acceptance 绑定 task/obligation/revisions/review；没有独立的准备阶段或所覆盖动作义务字段。
- `orchestrator/hierarchical_dispatch.py:2068–2096`：根审阅入口按 gating occurrence 的 ACCEPTED 判定；`:2112–2136` 按 CURRENT Acceptance 的 task/obligation 汇集贡献。
- `orchestrator/commit_service.py:5241–5298`：primitive CURRENT Acceptance 是完成检查的重要依据；只修改 Task 状态不能解决所有消费路径。
- `orchestrator/resolution_commits.py:1661–1688`：根 Requirements 未声明 delivery contract 时不要求交付回执。新补遗没有明确规定所有动作义务如何进入此门。

**请裁定唯一默认表达方式**：将准备与动作效果拆成冻结计划中的不同 obligation，还是保留同一 obligation 并增加精确贡献范围/阶段绑定？请同时指定 accepted-output 消费、occurrence outcome、根 readiness 和最终 Commit 各读什么权威对象，避免只挡 UI/Task 状态。

### 4.2 required_milestone 的权威生产与冻结

补遗 §4、§6.2（第 254–255 行）规定 `required_milestone` / `milestone_criterion_ids` 来自 Requirements 或已批准准则；profile 只证明支持哪些能力。

当前 `contracts/resolution.py:193–207` 的 Criterion 只有文本、分类、evaluation_kind、source/scope、evidence policy 等；`:110–150` 的 RequiredEvidencePolicy 只有检查 ID、独立性和覆盖说明。补遗 §2.3 的提交命令也不携带所需 milestone 的类型绑定。

不能仅凭“缺一个字段”断言所有旧 producer 都无法扩展；问题是需要先固定**哪种持久的、已批准的映射才有权决定用户要求的是 received、sent 还是 delivered**，并明确无法解析时的处理。Connector 支持列表和模型 candidate 不能替用户选择要求。

**请裁定**：使用现有 delivery contract、版本化批准检查准则，还是一个最小的 typed obligation binding？给出唯一 producer、精确引用、缺失/歧义拒绝规则以及 Requirements 变更后的失效方式。不要仅重复“prepare 根据原要求决定”。

### 4.3 T3 的实际效果怎样满足这份义务

补遗已有 effect contract、限定范围的 reconciliation observation，并要求 OPERATION_OUTCOME 达标后完成，但尚未指定它们如何成为现有 GoalResolution / DeliveryReceipt 的权威输入：

- `contracts/resolution.py:1519–1554`：DeliveryReceipt 含 acceptance_id、stage、operation_id 和 evidence_refs；SENT/CONFIRMED 只在类型层要求 operation_id 非空。
- `orchestrator/resolution_commits.py:1055–1118`：回执 writer 核验 Mission 和 Acceptance 归属，没有在此验证 operation→intent→action/link→冻结 effect contract→真实 connector receipt 的完整关联。
- 同文件 `:1829–1874`：最终引用校验检查 Mission、Acceptance、义务闭包与有效性；未核验上述动作关联。任意一项现有检查都不能单独证明“本次动作达到本次要求的 milestone”。

**请裁定**：受信 T3 outcome importer / OPERATION_OUTCOME Review 的唯一写入口和最小持久关联，以及根完成时如何精确验证。优先复用 DeliveryReceipt、evidence refs、原事件/receipt 和已允许的表；若需扩展合同，明确扩展点，不建立第二套 action 效果账本。

## 5. 建议方向（供裁定，尚非已批准规格）

固定一个贯通三段的最小合同：

1. Requirements / 批准准则先冻结“哪项 obligation、哪些 criterion、要求哪种 milestone、允许何种证据”。此时不要求尚未生成的 intent/operation 身份，避免循环依赖。
2. T0 将真实 source-slot/intent、producer occurrence、准备 Acceptance、上述要求和冻结 effect contract 精确绑定。缺失或歧义继续拒绝，不能用临时文本猜测填补。
3. 准备 Acceptance 可供获准的后续准备/审阅读取，但不能独自满足动作效果义务。
4. T3 由受信导入链核验真实 action key/version、operation occurrence、link、effect contract 与 connector receipt；以已有持久对象记录核验结果。
5. 根完成只接受覆盖该动作义务且仍有效的结果；未提交意图、待审批、UNKNOWN、仅 received 而要求 delivered，都保持未完成。

这不是要求再设计一套通用平台。请选定最小兼容方案，并明确准备/效果是分 obligation 还是同 obligation 的不同贡献范围；不要把这个会影响计划表达和完成语义的选择留给实施时猜测。

## 6. 请给出的最小补遗与决定性验收

请按上述单一主题给出：字段/严格 codec、唯一 producer、持久位置、读者与状态转换、事务边界、legacy 兼容和需要扩展的 allowlist。特别明确 `resolution_commits.py`、`leaf_acceptance.py`、根 readiness/贡献读者等已有文件的必要改动，不限定为 accept_result 一处。

验收至少覆盖：

1. 同一 Mission 要求“生成报告并送达”：准备报告真实通过审阅并产生可用贡献后，动作义务和 Mission 仍未完成；不存在等待 Mission 完成才允许操作的循环。
2. milestone 绑定缺失或歧义拒绝；只支持 received 的真实 profile 不能满足 delivered；不能静默降级。
3. 正确动作、正确效果合同与正式 outcome 使义务达标；同一回执重放幂等。
4. 错 operation/occurrence/action version/link/effect hash、其他 Mission/义务的回执均不能完成；不能靠传入一个非空 operation_id 绕过关联验证。
5. 原要求/输入撤回或变更后，旧准备 Acceptance / outcome 不得误用于新的完成判定。
6. 存在多个必需动作义务时，一个成功动作不能完成其余义务；UNKNOWN/待审批保持可见且未完成。
7. 纯报告 Mission 和 legacy 行为保持兼容；不凭 action candidate 的存在自行发明用户未要求的外部动作义务。

## 7. 本轮验证边界

- 附件已保存到本目录；解压只用于临时只读评估，未覆盖 SDK。
- DELIVERY-MANIFEST 中 9 个文件的大小与 SHA-256 全部匹配；包内主文档与独立 Markdown 相同。
- 尝试 `uv run --frozen python /tmp/v14-operation-seams-review/simpleharness-v14-operation-seams-2026-09-21/tests/test_contracts.py`，在 import 阶段因 `ModuleNotFoundError: jsonschema` 退出。**没有本轮参考测试 PASS**，未改项目依赖。这是环境缺依赖，不是架构待决项。
- 本轮未运行真实模型、Host UI、SDK 全量或新的完成判定反例。旧回归数字不提升为当前验收；整个 V1.4 仍未完成。
- 本次只新增评估 Handoff，没有实现补遗，也没有任何功能验收完成声明。

所核 SDK 文件 SHA-256（路径相对 `src/agent_orchestrator/`，用于识别 dirty 源码）：

| 文件 | SHA-256 |
|---|---|
| contracts/resolution.py | `92dcb60352ba829275bfe4c732691f20d63a97eee875edb98db81672461fad60` |
| orchestrator/commit_service.py | `6eaf99796b65065eb1521dd78103e97b97ded2b0bd46dc16bb075d5c87319325` |
| orchestrator/leaf_acceptance.py | `7bfe1ac593b3758baaa205c4ffea0cc26afbf86c238073020859ccc0077c31c7` |
| orchestrator/hierarchical_dispatch.py | `1ce4a987b3ded8d2b90a5e78bbf6f82855b2c4b273d840fa1f206810223608c6` |
| orchestrator/resolution_commits.py | `109d9bc8e062b1c7ef0d6727e65ee8882ccccf8cf02e3be4ddf5218e83a9da5e` |
| orchestrator/event_handler.py | `6f860626e71b804bbfadcffe97608a1fda2f6088140d8ac25fe2f9e13ddd874f` |

## 8. 并行继续的工作

D2 codec/resolver/profile、T0/T1 原子性、正式 Review/原审批接线、D3 stop gate/原 raw 冷恢复、既有回归可以按已裁定范围推进。上述完成合同的依赖部分保持未完成标记，不能宣称整个 Operation 链或 H1/V1.4 已关闭，也不应因此冻结其他已明确任务。
