# C05 审批与缺失证据分界

2026-09-07，主H0710/M619/S0313。初始f10d0624（a0dc37eb）；修复6f22f495/598918c6（30faa938/244a9e9e），后续scoring消费e442993b（a26307c7）。

共3个唯一控制分批通过。r1 **1 PASS / 1 FAIL，1.89s**：真实empty search与已索引effect缺失、terminal result缺失的分界通过；实际pending decision已OPEN/WAITING，但public read_effect返回None，原测试/审批错误假设已有EffectRecord。

查明SDK REQUIRE_USER发生在prepare_effect之前，改用公开持久requested/waiting审计与proposal、实际成功Provider响应的完整call/args核对OPEN decision；独立PendingCallProof不冒充EffectRecord、不复算内部ID、不写SDK私库。audit.request_hash未导出，明确不伪填，以实际decision与实际response fullargs全等及唯一审计关联核对，决策前后回读不变。

r2 **2 PASS，2.75s**：只复原pending红，加一个非空候选policy=False负控。实际内部ID与原rawID不同，错turn/错args拒绝且原decision不变，恢复实际完整调用后公开allow成功；另一控先真实非空candidate/正常policy可见，再让读取口返回无typed原因的False，必须报unverifiable。真实空结果仍是首批已通过的独立路径，未复跑。

不存在的verify_route_receipt已换为原exact_receipt四字段，再核原physical root authority。pending控制的首context_route不经过write/closure深分支，因此整个marker/closure审批仍由C05实际main首批另验，不能以这3控声称整链完成。公共审计proof只核所需调用关联，不代表完整operation audit覆盖。

r1 PG83853 exit1/2.796s/peak199648KiB/minDisk4526MiB；r2 PG84899 exit0/3.644s/peak202464KiB/minDisk4457MiB；均remaining=[]、cleanup=null、stop=null。原FAIL保留，无资源拦截，无真实模型/原生，防熄屏持续。

命令：当前target优先PYTHONPATH，共享run_resource_bounded.py 2GiB/180s；r1 pytest -q backend/tests/quality/test_corpus_c05_authority.py（当时两控）；r2仅test_actual_pending_approval_rejects_foreign_turn_and_wrong_args_before_allow及test_nonempty_actual_candidate_policy_false_is_unverifiable。

| 本机原始证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-c05-authority/r1/command.log` | `317739498b3645eee9a98a2e3dcbb98643279dd2963681a425e28d9847d3c3b2` |
| `.local-test-evidence/2026-09-07/corpus-c05-authority/r1/resource.json` | `990703b1f7998146985835e613e7f340c96e1c103511c99fc1e035a46491f6f0` |
| `.local-test-evidence/2026-09-07/corpus-c05-authority/r2/command.log` | `f96b6de1451e532a112a29f61af67d2f439727df4064cc1c1d3740005435b1b4` |
| `.local-test-evidence/2026-09-07/corpus-c05-authority/r2/resource.json` | `929f6551e614e1c19cd82c7aa36a632ffa6c1fa05e5f49d4a71ebb9598a8f622` |
