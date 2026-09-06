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
