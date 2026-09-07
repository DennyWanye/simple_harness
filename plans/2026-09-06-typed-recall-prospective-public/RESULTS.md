# Prospective public scheduler fixture：结果

最后更新2026-09-06。执行adapter/oracle固定4aee0cdba8808664d481562ccbfb6e13369ebc9f；
后继dd988b1900f5e46f0bd503cdb6c690d1a0b19b12仅收紧一个测试方法的公开异常类型/完整code/cause。
两SHA均获Dirac限定源码ACCEPT后执行。base3de9879f，Procedure结果不覆写。

| 批次 | 固定源码 | 实际结果 | PGID/exit | 峰KiB | 资源秒 |
|---|---|---|---|---:|---:|
| tests-r1 | 4aee | 3 passed in 1.31s | 34656/0 | 73552 | 1.518 |
| tests-r2 | dd988 | 受影响方法1 passed in 0.41s | 34727/0 | 72768 | 0.663 |
| formal-r1 | dd988 | 原13格13PASS/0FAIL/0BLOCKED，13OBSERVED | 34742/3 | 135296 | 1.513 |

只有3个唯一测试方法，不能将3+1复测报4个独立case。首轮即绿；P2为审查后加强expiry断言，非产品red→green。

正式Run `ae075cb1eaed43a3b8f8221160d2c874`，dependency=[]；其余388未选，整体/层仍NOT_RUN/BLOCKED，exit3。
这不是全401结果，也不与旧182、source10、Procedure4直接累加为同一候选验收。

## 原格

- eligibility/epistemic:prospective:explicit_user:repeated_observation: PASS
- eligibility/epistemic:prospective:explicit_user:source_bound: PASS
- eligibility/epistemic:prospective:explicit_user:source_verified: PASS
- eligibility/epistemic:prospective:explicit_user:unverified: PASS
- eligibility/epistemic:prospective:explicit_user:user_confirmed: PASS
- eligibility/epistemic:prospective:observed_behavior:repeated_observation: PASS
- eligibility/epistemic:prospective:observed_behavior:source_bound: PASS
- eligibility/epistemic:prospective:observed_behavior:source_verified: PASS
- eligibility/epistemic:prospective:observed_behavior:unverified: PASS
- eligibility/epistemic:prospective:observed_behavior:user_confirmed: PASS
- eligibility/epistemic:prospective:verified_external:source_verified: PASS
- eligibility/prospective-pending: PASS
- eligibility/prospective-triggered: PASS

## 已证事实与边界

actual public read_outbox绑定原event release_succeeded与target/revision；public signal ACK保持pending/revision，
显式synthetic event经live registration消费为matched/applied→triggered/new revision；原CREATE source内容/证据保持。
未注册时真实no_recall并有candidate query，event被exact registration门拒绝；future observed_at与expired authority
分别命中公开MemoryValidationError精确code，expiry还核ValueError cause `ProspectiveSignalAuthority is expired`。
重开同request零candidate读取/同result，新request引用实际signal提交revision；typed结果完整hash/来源/原预期仍验证。
独立time fixture只用于SDK合同：early拒绝、trigger_at等值成功；不算原event矩阵中的时间格。
source/run/clock/trigger篡改负例命中独立synthetic输入检查。

这是synthetic SDK scheduler合同测试，不是Host提醒/UI/真实外部event运行。
原19中的另6 lifecycle（in_progress/rescheduled/candidate/completed/cancelled/expired）尚未在此leaf补齐可归属证明，
projection仍独立未验；没有将这些未实现声称API不可构造，更没有改其state/expected来通过。
32不合法epistemic组合原construction conflict保留，前置拒绝不算recall PASS。

## 命令/安装/资源

沿现有installed H073/M0613 own Python，无新venv/checkout、source overlay、SDK私有SQL、模型/native/build。
解释器 `/Users/denny/projects/simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/typed-short-sources/artifact/venv/bin/python`。
145入口 `/Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py`，
默认共享OS lock，--rss-mib 2048 --seconds 180；未改锁，三批均成功取锁，无BUSY。

child tests命令：`<python> -m pytest testcase/human-memory-program/tests/test_typed_recall_prospective_public.py -q -p no:cacheprovider`；
窄重测加 `::ProspectivePublicTests::test_missing_registration_future_expired_and_reopen`。
环境PYTHONDONTWRITEBYTECODE=1、PYTEST_DISABLE_PLUGIN_AUTOLOAD=1。
formal child是既有run_typed_recall_public_consumer.py、exact H073/M0613 source/wheel/pins、原13个--cell、无--observe。
全部完整argv位于command.json；无另建包装器覆盖退出码的宣称，以resource.returncode为准。

## 本地raw索引/hash

本树 `.local-test-evidence/2026-09-06/prospective-public/`；原始文件ignored不进Git。

| 相对路径 | SHA256 |
|---|---|
| tests-r1/command.json | 622ea0ee81b2a841294a3056f6de015398a2f1426c5869577c1fafe783cd799b |
| tests-r1/source.txt | ff80cf0b8b0df0afad6f011380c68e27421a6a5f06f780897978d72951fda20b |
| tests-r1/resource/command.log | 323093b1d87135d3917cc3d45e9d410ae649c19f1b2cb18d7a212666c1ec2046 |
| tests-r1/resource/resource.json | c0240343108272896ab426d91ae45412a5854d99913b29fc1c8731fd8882a7af |
| tests-r2/command.json | cbd6f288ae426470fa721d3dabab3a71f6638a49307976167bb03745a6d80451 |
| tests-r2/source.txt | c3e8e48250a83519c7e12bd55afc73f66b55fafe0c0b132e1a83cb06e15f9c73 |
| tests-r2/resource/command.log | f6b6e232e0345fa742cf57cbc60c8884709a6f029bd43161ca6e812c9175f2a8 |
| tests-r2/resource/resource.json | b7342bb89e69f7c128f5c7a7c9854320d2f3755362d3a0ea357ed2b9010504a7 |
| formal-r1/command.json | 6993e84bc89a30aa560442f5f490605371327612e0a6c594cc66ba2330ad333c |
| formal-r1/source.txt | c3e8e48250a83519c7e12bd55afc73f66b55fafe0c0b132e1a83cb06e15f9c73 |
| formal-r1/resource/command.log | 1d298f62907e052efb6b61f5fb788c8fc113ba41ca4a7b8bdce6eb4cf220a5b6 |
| formal-r1/resource/resource.json | aecab9111b7f3c9bae8555eafc7b01b1f844482db92c73d9c828c628b9f6bd32 |
| formal-r1/public-data/bridge-summary.json | 0d5b1503e764280eba108309911854e0ae6fadf11c002941da276ff318f3277a |

三份resource：stop_reason=null，remaining_group_members=[]，cleanup_error=null。
所有自有测试PGID已退出、无残留、槽释放。无需重跑已绿基线。
合runner时bridge保留source/context_use/procedure/prospective所有oracle文件指纹；主原runner WIP不覆盖。
