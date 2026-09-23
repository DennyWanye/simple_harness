# 代码入口、输入输出与事务 owner

接口是目标，等价已存在实现优先复用。Schema对象在`contracts/runtime-plane.schema.json`完整定义。以下internal snapshot只由producer构造，不向LLM开放。返回成功或具名`RuntimePlaneError(code,stage,source_refs)`；不使用bool/空dict表示缺来源。

## 1. 额外内部快照类型（只用于函数，不增加外部wire）

```text
ContextSourceSnapshot = {
 original_request_key: 原runtime request键,
 session: SessionView, profile: RuntimeProfile,
 effective_policy: Policy, effective_policy_ref: Pin(policy),
 model_limits: ModelLimits, sections: tuple[Section],
 groups: tuple[JournalGroup], required_group_ids: tuple[str],
 tool_snapshot: ToolSnapshot, active_skill_refs: tuple[Pin(skill)],
 original_turn_ref: Pin(agent_turn), source_read_set_ref: Pin(artifact),
 authority_receipts: tuple[AuthorizationReceipt], root_incarnation: str
}
ContextAssemblyInput = {source:ContextSourceSnapshot, authorized_recall_candidates:tuple[RecallItem],
                        retrieval_receipt:RetrievalReceipt}
ContextCandidate = {manifest:ContextManifest, rendered_request_ref:Pin(artifact),
                    prepared_blob_roots:tuple[Pin], final_token_receipt_ref:Pin(receipt)}
CompleteSessionDisposal = {
 session_id,generation,root_incarnation,closed_receipt_ref,
 active_call_refs,unknown_call_refs,pending_import_refs,live_temp_root_refs,
 ledger_highwater,root_set_hash,index_guard_receipt_ref,read_scope_receipt_ref,
 complete:bool, captured_at_ms:int
}
```

`complete=false`不能进入Purge许可constructor；没有source receipt不能将集合填空。reference `can_purge`只验证已经正确生产这些计数后的规则，不承担实际读取。root/read证书引用真实authorization/FileGuard持锁证据，FileGuard receipt是当前实例生命周期局部证明，不作为永久授权。

## 2. 函数与责任

| 目标函数 | 输入→输出 | 正式来源/是否写 |
|---|---|---|
| RuntimePlaneService.bind_agent_profile_locked | original_agent_binding, approvedprofile, creationreceipt→SessionView(CREATING) | 原executionUOW同txn写protocol/profile/adoption/session CREATING；不调模型 |
| RuntimePlaneService.prepare_provider_context | turn_id,ordinal→原FrozenProviderRequest＋ContextManifest | 查已冻结优先；否则读源/compose→原request事务；不自行发送 |
| ContextSourceReader.read_snapshot | originalowner/session/request→ContextSourceSnapshot | 原Task/Assurance/Input读取，standalone明确N/A；不签权限 |
| RequestTokenMeter.measure | 原Provider实际renderedbytes＋ModelLimits→原TokenReceipt | exact或certifiedbound；invalid不做近似继续 |
| ContextComposerV2.compose | 完整ContextAssemblyInput→ContextCandidate | 纯选择/render/count，无DB/Provider副作用；源最后复核归freeze |
| RuntimeExposureImporter.import_input | 原Provider真实调用输入回执→原Assurance曝光桥 | 原execution事实先存，后向编排导入；匹配request/view不能猜 |
| SessionIndexCoordinator.enqueue_closed_group_locked | originalclosedgroup/session/fp→job receipt | 原JournalUOW同txn；jobpayload绑定sourcehash |
| SessionIndexWorker.process | durablejob＋realEmbeddingPort→原embeddingreceipt＋indexACK | 外部调用无DB锁；持FileGuard核对后局部index commit，再central ACK |
| SessionRetriever.search | 固定SessionAccess,query,exclusions,cursor,limits→RetrievalReceipt＋RecallItem | 全范围候选、分页、当前授权；不接受modelagent_id |
| SessionRetriever.read | 固定SessionAccess,seq range,byteslimit→原Journal view＋range receipt | 原record exacthash/scope/原retention；index不是唯一来源 |
| CapabilityResolver.resolve_requirement | 原owner-contract/key/inputmanifest＋currentdeploymentread→CapabilityBinding | filter硬门，稳定选择，原execution写binding |
| ToolExposureService.prepare | 原session/request＋requestedToolRefs→ToolSnapshot | 生成真实map/modelschema，记录preparation；实际曝光由inputreceipt |
| ToolDispatchAdapter.dispatch | originalrequestToolSnapshot＋nativecallid＋rawargs→原toolreceipt/ToolResultView | 原ToolGateway准入；效果只原OPS，不直接调用 |
| SkillRegistryCommands.install | authenticated command＋bundleartifact→QUARANTINED entry | actualdecode/pathhash/依赖审查，原registryUOW |
| SkillRegistryCommands.admit | SkillAdmitCommand＋fixedcaller→activation receipt | originalAcceptance/evalpolicy/currentauth核对，epoch同txn |
| SkillRegistryCommands.suspend | SkillSuspendCommand＋fixedcaller→activation receipt | 精确版本＋epoch；旧call原事实保留 |
| SkillUseService.load | 固定session/turn＋exactskill/files→源view＋原loadreceipt | 只取获准immutablefile；不扩大E/消息重复计数 |
| SkillUseService.execute | 原ToolCall＋exactskill＋typedinput→originalexecutor result | 先locked prepare call/reserve→sameUOWuse，无新运行账本 |
| RuntimePlaneService.update_context_settings | ContextSettingsCommand＋fixedcaller/command_id→adoption receipt | 同UOWexpectedCAS，持久adoption新rev；原frozeninput不改 |
| SessionDisposalReader.read_complete | session/root＋持有FileGuard→CompleteSessionDisposal | 所有来源实际完整读取；incomplete具名拒绝 |
| RuntimePlaneService.destroy_session | SessionDelete＋fixedcaller→SessionView | lock→DRAINING/generation→原收敛→prove→PURGING→文件→PURGED |
| RuntimePlaneService.resume_pending | 原runtime启动/tick＋owner→扫描CREATING会话＋处理due INDEX/PURGE/BIND_IMPORT | 一套原eventloop，不建第二scheduler；lease只重领协调不证明效果没发生 |

## 3. Store方法（同原UOW connection，writer不自行commit）

`put_profile_locked`, `put_protocol_marker_locked`, `create_session_locked`, `get_session_exact`, `cas_session_locked`, `append_policy_adoption_locked`, `read_effective_policy`, `find_frozen_context`, `put_context_locked`, `put_catalog_revision_locked`, `cas_catalog_activation_locked`, `bump_registry_epoch_locked`, `put_capability_binding_locked`, `put_tool_snapshot_locked`, `put_skill_use_locked`, `enqueue_job_locked`, `claim_job_locked`, `finish_job_locked`, `put_blob_root_locked`, `transfer_blob_root_locked`。

命名SQL在queries.json。每个writer须：正确定义caller→原事务→幂等原key/body比较→真实来源/外键/当前state→写行＋原receipt/event。缺同源receipt时不创建fakeevent当证明；recorder必须挂在真正发生的执行/授权writer上。

SessionStore `put_group`, `put_chunk`, `put_vector`, `mark_group_complete`, `search_fts`, `iter_vector_pages`, `get_marker` 使用独立partition connection和同一FileGuard；索引ACK不能加入另一库事务作原子承诺。

## 4. 收尾边界

原close要接实际journal/call/runtime owner状态，close单独可能先返回CLOSING；destroy不可将它当已无在途。原result/usage collect永远先保真，再由guard决定是否采用，不因ARDRAINING丢掉计费/输出。无空间写index时允许Journal继续至原磁盘政策上限；达到原持久空间上限整体有界拒绝新输入，不丢记录假成功。
