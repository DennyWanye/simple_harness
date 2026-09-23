# ARP-EXEC-1.0 执行准备度与可替换 Runtime 评审

日期：2026-09-23。结论：**CHANGES_REQUIRED**。可以开始来源盘点、合同修订和不依赖争议合同的工作；不能认定接口/字段已经全部冻结、可以无设计补充地完成全量实施。针对“可以替换成 pi”这一新增目标，现包尚未定义必要契约。

本次只做评审。附件中的施工指令、模型选择与验收命令作为被评审内容，没有据此执行产品开发。没有修改 SDK/Host、安装依赖、运行大批量测试或真实 Provider/UI 验收。

## 1. 实际检查与证据边界

- ZIP 解压后 69 个 manifest 列明文件的完整性检查 PASS（另有 manifest 自身）。
- 包结构检查 PASS：32 个 Schema 定义、330 个顶层字段 JSON Pointer、31 个结构样例；不代表每个嵌套/跨字段业务约束已验证。
- 32 个 seams 全部 PENDING_LOCAL_MAPPING；source-map 的 local_basis 为 NOT_READ_LOCAL_WORKTREE，dirty_fingerprint=null。
- 进行了两个窄合同反例检查：文档要求的 Pin kind 被 Schema 拒绝；给 RetrievalReceipt 增加续页字段被拒绝。
- 包中 116 项参考测试结果为作者报告，本次没有重跑；60 个 SDK 场景和 16 个 SDK mutation 在包中仍为 PENDING_SDK_EXECUTION。它们未执行不是“计划不能开工”的原因，已经发现的合同冲突才是。
- 本次直接读了当前 SDK 候选的 agents/runtime.py、agents/ports.py、agents/execution.py、agents/context/tokenizer.py；候选 HEAD 为 102ad3dfa2db38d575ea929d39ec5ed1561a71da。未冻结或审计全部 dirty 内容，因此不宣称完成 32 项本地映射。
- 当前 Host 的 ARCHITECTURE/index.md 已记录 V1.4 阶段集成与 TaskGraph 接线资料 READY；TASKGRAPH.md 已记录 taskgraph.23 主体完成及完整 acceptance 后置。本评审没有将旧记忆中的 H1-H 缺口当成当前事实，也不将这些新阶段成果当作 ARP 接线完成。

原始检查：[review-checks.json](./review-checks.json)，SHA-256：64805873a36cec4c6ec6ce73bf0eed99e1494c48c26d529a70d478958d88124c。文件内保留 ZIP 与关键被审文件 hash。

## 2. 必须先收敛的合同问题

### ARP-R01 / P1：引用类型的正文与 Schema 冲突

位置：implementation/FIELD-CONTRACTS.md:19–28、implementation/INTERFACES.md:15–21，对照 contracts/runtime-plane.schema.json:15–46。

正文要求 profile、journal_record、input_manifest、agent_turn、receipt、context、retrieval、tool_snapshot、skill_use、occurrence、completion_scope 等 Pin.kind；Schema enum 不包含它们。逐个用包内严格 validator 验证，全部报 ENUM。

实际影响：按正文生产 ContextManifest.profile_ref、检索来源、工具快照或内部 turn/receipt 引用，会被唯一结构合同拒绝。若统一塞入 agent/artifact，则需要正式映射与 resolver 规则，不能由实现者临时改类型。当前 ContextManifest 样例的 profile_ref、retrieval_receipt_ref、tool_snapshot_ref 都使用 kind=agent，故“样例通过”没有覆盖此问题；包已说明样例仅为结构正例，不能把它当业务证据。

修订要求：冻结一份统一 kind 词表、各字段允许 kind、精确 producer/resolver 和 revision/hash 语义；统一 Schema/文档/样例。增加少量“正确业务引用可接受、错误 kind 被拒绝”的合同反例即可，不需要为此提前跑全量回归。

### ARP-R02 / P1：历史检索分页输出缺少合同

位置：主计划 §4.4；implementation/HOST-DTOS.md:60；implementation/INTERFACES.md:44；Schema RetrievalReceipt:571–675。

计划规定扫描预算到达后返回 PARTIAL 和可续 cursor；session_history.search 接受 cursor。但输出 RetrievalReceipt 没有 next_cursor，也没有单独定义 SearchResult 封套。Schema additionalProperties=false，加入 next_cursor 会报 UNKNOWN_FIELD。HostResponse 的 next_cursor 服务 Host 管理查询，不能自动充当模型工具的返回字段。

实际影响：全历史扫描中断后，调用者没有正式返回值可用于继续扫描；不同实施者可能私自扩 DTO，跨层接口随之分叉。session_history.read 的 range receipt、has_more 与续读位置也只是文字描述。

修订要求：定义严格 SearchPage/HistoryReadResult，明确 next_cursor、scanned range、has_more/complete 的关系，以及 PARTIAL 原因、索引快照和授权变更后的失效规则。若选择复用原 DTO，必须指明当前实际类型与精确字段。

### ARP-R03 / P1：设置更新与 UI 读取尚未形成完整往返协议

位置：implementation/HOST-DTOS.md:13、22、77；Schema ContextSettingsCommand、ContextSummaryView、SessionView、HostResponse。

更新要求客户端发送当前 adoption revision 和 expected_effective_policy_ref；但 SessionView/ContextSummaryView 没有明确返回这两者。HostResponse 有泛用 view_revision，但未定义它在各 verb 下是否等于 adoption revision。文档又要求 UI 显示 configured/effective context、token count mode 和新生效 policy，而 summary 没有配置上限、计量模式或 effective policy ref。

manifest_ref 可以作为详情入口，但包没有锁定其读取 verb/DTO/当前读权处理；“沿既有 receipt 字段或以请求 ID 查询”也尚未绑定实际响应接口。只有 plan 文字不能保证前端第一次打开设置、改两次、断线后重试都能取得合法 CAS 输入。

修订要求：定义 ContextSettingsView（或明确扩充 summary）、每个 revision 的含义、effective policy ref、command receipt 关联/查询；明确尚无 Provider 请求时也可读写设置。以真实 Host 接口映射代替两种未决路径。

### ARP-R04 / 集成准备缺口：当前源码的 32 项映射未完成

位置：implementation/source-map.json:2–6；seams.json 全部 32 项。

actual_path、actual_symbol、actual_sha256 均未填，参考远端只读过部分文件。现阶段无法证明 planner/manager/worker/verifier 各入口、请求 prepare/reserve 事务、曝光导入、撤权与 GC 读写链都与计划假设一致。

这不要求目标 NEW 模块在开工前已存在，也不意味着新功能缺代码就是设计缺陷。需要补的是：哪些现有接口能复用，哪些必须改签名/提取 locked writer，具体由谁调用，原 UOW 是否真能容纳该操作，以及跨库桥的真实身份来源。普通命名和迁移编号可由实施者处理。

修订要求：基于当前候选源码/dirty fingerprint、Host wheel 与 TaskGraph 当前交接，填完映射并标注 EXISTING/MODIFY/NEW；已存在项填路径和符号，NEW 项固定目标签名及 caller。不能使用旧远端快照覆盖现有候选。

### ARP-R05 / 技术可行性证据缺口：真实 TokenMeter 尚未选定

位置：主计划 §3.3、附录 A3；当前 SDK src/simple_harness/agents/context/tokenizer.py:30 起、agents/runtime.py:178 附近。

计划要求对完整实际请求含 tools/framing/media 做 EXACT 或 CERTIFIED_UPPER_BOUND 计量，方向明确。但现有默认 tokenizer 是 UTF-8 bytes/2 加固定开销的估算实现；仅复用此名称不能获得针对当前真实模型/序列化模板的上界证明。包明确未调用真实 tokenizer/model，R04/R05 映射也未完成。

修订要求：明确首个受支持的 provider/model、renderer、计量实现版本、认证依据和不支持输入的处理。对已批准模型做一个有明确停止条件的最小验证；若不可获得可靠上界，应在计划中直接标明该模型不能启用 ARP 请求，而不是写完后才发现所有请求被拒绝。这里不要求先完成 512K 模型质量评测。

## 3. 这版计划到底支持哪一种替换

| 替换对象 | 本版设计状态 |
|---|---|
| 模型或 LLM Provider | 有设计路径；更换后重新冻结模型限制、Context 和预算，不能重发旧 UNKNOWN |
| Tool / Capability Provider / 已注册 Workflow executor | 有设计路径；需要具体 adapter 与原执行/授权/验收链 |
| 由 pi 接受一个委派子任务 | 可作为新增能力 adapter 设计；这仍是“调用 pi 的工具/子任务”，不等于替换主 Agent Runtime |
| 让同一种 Agent 在原生 loop 与 pi loop 之间选择 | 本包未定义；需要新增 RuntimeBackend 契约 |
| 活跃或 UNKNOWN 状态的 Agent 跨 Runtime 热迁移 | 本包未定义，也不能由 provider fallback 推导 |

依据：主计划 §0.1 统一 BaseAgent 执行内核；§2.2 要求 runtime_plane.py 只装配/委托原 Runtime；§6 绑定原 request/reserve；§13 修改原 agents/runtime.py/execution.py。RuntimeProfile Schema:233 起没有 engine/backend selector、adapter version、runtime capabilities 或 checkpoint format。

当前候选也确实通过 assemble_runtime → build_agent_execution_driver → AgentExecutionDriver → ReActLoop 装配；底层有 drivers 注册机制，不代表 ARP 已为另一套引擎定义可兼容的持久化与调用协议。

## 4. 如“能换 pi”是目标，建议现在补的边界

建议由 simple_harness 保留身份、Task/HTN/TaskGraph、预算、原执行账本、授权/OPS、Assurance 和生命周期。把“下一步模型/工具循环的执行实现”抽成可替换 RuntimeBackend；原生实现与 Pi adapter 都必须使用同一组受控服务。这是本次评审提出的方案，不是原计划已经包含的设计。

最少需新增并冻结：

1. RuntimeDescriptor/Binding：backend ID、实现与协议版本、制品 digest、能力声明、创建时绑定的 scope、配置 fingerprint；旧 session 固定绑定。
2. 生命周期契约：create/start/submit/control/resume/dispose 的确切 DTO、幂等 key、取消/停止/暂停语义与不支持时的显式结果；不要只列函数名。
3. RuntimeEvent：run/turn/request/toolcall 关联身份、单调 seq、generation fencing、终态、usage/raw result/source hash；说明断线后的补读、去重、乱序与背压。
4. 每次物理 Provider 调用的接管点：ARP compose → exact render/count → durable prepare/reserve → handoff → raw result/usage → actual exposure。pi 自动 retry/compaction/steering 引起的新调用也必须记账，不得隐藏在一个“大调用”里。
5. Tool bridge：只暴露获准快照，call ID 映射稳定，原 ToolGateway/OPS 执行；pi 自带文件/Shell/扩展能力若启用，必须纳入同一条权限与效果链。
6. State/checkpoint ownership：谁拥有 Journal，pi 的会话状态是否只为派生副本，checkpoint schema/version/hash，原始响应已到而 bridge 崩溃如何恢复；UNKNOWN 不能变成自动重试。
7. 切换政策：首版建议只支持新建 Agent 选择 runtime；既有 session 继续冻结绑定。跨 runtime 热迁移单列范围，需要 portable checkpoint 和工具/消息语义兼容证明。
8. Adapter 一致性验收：同一输入与工具合同，在 native/pi 两种实现上检查身份、记账、撤权、崩溃恢复、事件投影与外部效果不重复；不能以回答文本相似或子进程退出码代替。

Pi 官方文档已说明 SDK 可嵌入并定制 tools、资源加载与 session manager；RPC 可用 JSONL 子进程供其他语言接入。但这些 transport/API 能力不等于满足上述 ARP 持久执行合同。Python Host 可以接 RPC，也可以经 Node/Bun bridge 嵌入 SDK；需要按接管能力选择，不能仅因 RPC 能收发 prompt 就宣布适配完成。

- 官方 SDK：https://pi.dev/docs/latest/sdk
- 官方 RPC：https://pi.dev/docs/latest/rpc

检索日期：2026-09-23；具体实施时应固定 pi 的发布版本/制品。RPC 文档还明确 prompt 成功响应只代表命令被接受，agent_end 后可能继续自动工作；终态映射必须使用所选版本的真实生命周期语义。

## 5. 执行判断

**原有单 Runtime 的增强方案：PARTIAL，可以开始收敛合同与本地接缝；不能给出 100% 接口完备判定。**先修 R01/R02/R03，完成 R04 映射并解决 R05 首模型计量适配，随后集中主体实现，按用户要求把大批量回归放在主体完成之后。

**可更换为 pi 的 Runtime 架构：NOT_COVERED，需补设计。**如果这是必需目标，应在主体编码前加入 RuntimeBackend 边界及至少 native/pi 两个 adapter 的接管映射；否则本计划全部实施完成后，仍可能只有一个增强后的原生 Runtime。

不要求为本次评审重做 HTN/TaskGraph/Assurance，不要求把尚未实现的 60 个 SDK 场景先跑通，也不把作者参考测试视为生产接受证据。
