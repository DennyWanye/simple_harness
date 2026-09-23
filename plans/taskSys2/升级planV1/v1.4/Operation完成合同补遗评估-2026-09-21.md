# V1.4 Operation 完成合同补遗：再次评估

日期：2026-09-21。评估对象：`V14-OP-COMPLETION-1.0`，叠加此前 `V14-OP-SEAMS-1.0`。范围仍为 V1.4 去除 NanoJev。

## 结论

**架构待决项：0。可以按补遗进入实施，不需要再给 Plan Agent 发一轮裁定 Handoff。**

上一份 `HANDOFF-2026-09-21-PlanAgent-Operation完成判定.md` 的唯一核心问题，在规格层已收敛。新文档选择了“同一 Obligation + 明确贡献范围”，并补齐批准要求、逐 occurrence 范围、准备接受、正式效果接受和最终完成读者之间的合同。

**附件可作实施依据，但参考 SQL 有一处已复现的唯一键缺陷，不能原样照搬。**正文已规定正确行为，修正此处不需要追加架构决策。本轮未改 SDK，也未把参考测试通过提升为产品完成。

## 上轮问题逐项核对

| 上轮待定内容 | 新补遗的明确裁定 | 评估 |
|---|---|---|
| 准备接受与动作完成的范围 | §2.2/2.3、§5：Scope 与 Contribution；MIXED 准备后保持 VERIFYING；数据可用但动作义务未完成；所有完成消费者共用 CompletionReader | 规格已收敛 |
| milestone 的权威来源 | §3：批准 Requirements 产生 Spec；认证 USER_CONFIRMED 或真实 REQUIREMENTS_POLICY 回执；缺失/歧义拒绝。§4 的 V2 intent/effect/draft 精确绑定 completion slot | 规格已收敛 |
| T3 回执与根完成的关联 | §6：受信 importer → 固定 OutcomeBinding → official OPERATION_OUTCOME Review → 效果 Acceptance/Contribution；DeliveryReceipt 是交叉核验后的投影；最终 Commit 不能跳过根效果门 | 规格已收敛 |
| Requirements/Plan 改变后的历史事实 | §7：旧事实不改写、不自动重发；新版要求使用新的正式 outcome review/Acceptance；无关 Plan 改变核验当前采用关系 | 有明确可实现路径 |
| D3、预算、迁移与 Host 接线 | D3 保持原裁定；其余为已明确范围内的实施适配 | 无需再裁定 |

同一 Obligation 下 CONTENT sibling 不承担所有效果；compound owner 可接受某个效果贡献，但整体完成仍由原组合 Review/GoalResolution 决定。Scope 可以插入原 Plan Commit 的同一 Store 事务。当前 SDK 的旧消费者需要修改，这属于补遗要求的实施内容，不能据此再次声明设计未定。

两项独立只读审阅与主代理核对均得出没有新增架构分叉。当前 SDK HEAD 仍为 `102ad3dfa2db38d575ea929d39ec5ed1561a71da`；上一 Handoff 所列六份 dirty 源文件 SHA-256 全部复核一致，不是仅根据远端历史代码作判断。

## 必须修正的参考 SQL 问题

位置：压缩包 `sql/operation_completion.sql`，`operation_outcome_review_bindings` 的唯一约束：

```sql
UNIQUE(mission_id,intent_id,effect_key,source_manifest_hash)
```

正文 §2.5 的 `outcome_binding_id` 包含 Spec 和 Scope 身份；§7 明确允许要求更新后，对同一已发生事实重新正式审阅。在原 intent、effect key、实际回执和 milestone policy 均不变，但 Spec/Scope 换版的合法场景，`source_manifest_hash` 可以保持相同。上述唯一键会拒绝第二份正式 Review binding。

主代理已在附件明确标记的 SQLite parent fixture 中复现：新旧两个 Requirements/Spec/Scope、同一原 intent 和 source manifest、不同正式 review package，第二次插入触发该 UNIQUE 冲突。这不是生产 SDK 的反例。

局部内存验证将约束改为以下形式后，两份 binding 可同时保存，`PRAGMA foreign_key_check` 无错误：

```sql
UNIQUE(mission_id,intent_id,spec_hash,completion_scope_id,effect_key,source_manifest_hash)
```

这里 `completion_scope_id` 指向不可变的 Scope；正式落地必须继续验证其完整 hash，并使存储去重身份与正文派生身份一致。该验证只说明参考 DDL 的修正方向，不替代真实 Store migration/并发/Commit 测试。

**处理方式：记录为实施必修项，保留原附件字节；实际迁移时采用完整 Spec/Scope 身份并增加跨版本重审回归。无需 Plan Agent 再决定要不要允许重审——正文已允许。**四张绑定表足够承载新 Review 对旧事实的重新采用，不需要第五套效果账本，也不应重写旧 T0。

## 本轮实际验证

| 检查 | 结果 | 边界 |
|---|---|---|
| 附件交付清单 | PASS，9 个文件大小/SHA-256 一致 | 文件完整性 |
| 包内正文与独立 Markdown | PASS，字节相同 | 来源一致 |
| 标准库参考测试 | PASS，41 tests，0 failure/error/skip，0.009 秒 | 参考 codec/覆盖函数/最小 SQLite fixture |
| 跨 Spec/Scope 重审唯一键反例 | 已复现原 DDL 拒绝 | 参考存储缺陷，不是 SDK 已发生事故 |
| 包含 Spec/Scope 的内存唯一键变体 | PASS，两份记录且 FK 检查通过 | 仅局部修正方向 |
| 12 组 OCC SDK 集成场景、Host UI、真实模型及 H1/V1.4 整门 | NOT_COVERED | 本轮未运行，不提升完成状态 |

使用候选实际解释器：`/Users/denny/projects/simple-harness-sdk-h1h-impl/.venv/bin/python`。没有安装依赖或变更项目环境；此前旧 seams 包缺 `jsonschema` 的测试记录仍保持原结论。

临时只读解压目录：`/tmp/v14-operation-completion-review/simpleharness-v14-operation-completion-2026-09-21/`。

执行命令：

```text
<candidate-python> <kit>/verify_delivery.py
<candidate-python> -B -m unittest discover -s <kit>/tests -v
<candidate-python> -B <evidence>/outcome_identity_probe.py
```

原始证据位于 Host ignored 目录：
`.local-test-evidence/2026-09-21/operation-completion/assessment-20260921T213152/`。

| 相对文件 | SHA-256 |
|---|---|
| delivery.log | `2997eb7f8fe1d1923f5030a1ee5cf9a010276489d6aa0ae756f97350c39c4551` |
| reference.log | `9fdd11767ec877c4a42628afd2d1e1df927d50667665de473f2f47fd955e4137` |
| outcome_identity_probe.py | `6132af4183665dfc07f2907ca9a1aeecd585e91f36312f97691edba3151f2a08` |
| outcome_identity_probe.log | `e2fef4f874305b8715b57986f068b99d0a318e38705b94d507de77e3d128ceec` |

## 归档与后续实施

两份原附件已保存到本 V1.4 目录，未改内容：

- `V1.4-Operation完成合同补遗-2026-09-21.md`：`8bf6a1c8ee40c2ada9ab88f51806e401ba8a690ea4ac1a481154f7e9ccfcb74f`。
- `simpleharness-v14-operation-completion-2026-09-21.zip`：`17d695f268328006acedfc924fd698aafc745e26800a910ff601dcce72a4429c`。

后续按 Spec/Scope 与严格 codec → 真实接受及全部消费者 → T0/T3 正式 Review 链 → OCC-01…12 和原门禁执行；D3 等已明确工作可以继续。纳入本评估的唯一键回归，避免只重跑附件现有 41 个测试而漏掉正文允许的换版重审。

本轮交付是评估、原附件归档和参考层验证；没有实现完成合同、没有修改 SDK 生产代码或数据库、没有合并/重装/发布。`OPERATION_COMPLETION_CONTRACT_VERIFIED`、完整 H1 及 V1.4 完成状态均不能据此宣布。
