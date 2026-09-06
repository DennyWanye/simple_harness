# 草稿发现与失败来源：六项新增控制结果

终审更新：Dirac限定ACCEPT Host242f1688+测试ade0d79a/结果8cf0bc66、SDKf03dab0。已独核r4两实际遗忘负控、source-state及5份本批raw/carrier hash，确认原误绿撤回，r1有效4+r4有效2=6unique。前台是夹具显式接生产guard函数，不冒真实main物理Provider链；closure是实际适配器与MockTransport物理边界。不是新installed/native/完整TC-HM04；主共同current-input组合1另计。下文待终审为原交审时记录。

2026-09-06。SDK业务固定f03dab0，Host业务6da7dc39及closure修订242f1688；后续147881a7/7798fd92/ade0d79a只修新增负控诊断、时钟调用和实际拒绝断言。结果为6个唯一源码覆盖控制通过，待Dirac终审，不是新制品、installed/native或完整TC-HM04通过。旧13不重跑，未build/install/模型调用。

| 批次 | 结果及有效边界 | 资源 |
| --- | --- | --- |
| r1 | SDK2通过0.42s；Host显示3通过、closure导入未入业务。前台forget发现时钟二次调用误绿，已撤回；本批只保留4个有效控制 | PG47158 exit1，10.234s，峰290768KiB，remaining[]/cleanupnull |
| r2 | 补当前HEAD稀疏源132KiB后仅closure1失败；已进入真实业务，fixture异常尚未完成forget | PG47366 exit1，4.715s，峰412752KiB，remaining[]/cleanupnull |
| r3 | 仅closure1诊断确认semantic_clock()()对float再次调用。原拒绝数/零发送不能当来源门已验 | PG47795 exit1，4.715s，峰412976KiB，remaining[]/cleanupnull |
| r4 | 仅两受影响负控2通过5.99s；不重复其他4绿 | PG48221 exit0，6.84s，峰424016KiB，remaining[]/cleanupnull，最低磁盘3569MiB |

有效6项：SDK完整UNBOUND预览/精确history binding/forget（含256字节完整page预算和游标）；SDK非SELF/非法界限/无匹配；Host未知id真实发现→原步骤使用→首个draft成功；Host实际文件冲突产生FAILED前缀→零success/不REVISED及伪归因拒绝；前台真实forget→draft不可见→明确primary_history_disclosure_rejected；后置closure独立草稿forget→旧三lane仍真实可见，但新lane拒绝导致0HTTP、not_sent和closure_request_disclosure_rejected。

两个负控均核public suppress返回真实decision的request/target，再通过公共check_history_visibility核该draft不可见；marker在真实调用完成之后。前台夹具显式执行生产check_runtime_dependencies，精确匹配来源拒绝，不能仅以任意异常少一次Provider调用当通过。closure通过实际RunBoundInvoker/固定主provider resolver/Httpx MockTransport统计物理HTTP；不是外部真实Provider请求。

命令统一经`/Users/denny/projects/simple_harness-test-resource-cleanup/scripts/run_resource_bounded.py --evidence-dir <新批次> --rss-mib 2048 --seconds 180 -- <python> -B <carrier> ...`，未覆盖默认lock。Python为primary-candidate下`.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python`；原H078/M618独有target仅作依赖，本Memory源码路径优先。r1载体run_discovery.py顺序SDK2/Host4；r2/r3仅test_procedure_draft_closure.py；r4仅test_procedure_discovery_runtime.py的[True]及test_procedure_draft_closure.py。没有新env，临时vendor由既有carrier finally恢复，Host tracked clean，资源释放已告主/Hegel/Carver。

剩余边界：当前候选完整多页未本批运行；wholeFAILED Run无完整conversation group、程序缺陷/用户纠正归因未闭合。与nonSELF e500556联合新增current-input hash allowlist接缝由主修复并另跑组合1控，此处6项不能替代它。不改complete冻结规则、root、旧阈值或M618制品；主统一后继版本。

## 本地原始证据索引

以下仅本批文件hash，不重扫旧绿色制品或旧13证据。原始日志/库保持ignored。

| 路径 | SHA-256 |
| --- | --- |
| `.local-test-evidence/2026-09-06/procedure-scope/discovery-r1/command.log` | `abddb04e899e42f34b65ffca4228f2839cae16fceecb36917f0b9c58106e75fa` |
| `.local-test-evidence/2026-09-06/procedure-scope/discovery-r1/resource.json` | `aefc56cec5c1c18c76f2a7b6284749461f08cf56703e99469a2c8f48a0c5fa45` |
| `.local-test-evidence/2026-09-06/procedure-scope/discovery-r2/command.log` | `956d109629349917fa308a39c9055e65d08d3690fdea38a9d343700d582be578` |
| `.local-test-evidence/2026-09-06/procedure-scope/discovery-r2/resource.json` | `cdafe351e028fe8d5ae3118e9911b09d25fbfb0b2195024aa856df341935f537` |
| `.local-test-evidence/2026-09-06/procedure-scope/discovery-r3/command.log` | `b1feebc8712b15203c73e74bb15c5c393b97dba5ed356411e543069e34140d48` |
| `.local-test-evidence/2026-09-06/procedure-scope/discovery-r3/resource.json` | `bff53acca25d431be597da91fce2beabd16eca1b51bc03b19b4b0c8759b46964` |
| `.local-test-evidence/2026-09-06/procedure-scope/discovery-r4/command.log` | `3ff350b9635a78ee8b2deae324a73348056768c0581022ab1d1c132693174b78` |
| `.local-test-evidence/2026-09-06/procedure-scope/discovery-r4/resource.json` | `140cb25a011974fe1932e2b0b54116995fa9dbf5bf53af7d11f3825ae184fe6f` |
| `.local-test-evidence/2026-09-06/procedure-scope/discovery-r1/sdk-batch/source-state.json` | `fb4a60eb1495d40fae2e497d84698d9ca374e9461d04b504338bad15aaaf3e9c` |
| `.local-test-evidence/2026-09-06/procedure-scope/discovery-r1/host-batch/source-state.json` | `fb4a60eb1495d40fae2e497d84698d9ca374e9461d04b504338bad15aaaf3e9c` |
| `.local-test-evidence/2026-09-06/procedure-scope/discovery-r4/source-state.json` | `7de23331dd685101d7963439092c0cca716fa8b54945fc37b8df4d3696fd9278` |
| `.local-test-evidence/2026-09-06/procedure-scope/run_discovery.py` | `6e0aa05721ff61d473f7a262cc1846aeb8eac5614c5cf995ab743ba1bb340f63` |
| `.local-test-evidence/2026-09-06/procedure-scope/run_recovery.py` | `45bcf32002044d7575b6cb8aad858440cab44c82ba39fdb247e3e3819e0e36d5` |
