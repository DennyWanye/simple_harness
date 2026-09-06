# 非本人用途的本轮输入来源（实施契约）

2026-09-06。Host base 0bf324ff；Memory base d8d80d5c（0.6.18）。冻结 wheel、旧 S1/hash/turn 不变。本片已实现并完成限定源测试；未合主、未构建后继制品、未验收240。

## 边界与复用

所有240例使用共同政策：`simple-harness-memory-sdk/scripts/corpus_trusted_bindings/逐例绑定.md` §共同政策。原文 UTF-8 SHA256 为 `3963adb81d62aa5b64e95c6a7f1a4fb6d6dd76ce4390cb1ac4df9b72c0f6ed69`；该 hash 只是政策身份，不是授权。不读 gold/隐藏答案，不由聊天改变政策。

复用真实 signed-control request scope、S1 envelope/receipt、append-only foreground_turns 和 disclosure config。EvidenceItemAuthority/AdmittedEvidenceAuthority 证明来源和分类；HistorySourceOrigin 证明实际入场顺序，均不等于用途许可。既有 Host evidence resolver 复制请求的 item 字段，不能用于本入口签发分类。

首版仅支持实际本轮 USER S1 的完整 `/text`，`item_ordinal=1`、item_id=真实 delivery_key、USER/AUTHENTICATED_USER、identity UTF-8。认证控制 API 显式提交 `input_declaration={schema_version:1,kind:current_user|public_material,item_json_pointer:/text,text_sha256:SHA256(UTF8(text))}`。current_user 只证明模型本轮请求输入（PERSONAL 分类不变），不是给最终非SELF受众的正文披露授权；public_material 是完整本项显式公开声明。SDK 的 invocation_input_allowed 与最终正文可披露必须区分，不能将前者称通用 allowed。部分跨度、其它 pointer、旧 source、工具/助手/recall/short 一律不能借此许可。实际240中 current_user_message 与 trusted_setup.public_material 是两个独立项，不能拼成一个 PUBLIC /text；本片只验证单项，后续消费需分别绑定，不称240已接。后续两个独立项须逐项来源，不按相似文本推断。

## Host 原子事实

同一 S1+enqueue writer TX 验证 live authenticated snapshot、actual subject/primary、明确 disclosure_binding_ref 及原 current token。新 declaration 的来源必须在该 TX 首次入场，旧 S1 late-enqueue 不可升级。持久 turn schema 3，包含 schema2 的 atomic 标记和 `input_use`：完整声明、实际 S1/receipt hashes、主体/primary/delivery、控制 principal/authority/lease、原 disclosure token及实际 recipient/id/intended_audience/purpose、共同政策 hash。turn_hash 绑定它；input_use 不引用 turn_hash，避免循环。

旧 v1/v2 不回填；无声明的 SELF 路径字节不改，配置存在但未声明仍不能启用非SELF。相同 delivery 重放比较原事实，连接重建保留原 admission lease，不重新签发；换 text/kind/用途/token 均冲突。事务取消/故障不留半份证据。新 reader 重算 S1/turn/config/current head，不能仅因对象可构造就信任。origin reader 支持 schema3 的完整原事实，保留 atomic/legacy_before_only 原语义。

## SDK 与 physical 接线

新增独立 item-level current-input visibility 公共口；从注入的可信 Host authority 获取上述事实，核 exact item/S1/context/origin，复用原 suppression/lineage/同 snapshot 检查。普通 history 221/428、ordinary/candidate policy 不放宽；输入许可不流向旧历史/健康家庭/typed/short。Memory 不读 Host SQL，Host 不读 Memory SQL。不新增 Harness DTO。

Host 将当前输入与保留历史的原始有序 bindings 一次传入新公共口；SDK 在同一实际 SQLite snapshot 里只对 exact 当前 USER pair 应用输入例外，其余和其父来源继续原普通 reader。Host 返回实际 SDK history_visibility，不拼造 SDK snapshot，不用整 lane 可见替代。physical guard 检查实际请求来源/字节，慢 Memory 检查后再次比较原 token，G1→G2 必须拒绝而非替换。用途为非SELF时禁读/披露健康家庭记忆及其存在性/推断/别名/转交；配置或 public_material 声明不能重新分类旧记忆。

## 决定性 oracle（测试前固定）

1. 实际 signed-control + 新 S1 + exact 声明 + 非SELF config 原子持久；冷重开 exact 事实相同。无 live control、无声明、错hash/partial pointer、错主体/primary拒绝。
2. 同delivery重放不新增/不换lease；换文本/声明/原token冲突；旧S1晚排队不升级；after_evidence_insert故障回滚两者。
3. SDK 当前输入正向；旧健康家庭 history/typed/short 无此许可；伪 item/role/receipt/context、旧turn/foreign principal拒绝；原 source/memory suppression 及晚撤回继续拒绝。
4. actual Host physical 正向一次send；慢检查时 G1→G2、换request source或字节、源撤回均0send。继续保留既有 final original token fence。

分批只跑新失败/必要交互，不重复旧绿色。SDK 文件独立于 Singer Procedure 树，manager/port/root 小 hunk 后续合并；版本统一由主分配。首片 source facts 通过不代表 SDK/physical 已完成。

## 已固定公共接缝与审计

`build_human_memory_v7(..., current_input_authority=port)`；`MemoryManager.check_current_input_visibility(*, principal, disclosure_context, binding: CurrentInputBindingV1, bindings: tuple[HistoryBinding,...]|None=None)`。

- binding 为真实 `turn_id/request_id/HistoryEvidenceBinding(envelope,receipt)`，无 caller 分类或 grant。完整 tuple 为1..256且含 exact 当前 pair。
- authority port `resolve_current_input(*, principal, disclosure_context, binding)` 返回 `CurrentInputAuthorityV1|None`。Host 验实际 claim、完整配置 principal（deployment/household/actor/session）、原 S1/receipt/turn/config、原子 origin、当前 disclosure；Memory 先核实际注册 owner，再核 authority 完整 principal 和真实 admitted item。legacy ingest 的 actor/actor placeholder 不成为新用途授权。
- `CurrentInputVisibilityV1.invocation_input_allowed` 只指当前项模型输入；`final_audience_disclosure_authorized` 永远 False。`history_visibility` 是 SDK 同 snapshot 的完整逐项结果；其它字段含 request/binding/policy hash、authority_epoch、checked_at、authority_hash。
- canonical 为既有 `C=canonical_json`，`E(domain,payload)=SHA256(UTF8(C({domain,payload})))`。binding域 `memory.current-input.binding.v1`；request域 `memory.current-input.request.v1` payload精确为 `{principal:asdict(principal),disclosure:context.to_json(),binding_hash,bindings:[E("memory.history.binding.v1",item.to_json()),...]}`，顺序保留。authority/visibility/observation分别用对应 `memory.current-input.*.v1` 域；没有NUL迁移，也不改旧域。
- `CurrentInputObservationV1` 返回 attachment 或异常 attachment；wire字段 schema_version=1、invocation_ref_hash、request_hash、snapshot_hash、outcome、observed_at、operation、persistence_status。outcome为 input_usable/input_denied/rejected/failed/cancelled，实际返回结果才有snapshot_hash。operation固定check_current_input_visibility；persistence_status保持host_persistence_unverified。
- SDK复用现 observability sink 发出 `memory.current_input.observed`。Host `CurrentInputJournal` 直接调用 Manager、独立复建公开DTO/request hash，复用原 operation-audit.db/memory_call_attempts/findings；不存原文/原subject/异常消息。已捕获 SDK 拒绝/错误/取消可记SDKfinding，缺API/调用前取消不能归SDK。写失败不重试业务；settle失败留started/pending，读/启动写失败有稳定诊断。公开page限定此operation，all_operations_recorded=False、usage/cost=None；不是原 sealed OA1 family 全覆盖或独立授权ledger。

## 真实 physical 边界与合入

新 schema3 turn 使用可逆 request_id 编码 + 原token摘要的 `:input-v1:` authority ref，旧 wire 不改。默认 runtime factory 注入实际 HostCurrentInputAuthority，main checker 调用 SDK 批读并持久捕获观察。模型 persona 附同一共同政策（不是正文披露授权）。物理 Adapter 原guard继续读 USER/history/typed/standalone short 依赖；以 SDK公开 provider reservation 验 actual request_json/fingerprint，完整慢 policy 读取之后再次比较原配置token和原claim八元组，并再验 request bytes。不读SDK私有SQL，不换成新generation继续放行，不造假的SDKRun或handoff。两个数据库不是一个原子TX；该检查是当前Host事件循环物理交接前的最后复核，不宣称跨进程原子授权。

Host固定基0bf324ff的本分支交主合入；main现Carver A7/4k8k仍独立。Memory新源码与Singer Procedure源按 manager/port/root/sqlite_v5 的分离小hunk组合，主分配版本并更新公共快照。未变冻结M618/H078 wheel/版本/pins；未借此授权当前native中热替换。
