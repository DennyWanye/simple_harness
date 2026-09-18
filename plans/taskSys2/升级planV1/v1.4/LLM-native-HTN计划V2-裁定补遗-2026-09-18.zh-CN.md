# LLM-native HTN 执行计划 V2 —— 裁定补遗（H1 适用）

日期：2026-09-18　裁定人：执行指挥（用户 2026-09-18 授权："不是太重要的，就自己解决"）
依据：《LLM-native-HTN计划V2-开工评估》11 条 + Grok 独立复核《H1-V2对照源码冲突检查》（9 条 BLOCKER、13 条内部矛盾、11 条新缺口，Grok 对 11 条建议 10 条同意、1 条要求加字段）。
效力：**本文与 V2 计划同等权威；与 V2 冲突处以本文为准。** 编码代理不得再以"未裁定"为由自行发明格式；本文没覆盖到的新问题仍按 V2 第 0 节写 BLOCKER。
性质：全部是细节收口，不改变 V2 的架构、决定类型、一回复一决定、系统字段禁令、请求绑定、持久化表职责。

## 一、引用（对应 BL-1、BL-2）

1. `PlanningRefKind` 增加第 16 个值 `method_instance`。V2 第 25 节 `rejected_method_instance`、第 27 节 `consumer_method_instance_ref` 都是完整四元组 `{kind:"method_instance", id, semantic_revision, content_hash}`。
2. 类型引用是另一种形状，定名 `VersionedTypeRefV1 {id, version, content_hash}`，只用于 V2 第 26 节 `goal_type_ref`；不进 `visible_refs` 的四元组匹配，按类型注册表校验。
3. 每种引用的修订号与内容哈希来源，采用冲突检查文档 §5.1 的表，原样生效。要点：任务取 `task_semantics.binding_revision / content_hash`；方法取 `method_version / content_hash`；方法实例取 `plan_revision` 与 `parameters_digest`（不是 64 位十六进制时改用草案规范 JSON 的 sha256）；现网包里 kind=fact 的条目在 `visible_refs` 里一律写成 `observation`；没有现成修订号的对象取 1，没有现成哈希的取对象规范 JSON 的 sha256。
4. 校验规则：`semantic_revision` 是 ≥1 的整数（布尔值不算）；`content_hash` 是 64 位小写十六进制；`reason_refs` 去重键为四元组。

## 二、小字段的取值范围与子结构（BL-3）

采用冲突检查文档 §6 的封闭枚举，原样生效：假设风险、不确定性严重度各为 LOW/MEDIUM/HIGH；备选处置 CONSIDERED/REJECTED/DEFERRED；重规划建议复用九个决定类型；受阻代码 NO_USABLE_METHOD / EVIDENCE_INSUFFICIENT / AUTHORIZATION_MISSING / CAPABILITY_MISSING / STRUCTURE_UNSAT / OTHER（OTHER 时 detail 必填）；可恢复条件 new_method_admitted / evidence_updated / authorization_granted / human_resolved / plan_revision_changed；假设的 required_for 取九个决定类型名；修复子类 REPLACE_METHOD / PROPOSE_SUCCESSOR（H4 再扩）；绑定模式 REUSE_ACCEPTED / SHARE_ACTIVE。
Schema 文件必须写全 `$defs` 五个子结构，字段与 Python 类型逐字段一致。V2 第 12 节矩阵里的"REPAIR / REPLACE_METHOD"两行指的是 `payload.repair_kind`，不是决定类型。

## 三、仅解码的三个类型的载荷（BL-4）

H1 只解码、保存，随后以 `DECISION_NOT_ENABLED_IN_PHASE` 拒绝。载荷一律 `additionalProperties:false`：
- `REQUEST_EVIDENCE`：`{"questions":[{"predicate_key":str,"arguments":{…},"purpose":str,"blocking":bool}]}`，1–8 条。
- `REQUEST_HUMAN`：`{"question":str,"options":[{"key":str,"label":str}],"blocking":bool}`，选项 0–12 个。
- `PROPOSE_METHOD`：`{"method_proposal":{…}}`，内层用现有 `MethodProposal` 的编解码；现网合成器的 `<method_proposal>` 路径不动（V2 第 52 节 H6 才统一）。

## 四、黄金样例与 Schema 测试（BL-5、BL-6）

1. V2 第 13 节的对象只是字段骨架，不是合法样例；合法样例按第 24–30 节写完整载荷。valid 至少 11 个（六个可执行形态 + 三个仅解码类型 + 两个边界），invalid 每个拒绝码至少 1 个并标注 `expected_stage / expected_code`；H1 走不到的码用准入层单元反例满足。
2. 不引入 jsonschema 依赖。`test_planning_decision_json_schema.py` 断言：Schema 文件能通过 `importlib.resources` 读到；`$id`、`required`、各 `enum` 与 Python 枚举逐值一致；每个黄金样例由 Python 编解码器给出与标注一致的结果。
3. `contracts/schemas/*.json` 作为包数据进安装包（允许改打包配置）。

## 五、存储与幂等（BL-7、BL-8）

1. 迁移 19 是**一条**迁移、三张表。`PlanningRequestBinding` 增加 `intent_id: str`（以表为准）。
2. `attempt_ordinal` 从 0 起，同一请求每重问一次加 1。格式重试**复用**现网"提案不可读 → 带修复提示重问"机制，不另建；上限 1 次。同请求同序号不同原文 → 存储层身份冲突。
3. 新任务不继承协议绑定行；读不到行即旧协议。源码没有"克隆任务"接口，V2 第 8.2 节相应表述按此理解。

## 六、事件（BL-9）

新协议任务**两个事件都发**：旧的"规划被拒"事件字段与字节不变（修复轮、根评审修复、相同失败早停都读它）；新事件 `PlanningDecisionEvaluated` 载荷为 `decision_id, request_id, attempt_ordinal, decision_type(可空), status, rejection_codes, canonical_hash(可空)`。旧协议任务不发任何新事件。

## 七、请求包与提示词

1. 整数配对版本 3→4；包内字符串标签新值 `planner-package-hierarchical-v5`。开关关着的路径两个值逐字节不变。
2. WAIT / NO_CHANGE 只持久化、不编译、不产生计划修订；NO_CHANGE 不得走现网"声明无可用方法"的路径。
3. 编解码器是纯函数：请求编号、重问序号、原文哈希由调用方传入。理由文字上限用 4000，不用通用的 20000。
4. H0 发现的三处提示词冻结缺口，在加入第 8 版提示词的同一片补上。

## 八、开关与默认值

1. 两个开关互不相干：编排语义（现网已有）与规划协议（新增）。缺省任务两者都不出现在章程 JSON 里，章程字节与哈希不变。
2. SDK 侧：整个 0.13.x 默认值保持旧协议；是否在 0.14.0 翻转，待 H8 验收后另行决定。
3. Host 侧：钉到 0.13.0 候选版时，由 Host 为新建任务**显式选择**新协议。这是对 Host 仓库"测试阶段做完立即默认开"纪律的书面例外，理由是协议切换须先过真实模型回归。
4. 新模式哨兵：H1-A/B/C 三片保持 19 不变，不得为读协议开关新增调用点；H1 全程上限 22。

## 九、真实模型专项（H1-I）

1. 开发期冒烟：DeepSeek flash，0.12.2 验收的 7 道题各 1 遍，新协议开。通过标准：账本守恒与用量全知全真；官方通过的局都被系统宣告完成；展开、替换方法、受阻后转合成、等待四个场景各至少出现 1 次，以新事件为证；统计"请求过期被拒"的比例，超过两成就把 V2 第 34 节"无关变更精确重评"提前到 H2 首片。
2. 正式对比：放在 0.13.0 第三个候选版，用 Grok（与 0.12.2 成绩同模型可比），待 Grok 额度恢复后执行。
3. 做题程序为任务选择新协议的改动在做题程序侧，不在 SDK；起跑前重新冻结。

## 十、分工（用户 2026-09-18 指令）

Codex（DeepSeek flash）为主要劳动力，走 `slice_pipeline.sh`；Grok 只用于改热文件的接线片（H1-H）的实施或核验、阶段收官核验、正式真实模型验收。验收只认闸门报告、测试日志与 git 输出，不认代理回复里的数字。

## 追加裁定（2026-09-19 06:30）：请求包不新增第六个模型可见字段

**问题（H1-D 实施者备案 P2-8）：** 为了让"模型可引用条目清单"里任务、责任两类条目带上权威哈希，实施者在请求包里加了第六个模型可见顶层字段 `authoritative_refs`；V2 第 38 节只允许加五项。

**裁定：不追认。** 请求包在新协议下仍只加 V2 第 38 节的五项。任务与责任的权威哈希改由**收集器的可选入参**在构包时传入（调用方从任务网络/绑定处取值），不写进包体、不渲染给模型。

**理由：** ① 模型同时看到两份引用清单容易抄错来源，第 8 版提示词只教它从 `visible_refs` 照抄；② 线上字段名越少越好，新增字段要同步改第 14 节格式文件与提示词；③ "能否只凭已存包体重算清单"不是必需——V2 已规定每次请求把清单摘要存进请求记录，准入检查以**请求记录里存的清单**为准即可。

**对 H1-F 的影响：** 准入检查核对引用时，以请求记录保存的 `visible_refs`（及其摘要）为权威，不从包体重算。
