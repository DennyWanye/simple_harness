# Procedure 创建采用分类契约：供 Dirac 挑战

2026-09-06；契约候选，尚未实现或验收。此契约替代此前只读建议中“Host 独立语义判别/受限采用语法”的设想：**主模型负责语义分类，Host 负责真实来源、引文、步骤及版本绑定，不新增关键词、正则或第二套 NLP 分类器。**

## Owner 与实际代码边界

- 本叶独占既有稀疏树 `/Users/denny/projects/simple_harness-corpus-clock`；切换前 tracked/untracked 均无 WIP。旧分支 `feat/host-trusted-disclosure` 保留在 `f3675064`。
- 新分支 `feat/procedure-adoption-contract`；base `2c8c57c6c02ee5dda288f9d352d2611ffcd29933`，来自已存在 primary candidate。没有新 worktree、venv、依赖安装或测试进程。
- 拟改 `backend/deskpet/memory/analysis_proposal.py`（v4 schema/prompt/结构验证/Procedure 创建）、`analysis_executor.py`（按实际 request 版本选择 prompt/schema/compiler），新增 `backend/tests/memory/test_procedure_adoption.py`。可将纯结构校验拆小模块，但不得变成 NLP 判定器。
- `memory_ingestion_outbox.py:368` 已导入 proposal 版本常量生成 worker config，预计无须修改；若实际接缝要求增加变更，先列出具体原因。测试通过后同步本树 ARCHITECTURE，当前只交契约。
- 不改 Memory SDK、已冻结语料、阈值、预算/model parameters、runtime/composition、context authority/route 或 Prospective source/store/consumer。后两组仍分别由 Hegel/Carver 负责。

## 信任边界：模型的语义分类仍是模型判断

现有 `analysis_proposal.py:383` 把所有 Procedure 设 ACTIVE，`:445` 都标 EXPLICIT_USER。新协议把“用户明确要求采用”与“用户仅叙述操作”分开，但不声称字节匹配能证明自然语言语义。

模型输出 `intent_kind = adoption | reported_steps | uncertain`。它不是 execution authority，也不能自行提供 lifecycle、success_count、receipt、subject、Run 或权限字段。主模型需阅读完整证据，分辨本人采用、他人引述、否定、假设、过去成功叙述及指代范围；不以采用词表或 confidence 阈值替代语义判断。

Host 仅在 **adoption 分类 + 合法来源 + 完整上下文引文 + 采用引文 + 每步精确绑定** 全部成立时创建 ACTIVE。无凭据的 bool/enum 不足以创建 ACTIVE；但模型若把真实引文中的否定错误分类为 adoption，结构验证本身无法辨认。该剩余语义风险必须由真实主模型分类验收覆盖，不能在确定性测试中宣称已消除，也不再引入一个伪“独立语义 authority”。ACTIVE 只表示显式采用的记忆状态，后续适用性与 effect 权限仍独立。

## 模型字段与 Host 可执行检查

保留 operation 的 `evidence_item_id`、`exact_quote`、procedure.name/applicability/risk_level；procedure 新增以下严格字段，拒绝 unknown fields 和类型强转：

| 字段 | schema 与意义 |
|---|---|
| `intent_kind` | 必填 enum：`adoption`、`reported_steps`、`uncertain` |
| `adoption_quote` | 必填 string；adoption 时为非空的实际本人采用语句；其他类别必须空串。不是权限 token |
| `steps` | 必填，1–16 个非空字符串；本叶每一步直接复制源步骤，编译 payload 也使用这些原文字节，不另让模型改写步骤 |

首叶明确支持**单个已入场 USER 消息内自足的程序**，不偷偷拼接多项历史：

1. `evidence_item_id` 在 request.ordered_evidence_refs 对应的实际 AdmittedItem 中恰一命中。校验 durable envelope/receipt、对应 ref content hash、subject、source_kind=USER_MESSAGE、实际文本存在；不能只凭 top-level text 或 derive_span 里硬编码 USER 推断作者。源消息的原 run 身份保留，不误要求它等于分析 batch 的 run。
2. Procedure 的 operation.exact_quote 必须等于该项**完整 sanitized USER text**。沿现有 identity UTF-8 v1，整段不超过既有 16 KiB span 上限；不做 trim、Unicode 归一化、模糊匹配或模型偏移量。完整上下文约束防止只截取引述/否定中的肯定片段，但不冒充语义判定。
3. 每个 step 必须在完整文本中唯一出现，按源顺序排列且不重叠；拒绝重复步骤索引、漏绑定、跨 item 拼接和幻造改写。模型需要概括或跨消息指代时，本叶不能偷偷构造合法 seed；有本项实际可绑定步骤但采用不明确可 uncertain/draft，完全缺步骤则不创建并记录原因。
4. adoption_quote 必须是同一完整文本的唯一非空子串。模型负责判断此语句确实由本人明确采用，并作用于所列全部步骤；Host 只核唯一位置和同源关联，不声称同源即蕴含关系成立。
5. Host 派生完整上下文、各步及采用语句的 EvidenceSpanRef（adoption 才有采用 span），字节范围/hash/receipt/ref 均从持久来源计算；模型不提交这些可信字段。所有 span 一起绑定编译 plan，并保留现有原始 Provider response 的持久副本。
6. reported_steps / uncertain 在结构和步骤合法时创建 DRAFT；保留真实 USER 陈述的 EXPLICIT_USER/SOURCE_BOUND 来源语义，不把“我成功了”改成已核 OBSERVED_BEHAVIOR。观察成功数不增加，不调用 record_procedure_observation，不签 terminal receipt/effect。
7. 字段缺失、非法来源、引文缺失/重复/超限、adoption 与空采用引文等结构矛盾：按当前 rejected-operation 审计策略拒绝该 operation；不能降成普通 draft 来掩盖损坏。**合法语义 uncertain** 才是 draft。编译 reason_code 由 Host 根据已验证分类固定，不将模型 reason_code 当许可。

例：完整 USER 文本“以后整理文件就按这两步：先列清单，再复制到备份目录。”，adoption_quote 可为“以后整理文件就按这两步”，steps 为“先列清单”“再复制到备份目录”，可按 adoption 创建 ACTIVE。完整文本“这次整理文件先列清单，再复制到备份目录，成功了一次。”应分类 reported_steps、空 adoption_quote、相同原文 steps，创建 DRAFT。两例都不执行复制。把第二句强行分类 adoption 且引用“成功了一次”是**模型语义反例**，不能声称 Host 的子串检查会识别它。

## 版本和 replay：不得用新规则改写旧事实

实际接缝：`analysis_executor.py:266` 的 durable envelope 优先回读；`:369` 附近还存在 settled response 已持久、envelope 尚未派生的恢复路径；`:507` 当前直接调用全局 compiler。Memory `sqlite_v5.py:10819` 从持久 analysis_batches.request_json 恢复原 request；原 request 带 prompt/result_schema/policy 三版本，不带 validator_version。

- 新批次使用 `host-analysis-prompt/v4`、`memory-analysis-proposal/v4`、`host-analysis-policy/v4`。沿既有 worker config 构造并写入 SDK 持久 request/hash，模型不能选版本。预算、Provider/model/config 和持久绑定不变。
- SDK 通用结构验证器本叶未改，`host-analysis-validator/v3` 保留；不得谎称 request 含第四个版本字段。v4 是 Host prompt/proposal 编译政策版本，不改 SDK validator receipt 格式。
- executor 以 request 中精确三版本组合选择 v3 或 v4 的 prompt/schema/compiler；未知或混合版本拒绝并审计，零 Provider 调用，不默认套用最新规则。现有 v3 逻辑保留为受版本约束的兼容实现，新 v4 不因字段缺失降回 v3。
- 已落盘 envelope/plan 优先按原 request 验证并回放，零编译、零 Provider；不重新审判旧 ACTIVE。已落盘 v3 response 尚无 envelope 时，只能走原 v3 派生；已落盘 v4 response 走 v4。不得根据响应是否包含新字段推断版本。
- 新请求版本不会覆盖旧批次的 request/hash、attempt、receipt 或 member 互斥记录；不因切换版本绕开旧未决投递开新调用。新结果的分类、证据 span、状态和版本分别由既有 response/plan/request 持久绑定，无新增表或账本。

## 必要控与 Dirac 挑战点

测试需等待主通知，之后只用共享默认资源锁。当前不跑 SDK 旧绿、模型或 native。

| 必要新控 | 具体边界 |
|---|---|
| 三分类编译及一个实际 installed 公共写入路径 | adoption ACTIVE，reported_steps/uncertain DRAFT；观察 success 为零；无 effect/observation 调用 |
| USER/subject/ref 与引文/步骤变异 | 非 USER 顶层 text 也拒绝；跨 item、冒造步骤、截断上下文、重复引文、空 adoption_quote、unknown fields 不能晋级 |
| 正确语义分类的否定/引述/假设样例 | 主模型协议要求 reported_steps/uncertain；手写响应仅证明映射，不证明真实模型分类准确 |
| 高风险明确采用 | 仍可 ACTIVE 记忆，不获得自动执行或跳过 applicability 的权限 |
| v3 已有 plan replay；v3/v4 response-only 重开 | 原计划不改；分别按固定请求版本派生；零重复 Provider；原三键互斥保留 |
| 新 v4 缺字段、未知/混合版本 | 无兼容降级、自报授权或静默改写 |

请 Dirac 优先挑战四点：①明确接受模型语义判断的剩余边界，而非把 quote/hash 误称授权证明；②完整 USER text + 采用引文 + 原文有序步骤是否足以限定首叶，是否存在结构绕过；③v3 response-only 恢复是否被最新全局 schema/prompt 污染；④版本变化是否触发旧 evidence member 重投、改变持久 plan。挑战接受前不扩展观察累计或适用性叶。

当前无可调用的 Dirac 子代理入口；本固定契约交主转发，不声称已完成独审。
