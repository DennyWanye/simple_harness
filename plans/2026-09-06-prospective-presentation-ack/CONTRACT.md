# A7 presentation / ACK 最小执行契约

2026-09-06；base5513be7d（主已merge2c8c57c6）。只复用现Host表，无SDK/wheel/schema变更。当前source契约，NOT_RUN。event发布成功receipt另缺口；A8循环只首次一次性到期，不扩递归。

## 事实与接缝

- `prospective_occurrences`已有claimed/presented/acknowledged/settled/overdue。`S5cStore.claim_occurrence`当前只claim，没有后四类writer。
- `ContextRouteLedger.record_snapshot_receipt`在state.db TX内固定snapshot及Host事实；新增同连接的presentation writer，禁止另开连接提交后补写。
- `occurrence_presented`是旧mandatory-inbox退出投影：注入/claimed/presented/overdue绝不写。只有durable ACK，或确证superseded/expired退出时同TX写；suppressed/FORGOTTEN不写。保留旧表名，不改其语义。
- `HumanMemoryV7Runtime.pending_occurrences`现读public inbox并筛状态/privacy/suppression；它没有按本Run当前DisclosureContext完成明确授权的契约。因此新occurrence协调器的prepare输入必须来自可信current-principal/current-run disclosure检查后的public entries，不接受模型提供entry/body/owner/run；接线缺此检查不得声称普通出站闭合。

## 最小接口（内部，不给模型新增authority）

新`prospective_occurrence.py`承载验证和事务函数，`S5cStore`对外薄入口：

1. `prepare_presentation(*, principal, sdk_run_id, entries)`：读取现canonical事件验证完整row hash/identity，保留同Run已presented但未ACK的entry；计算本次跨Runcount/overdue。最多实际注入的8条进入待提交组，不把page中未注入项记presented。普通读取与现在披露检查由可信caller完成；返回固定entry字节、原hash、历史head commitment，后续TX校验head无漂移。无写入。
2. `record_presentations_tx(db, *, prepared, snapshot_id, snapshot_receipt_hash)`：同snapshot TX核exact principal/Run、entry/head、真实snapshot关联；claimed+presented可在此TX首次落库。跨Run第3次同TX追加唯一overdue。每(owner,key,Run)最多1presented；同Run后续Provider保留消息但不增加次数。重放原snapshot复用，不重算为不同内容。
3. `ack_presented(*, principal, sdk_run_id, occurrence_key, current_entry)`：当前可信toolcontext提供principal/SDKRun；key严格64hex。先核真实presented/snapshot关联，同owner/key/Run原ACK优先返回原receipt；新ACK必须当前public inbox仍live且通过同Run当前披露检查。ACK和退出投影同TX，返回既有row身份/record_hash作为receipt_id/hash；fault回滚不可半写。模型只输入key，不能输入Run/owner/receipt。
4. `settle_acknowledged_tx(db, *, principal, sdk_run_id, terminal_identity)`：仅从同Run已持久ACK或旧已ACK待收口事实派生canonical settled；调用方先验证实际SDK终态并在原Host terminal TX调用。本函数不提交连接、不更新Memory。Memory triggered→completed仍由真实ACK轮analysis plan执行。
5. suppressed/superseded/expired由实际public inbox当前结果形成退出，不能凭失踪推断；suppressed不向Context披露旧body、不写退出投影。没有N次自动settle。缺确证状态保留pending/明确拒绝。

事件使用现`inbox_json`保存versioned完整body（public entry、principal、SDKRun/snapshot、prior证据、receipt绑定），record_hash使用独立Host域canonical hash；这不是第二ledger。所有读核字段+hash，不能只看phase/count。已部署claim旧格式明确保持可读；旧claim不是presentation proof。

## 出站与工具

`context_authority.prepare_snapshot`先取得current disclosure允许的entries及prepared组，以同一组生成protected message（每entry overdue字段）与snapshot fingerprint；提交snapshot时同TX落presented。snapshot replay不新增presented/overdue。现no_recall门继续只认退出投影，不能把canonical presented当processed。

`prospective_ack`严格schema仅occurrence_key，context_handler解析实际SDKRun及trustedprincipal；本地Host状态effect，不创建TaskScope，不需要项目目录，不授予project effect。使用现catalog注册和五路曝光机制，不建旁路工具协议。准确catalog注册hunk与main owner协调后接入。

## main/Hegel所有文件精确接线义务（我不覆写）

- 生产构造：给ContextAuthority注入occurrence coordinator和current principal/Run disclosure reader；当前公共inbox+普通source策略形成合法组，不能将未检查的全inbox直接标授权。
- 工具注册：调用新模块提供的register入口，handler context必须绑定实际SDKRun/当前principal；默认启用完成能力。
- `foreground_runtime`实际Host terminal事务：取得已验证raw SDKterminal identity后、原TX commit前调用`settle_acknowledged_tx(db, principal=..., sdk_run_id=..., terminal_identity=...)`；同TX后analysis入口可读取本Run真实ACK receipt来源。不能在finally或广播回调补写，不能把FAILED且无ACK的Run自动processed。

## 必要新控制（共享slot当前Singer优先，不重跑旧time/schema）

实际public inbox occurrence +真实Host snapshot：3个Run未ACK→3presented/1overdue/0退出投影/仍mandatory；第4Run ACK同receipt重放→terminal settle。snapshot before/aftercommit、ACK before/aftercommit恢复；wrongowner/Run/key/未呈现key拒绝零变化；第9条未注入不presented；重复Provider turn不增count；hash/关联篡改拒绝；fresh suppression在出站/ACK前拒绝且不生成退出投影。工具五路实际catalog可见和真实context dispatch。测试事实不冒充native/provider。

## Dirac b619预审收紧（2026-09-06，实施约束）

- 五路明确是direct_standalone/memory_standalone/continue_active/resume_existing/create_new，不是五种工具发现入口。
- current reader返回typed组：已授权可见exact entries，以及针对请求keys的确证状态suppressed/superseded/expired/unverifiable。missing/page缺席不得当退出。每项绑定真实principal/Run/当前披露身份；无法核验就拒绝，不静默丢组。
- actual Provider每次physical invoke前校验snapshot中实际注入的原组/entry hash与原披露身份；不得更换组/token。没有公开跨库epoch原子口，明确存在check-to-send窗口，不宣称跨库原子撤权。
- snapshot replay从原持久组重建/核验，不以新过滤结果替换；最终gate仍重新读当前源。呈现计数是snapshot提交次数（按Run唯一），不等于Provider收到/用户已见。提交后gate拒绝/取消仍可留下presentation；第3Run overdue的计数不描述为3次成功投递。
- 新ACK要求当前exactentry/sourcehash与本Run原presentation一致，变更拒绝。原ACK优先重放只返回已验证receipt，不返回旧body、不重新授予披露。两个Run并发ACK唯一赢家，另Run冲突；不能重复生成completion。
- settle只使用ACK原Run实际terminal；旧ACK待收口仅恢复同Run，禁止后继Run代盖。FAILED/CANCELLED表示实际Host终态，不等于Memory意图完成。
- 终态接口接现`PrimaryTerminalIdentity`与实际public SDKterminal（额外参数actual_sdk_terminal），调用现verify_sdk_terminal，禁止dict碰巧同Run冒raw证明。source模块仍依赖调用者在原TX从Host权威封套取得该identity；后续接线须实测。

### WIP具体main接线（无main写入）

- `ProspectiveOccurrenceCoordinator(store, read_current, clock)`；reader签名`(*,principal,sdk_run_id,requested_keys)->CurrentOccurrenceRead(owner,sdk_run_id,disclosure_identity_hash,visible,exits,unverifiable_keys)`。visible/exits均真实public DTO，exit必须明确public当前状态。原ACK重放仍认证Run，只回receipt；不把缺席变成exit。
- `ProductRunContextAuthority(...,occurrence_coordinator=coordinator)`；新组exactentry/count/index/disclosure放版本化Host snapshot关联payload；SDK source_revisions只含真实revision。原snapshot关联canonical presented原body用于交叉验证，重放不取新可见集合改写。
- `ProspectiveRequestGuard(sdk_run_id,coordinator,read_provider_context_use=stack.read_provider_context_use)`组成现`primary_guard`的额外await，位于实际delegate前。用public实际handed_off view绑定Run/request/fingerprint/snapshot，核原组protectedmessage后current-read；异常为Host专用ProviderRequestRejectedError子类，不改unknown/重发语义。没有handed_off证明不可凭Hostlatest查一个snapshot冒用。
- `prospective_ack_registration(coordinator=...)`返回现ProductToolRegistration；加入main projected_registrations，现direct kernel/catalog同步包含此tool，五种Context route均NON_PROJECT_EFFECT默认OPTIONAL，不授予项目effect。
- 原Hostterminal TX：`settle_acknowledged_tx(db,principal,sdk_run_id,terminal_identity=<read_primary_terminal_identity_tx返回>,actual_sdk_terminal=<本Run实际public terminal>)`。必须先有本TX可读的foreground terminal receipt/observation，verify_sdk_terminal继续严格比较raw namespace。后继Run不可调用代结算。


## 7e8预审P1及存储纠正（后继源码，未测试）

- Host专用Provider拒绝用keyword-only `public_message`；新增真正调用guard的missing-handoff拒绝控。不是仅构造异常。
- 外部current-read在snapshot writer TX前进行，record_tx仅核Host捕获组/head/receipt。physical guard仍再次fresh-read。没有用SQLite写锁等待Memory/SDK I/O。
- 不把opaque证明编码为revision。复用既有snapshot行的JSON存储列（历史名称`source_revisions_json`），新记录明确Host版本化envelope精确三键 `{host_snapshot_schema_version:2, source_revisions:<真实revision字典>, host_occurrence_group:<完整组>}`；这是一份Host snapshot关联payload，不是SDK source_revisions格式。SDK RunContextSnapshot只收到内层真实revision。旧行无版本wrapper按原格式/原receipt核验，不改旧hash。
- 新Host receipt hash覆盖原snapshot字段+真实revision+Host版本/group；读时重算完整receipt、组exactkeys、唯一顺序index、key去重、完整publicentry字节、canonical presentation关联。JSON重复键拒绝。无DDL、无第二ledger。
- main hunk仅coordinator/currentreader、projected_registrations、primary_guard组合、contextauthority构造和terminalhookfactory；queue仅record_sdk_terminal observer同TX，runtime只预先capture hook后透传。未碰closure_adapter/reserve_post_turn_attempt。
- 当前Host SELF披露是已实现lane，currentreader复用resolve_current_disclosure验证实际Run+旧/当前绑定；public read_occurrence_inbox返回owned事实。公开确证退出与未知分开，最大16x200页，tracked/requested缺席都unverifiable。不会把此实现称为任意受众通用许可。
- 源码准备阶段，a7-r1默认共享锁BUSY75，未启动child；首批4unit均NOT_RUN。真正runtime/五路/物理transport与撤权控制尚待实现和测试，不能称生产闭合。

### 当前剩余真实闭环点（不提升为完成）

A7 production适用范围通过Host foreground Run+SDK binding事实判定，覆盖该Run五种Context route；没有primary turn的child/workflow保留原authority链，不能因新增primary reader让全部子Agent失败。非本owner的已绑定Run拒绝。此判断不是工具调用授权，ACK仍必须有本Runpresentation。

除待测main接线外，派生assistant的后继history仍须继承本Run实际mandatory occurrence组；当前read_run_dependencies仅原recall来源，不可将新组静默视为recall=[]完整来源。保留snapshot完整publicentry/事件hash及实际SDKsnapshot/request绑定后，可用现public inbox当前exactkey验证其可见性（缺席unknown），不造typed recall或SDK新字段。该history/API接缝尚未实现，未经闭合不得把本叶合成完整隐私产品。

### 78d5c7a0 review delta — 2026-09-06

- Register the actual `ServiceContext` whitelist and dataclass slot. The existing
  human-epoch production `_build_product_sdk_runtime_stack` control now requires
  the real coordinator and projectless/direct `prospective_ack` registration.
- A 16-page inbox prefix with a remaining cursor raises
  `s5c_occurrence_inbox_scan_incomplete`; no empty mandatory group is returned.
  This bounded reader does not promise resumable scanning beyond the cap.
- Both Host codec version tags require exact `int`, rejecting bool/float aliases.
- New/changed controls are initially NOT_RUN; prior A7 tests remain unverified.
  The cap control isolates public page transport with synthetic pages; it does
  not claim 3201 real SDK mutations or native/Provider coverage.

First narrow batch `a7-r2` against ab40886c: **2 PASS / 1 FAIL / 4 deselected**,
2.63s pytest; PG88056 exit1, 398144KiB peak, remaining[]. Cap transport and codec
passed. Real factory initialization stopped at existing candidate verification:
Host fixed base pins M616 while isolated public test target is M617. This is not
factory wiring acceptance; verification remains intact. Raw:
`.local-test-evidence/2026-09-06/prospective-timer/a7-r2/{command.log,resource.json,identity.json}`.
Dirac accepted the two P1 source deltas and exact-int direction (limited source
review, not whole A7). Outer codec assertion is further narrowed to exact
`storage_shape_invalid`, so an unrelated receipt mismatch cannot satisfy it;
that stricter assertion is NOT_RUN. Slot released to Hegel, no immediate retry.
