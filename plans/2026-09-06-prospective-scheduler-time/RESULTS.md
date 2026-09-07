# Timer必要真实组合结果

2026-09-06。Host执行d3f9720a，实际installed H076(source a252109)/M616，旧075不改。source与协议此前限定只读审查；最终组合待Dirac。
- r2 H075: 3控制PASS、2SDK FAIL/1.45s。pending int/float签名前hash差异；rescheduled被H075严格DTO拒绝。PG60113exit1/peak184496KiB。
- 4aac79b0仅Host签前normalizefloat；H076允许actualRESCHEDULED，未伪pending。
- r3 H076: 2FAIL/1.52s，真实暴露Host assert_claim声明遗漏，未到SDKapply。PG67679exit1/peak184400KiB。
- d3f9720a补回事务claim复验声明；r4仅上述两失败重试：2PASS/1.71s，PG67788exit0/peak182320KiB/minDisk3524MiB/remaining[]cleanupnull。
原3绿控制不重跑。两SDKcase分别真实CREATE pending与公开REVISE rescheduled+新ACK，time_due后Memory实际提交再模拟lostACK；close/reopen clock越authorityexpiry，same-refapply恢复，inbox实际一key，后tick无新增apply。
这不是Host native提示/presentation/ack终态闭环；尚缺独立late-invalidation竞争、lease接管staleworker、observation篡改控制，不宣称scheduler整体完成。

Raw本树.local-test-evidence/2026-09-06/prospective-timer/r2,r3,r4/{command.log,resource.json,identity.json}。
命令为默认共享scripts/run_resource_bounded.py --rss-mib2048 --seconds180，既有primary-m0615/venv Python -I -B run_installed076.py，仅test_prospective_timer_sdk.py两参数。
运行generic依赖复用旧env；H076/M616各自installedtarget优先且逐已导入模块路径核target，非SDK源码overlay。r1误预建evidence目录被入口拒绝，无child。
所有测试/构建进程已退出、槽释放。无模型/native/全量旧suite。

## 新风险控制（2026-09-06，独立于上文已绿两case）

测试源eb944f61经Dirac限定源码预审；risks-r1实际1PASS2FAIL/2.10s，PG68816exit1/peak198368KiB。
observation复制库自洽外hash篡改拒绝首轮通过。lease失败为整数lease经SQLite REAL变float导致journalhash错，2ce1dff1在持久hash前规范float；
late-invalidation失败是重开测试backend漏classificationpolicy，a5358955保留同policy后合法REVISE实际执行。
risks-r2仅两失败：2PASS1deselected/1.46s，PG68902exit0/peak185072KiB/minDisk2927MiB/remaining[]cleanupnull。
三新风险各有通过证据，不计第二次3PASS；旧pending/rescheduled及已绿observation不复跑。

late-invalidation只证明claim后/live双检前actualREVISE+ACK失效阻止apply且inbox空，不外推所有跨库窗口。
lease证明真实handoff、超期接管epoch+1后旧owner assert/handoff/invalidate/actualresult settle均拒绝且journal不变；不能撤回既有SDK物理调用。
observation三字段在backupcopy仅改timerbody、重算body/record外hash，拒绝理由精确observation_binding_differs；原DB/S1不动，触发器原SQL恢复。
原始新日志/identity/resource：.local-test-evidence/2026-09-06/prospective-timer/risks-r1及risks-r2；同既有H076/M616 installed target，无新env。
本次产品delta仅2ce1dff1，测试fixturedelta a5358955；schema52未合。最终源码/证据限定复核待回；完整presentation/ack/native仍未验。
