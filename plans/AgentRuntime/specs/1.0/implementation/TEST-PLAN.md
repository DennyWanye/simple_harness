# SDK验收映射（不是已通过的测试）

先BODY_WIRED，后按本表统一执行。actual_nodeids与断言覆盖需本地登记；名字存在/collect成功不代表行为通过。

| ID | 目标测试 | 决定性断言 |
|---|---|---|
| R01 | `tests/agent_runtime_plane/test_lifecycle.py::test_create_replay` | 同body一Agent一session；异body冲突；无新identity |
| R02 | `tests/agent_runtime_plane/test_lifecycle.py::test_submit_replay` | 原receipt重放；Journal不重复；异hash拒绝 |
| R03 | `tests/agent_runtime_plane/test_lifecycle.py::test_crash_not_destroy` | 同session和未决请求；不删除临时库、不生成新原始调用 |
| R04 | `tests/agent_runtime_plane/test_lifecycle.py::test_turn_not_session` | session不销毁；history延续；第二Agent完全隔离 |
| R05 | `tests/agent_runtime_plane/test_lifecycle.py::test_close_draining` | 拒绝新输入；旧raw/费用仍入原ledger；不因CLOSED推断未应用 |
| R06 | `tests/agent_runtime_plane/test_lifecycle.py::test_destroy_unknown` | DRAINING+明确blocker；无unlink、不释放UNKNOWN hold |
| R07 | `tests/agent_runtime_plane/test_lifecycle.py::test_delete_index` | 可重入完成PURGED；DB/WAL/SHM不存在；墓碑保留 |
| R08 | `tests/agent_runtime_plane/test_lifecycle.py::test_late_embedding` | 费用持久但向量拒绝写入；不能mkdir复活 |
| R09 | `tests/agent_runtime_plane/test_lifecycle.py::test_wrong_root` | 拒绝访问/删除；外部目录不变 |
| R10 | `tests/agent_runtime_plane/test_lifecycle.py::test_restored_backup` | quarantine；不自动读私有history、Skill授权或执行旧调用 |
| C01 | `tests/agent_runtime_plane/test_context.py::test_formula_dynamic_n` | 公式实际成立；N随A-E/recall变化；不按固定20轮 |
| C02 | `tests/agent_runtime_plane/test_context.py::test_recent_contiguous` | 停止于大组；不越过挑旧小组；current tail保留 |
| C03 | `tests/agent_runtime_plane/test_context.py::test_tool_protocol` | 按call_id关联；不丢缺失result或拆tool group |
| C04 | `tests/agent_runtime_plane/test_context.py::test_recall_overlap` | 先去重再装recent；不回填造成震荡 |
| C05 | `tests/agent_runtime_plane/test_context.py::test_large_required` | 无Provider handoff/reserve；明确REQUIRED_CONTEXT_TOO_LARGE |
| C06 | `tests/agent_runtime_plane/test_context.py::test_full_wire_count` | 全部计量；任一模型不支持或未知成本不放行 |
| C07 | `tests/agent_runtime_plane/test_context.py::test_frozen_retry` | 原input hash/ContextManifest不变；新变化仅下一合法请求 |
| C08 | `tests/agent_runtime_plane/test_context.py::test_policy_change` | 旧request不变；新请求绑定精确effective_policy_ref |
| C09 | `tests/agent_runtime_plane/test_context.py::test_exposure_not_prepared` | 不签已曝光；实际input关联后才可被Verifier引用 |
| C10 | `tests/agent_runtime_plane/test_context.py::test_oversized_tool` | 原文完整、REFERENCED合法；不会悄改旧frozenmessage或预算 |
| M01 | `tests/agent_runtime_plane/test_memory.py::test_all_history` | 最早记录可精确和语义检索，不限最近2000 |
| M02 | `tests/agent_runtime_plane/test_memory.py::test_index_lag` | 原文可恢复，index partial可见，续做不丢记录 |
| M03 | `tests/agent_runtime_plane/test_memory.py::test_real_embedding` | 真实model fp/dim/usage；拒绝hash/随机向量替身 |
| M04 | `tests/agent_runtime_plane/test_memory.py::test_vector_spaces` | 只查询匹配空间；NaN/zero/dim错误具名拒绝 |
| M05 | `tests/agent_runtime_plane/test_memory.py::test_coverage_hole` | highwater不跨洞；不以MAX(seq)声称完整 |
| M06 | `tests/agent_runtime_plane/test_memory.py::test_cross_session` | 读取前拒绝；不泄漏其他session的正文和存在性 |
| M07 | `tests/agent_runtime_plane/test_memory.py::test_revoked_memory` | 候选可内部过滤但正文不披露；不把历史命令当新授权 |
| M08 | `tests/agent_runtime_plane/test_memory.py::test_retrieval_timeout` | PARTIAL与resume cursor；不回空集说无历史 |
| M09 | `tests/agent_runtime_plane/test_memory.py::test_fts_unicode` | 不SQL注入、不拼MATCH语法；短词明确fallback；原文bytes不变 |
| M10 | `tests/agent_runtime_plane/test_memory.py::test_degraded_and_empty` | COMPLETE_EMPTY与LEXICAL_ONLY/PARTIAL/ERROR分开；默认profile有实际语义能力 |
| K01 | `tests/agent_runtime_plane/test_capability_skill.py::test_provider_selection` | 过滤先于priority；单provider不额外LLM路由 |
| K02 | `tests/agent_runtime_plane/test_capability_skill.py::test_no_provider_fallback_unknown` | 不通过fallback产生重复真实调用 |
| K03 | `tests/agent_runtime_plane/test_capability_skill.py::test_skill_archive_security` | 隔离阶段拒绝；不执行scripts或写root外 |
| K04 | `tests/agent_runtime_plane/test_capability_skill.py::test_progressive_load` | 只目录摘要＋所选内容计费；无全库塞context |
| K05 | `tests/agent_runtime_plane/test_capability_skill.py::test_skill_execution_pin` | 仍执行冻结版或拒绝；不执行未审字节 |
| K06 | `tests/agent_runtime_plane/test_capability_skill.py::test_dependency_cycle` | 拒绝；有限图解包，不能递归自授权限 |
| K07 | `tests/agent_runtime_plane/test_capability_skill.py::test_promotion` | 模型不能自晋级；合法policy/eval精确bundle才能admit |
| K08 | `tests/agent_runtime_plane/test_capability_skill.py::test_suspend_hot` | 新handoff拒绝；旧费用/结果可导入，不重新绑定latest |
| K09 | `tests/agent_runtime_plane/test_capability_skill.py::test_workflow_is_tool` | 不增加Mission/TaskGraph权威；步骤原执行身份可恢复，副作用沿OPS |
| K10 | `tests/agent_runtime_plane/test_capability_skill.py::test_private_trace_to_skill` | 默认仅scope候选；脱敏/批准与eval后才可分享，无隐式全局记忆 |
| T01 | `tests/agent_runtime_plane/test_tools.py::test_exact_catalogue` | 本地namespacedidentity不冲突；server annotation不作授权 |
| T02 | `tests/agent_runtime_plane/test_tools.py::test_unexposed_tool` | 拒绝；目录可见也不等于本请求可调用 |
| T03 | `tests/agent_runtime_plane/test_tools.py::test_schema_and_auth` | 结构权限与合法domainparams区分；不误提升scope |
| T04 | `tests/agent_runtime_plane/test_tools.py::test_sandbox_write` | 实际边界阻止；平台不支持则CAPABILITY_UNAVAILABLE |
| T05 | `tests/agent_runtime_plane/test_tools.py::test_external_effect` | 只生成原意图/Review/授权；未完成效果不得成功 |
| T06 | `tests/agent_runtime_plane/test_tools.py::test_large_tool_result` | 合法ref与boundedtext；binary不塞无限字符串 |
| T07 | `tests/agent_runtime_plane/test_tools.py::test_schema_update_midcall` | 按发起请求的exactref处理；拒绝未授权新实现 |
| T08 | `tests/agent_runtime_plane/test_tools.py::test_resource_deadlock` | 等待时释放不需要的槽；收据/import/取消仍推进 |
| T09 | `tests/agent_runtime_plane/test_tools.py::test_truth_not_exitcode` | 仅tool成功，不自动Acceptance或Mission完成 |
| T10 | `tests/agent_runtime_plane/test_tools.py::test_all_entrypoints` | 同authority/accounting/fence生效；无旁路 |
| I01 | `tests/agent_runtime_plane/test_integration.py::test_execution_migration` | 新建/升级/rollback/FK/checksum/幂等reopen；旧bytes保持 |
| I02 | `tests/agent_runtime_plane/test_integration.py::test_multi_db_import` | 相同source/call身份重入，不新计费/不丢正式记录 |
| I03 | `tests/agent_runtime_plane/test_integration.py::test_gc_liveroot` | 临时DB可按条件删，正式pin仍读得到；无提前GC |
| I04 | `tests/agent_runtime_plane/test_integration.py::test_host_current_history` | 显示正确N/reserve/recallstatus；旧content不由latest覆盖 |
| I05 | `tests/agent_runtime_plane/test_integration.py::test_host_reconnect` | 恢复有权限元数据，stale cursor具名拒绝，坏数据不填0正常显示 |
| I06 | `tests/agent_runtime_plane/test_integration.py::test_legacy_compatibility` | 旧config/prompt/request/event字节不漂移；缺newbinding不冒充legacy |
| I07 | `tests/agent_runtime_plane/test_integration.py::test_default_new_profile` | 新默认ARP；旧活动session不切；未完成TG不被打开 |
| I08 | `tests/agent_runtime_plane/test_integration.py::test_new_domain_registration` | Core不改domain if；typedinput/output/原验收成立 |
| I09 | `tests/agent_runtime_plane/test_integration.py::test_native_platform` | importidentity、文件锁/删除与tokenizer/FTS实测；不拿Linux冒充Mac |
| I10 | `tests/agent_runtime_plane/test_integration.py::test_real_model_matrix` | 12/12硬不变量，每类≥2/3业务成功、总≥10/12；失败保留 |
