# C02/C03 prepare 与跨进程恢复后继

2026-09-06，NOT_RUN。保留已审叶 bfd56d99；同树后继分支
`feat/corpus-inference-prepare-recovery`。6999ec81合入主固定a6b915c7以消费已审C02入口，
原untracked参考副本移入ignored `corpus-public-seed/preserved-c02-references/`，未作为新代码提交。
主r18之后的native占槽期间仅源码，不运行子进程/模型/测试。

## 默认prepare接线

`corpus_inference_prepare.open_prepared_inference_fixture` 是 async context manager：
输入只有case_id、原setup文本、scenario_time、实际source Run/独立DB路径、principal及
公共builder policy/clock。没有gold、评分query、历史消息参数。

在公开Memory builder构造时绑定fixture authority；C02-19复用已审 `compile_setup` /
`apply_setup` / actual original pair inference，不重复修改词表或另造推断授权。
USER首次ingest保留group.user_analysis_lineage，ASSISTANT未指定lineage使用fixturefallback。
C03-20调用已审独立prepare。两者公开atomic receipt回读后**必经实际drain confirmed**
才能yield(manager, seed)，seed.analysis_jobs=applied并带真实report。低层旧helper仍保留
seed-only语义，不能当新完整prepare入口；未知case或未confirmed直接拒绝。
上下文退出/取消关闭manager，不启动productionworker，不清除任何job。

## 必要恢复文件，不是新权威ledger

`corpus_inference_checkpoint.FixtureRecoveryFile` 保存公开claim/application候选，
version1/domain独立、固定subject/case/HostRun/planhash/两原S1 id/hash/receipthash，
最多2条、1MiB、严格keys/schema整数/重复键拒绝。使用同目录临时文件+fsync+replace+
目录fsync。调用方独占本fixture进程；不提供多worker队列/并发覆盖协议。
proof路径不能与Host/MemoryDB相同或hardlink。正文只在ignored测试目录保留。

候选在SDK实际finalize**之前**保存，避免SDKcommit/调用方失去ACK后没有可重验proof。
候选文件不写成功标志，checksum仅发现字节变化，不授予真实性。
下个进程解码真实public DTO，再经已审exact来源/lineage/jobID/fixture envelope/
ACCEPTED receipt/空decisions检查及SDK幂等finalize。已applied由SDK确认；若候选尚未提交且
旧lease过期，False不得计成功，继续SDK实际claim/audit_pending恢复，不伪造新claim。
任何缺失proof+IDLE仍unconfirmed。恢复拒绝同request非fixture结果。

## 新控准备（全部NOT_RUN）

仅2参数场景，C02-19 after-finalize lostACK，C03-20 before-finalize进程退出。
原始Host/SDK确定性Provider只生成实际source group一次；评分Memory独立。
子进程走真实prepare/publicmanager/publicrunner，在公开finalize接缝退出OS进程，
无SDK私有SQL，无伪receipt。第二解释器从文件和双库恢复首job零executor、第二job一次；
第三解释器两job均零executor、相同public receipt与graph。
保留实际USERlineage、candidate/unverified来源，评分Host无source conversation。
C02追加重算file hash后换另一actualjob application，必须拒绝而非yield ready。

复用现有installed H078/M618和Python，不新env、无SDKoverlay。以后首跑仅此新文件，
默认共享资源锁2048MiB/180s；不复跑旧20setup或已审drain三绿。
未claim240质量、LLM提取、完整运行评测或最终合入ACCEPT。
