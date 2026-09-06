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
