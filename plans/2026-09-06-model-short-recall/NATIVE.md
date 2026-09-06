# 新组合原生构建与启动前置结果

2026-09-06。实际业务源18ec7194、H073/M0613/S0313组合的全新前端嵌入构建；产品名SimpleHarness Memory 18ec7194，隔离bundle ID com.dennywanye.simpleharness.primary0906r18ec7194。旧407d图谱binary未复用为当前前端，旧包未覆盖。

构建PASS：工作目录tauri-app；固定资源入口145baed3默认OS锁、2GiB/1800秒；DESKPET_BACKEND_PORT=18120、CARGO_BUILD_JOBS=1、CARGO_TARGET_DIR=/Users/denny/projects/simple_harness/tauri-app/src-tauri/target。命令tauri build --debug --bundles app，config显式新产品名/ID及beforeBuildCommand=./node_modules/.bin/tsc -b --force && ./node_modules/.bin/vite build。完整命令参数对应本文配置与resource入口；raw build log保持ignored。共享target/node_modules独占，没有npm ci、没有另起Vite或backend。

构建18.737秒、PGID29218、峰1108960KiB、exit0/无剩余；13个Rust编译warning保留于log，不称零warning。下述前端逐文件hash仅证明这次构建来源，不是UI点击证据。

新ignored carrier复制已审查旧launcher的隔离配置、凭据内存注入、日志脱敏、单实例和7GiB headroom检查；去掉native另起session及按app PID杀组的旧helper。native继承外层资源进程组，非阻塞流读取在native退出后结束，返回真实native退出码；外层负责RSS/期限/完整同组清理。退出时尚未排出的日志尾部可能不完整，不能据此声称日志全覆盖；实际应用退出/持久审计仍须分别验收。

carrier实际短进程检查：分段UTF8与模拟secret合并脱敏成功，保留模拟native的exit3；stdout继承孤儿不会困住carrier，模拟native=0时外层仍报orphaned_group_after_parent_exit/125且清理完毕。它们是运行工具的验证，不是原生业务或真模型结果。

启动前置BLOCKED：首次尝试PGID29560/exit1，native_test_memory_headroom_low:5579MiB，低于7168MiB，发生在Popen之前。**应用未启动、没有建立新的测试userdata、没有模型请求/加载、没有UI或重启PASS。**没有降低预算或退出用户应用。随后全机按RSS只读检查未发现属于本任务的多GiB遗留后端；继续其他轻量验收，保留原生待测。

本机根`.local-test-evidence/2026-09-06/native-18ec/`，实际bundle及frontend成员指纹见artifact-identity.json。二进制SHA256：bdc57848a184a4da12832f93c3fb79419315cb839ae6aa7bbe4c07073c584901。

| 相对证据 | SHA256 |
|---|---|
| artifact-identity.json | ec85a1d204ec9381bf9bbdc07ff67c923110f4d8a82d92246927389464e85cf7 |
| launch_current_native.py | 2c99b0fbab5e8e94e8f40bd5eaa37aa7e0d25f61b9ab5fd26475b4038971ade4 |
| carrier_probe.py | 803ff347ac542f300562cddbd1871a13a9a65b3f4ec2fad8aa1801155c830473 |
| build/command.log | 8b1abbe46d2a2c9011cc167c6fea6a556a055d741c5ae5af224842e9dbd692e8 |
| build/resource.json | ead039af2740a085e8bc2c53f72bd621a6251f5a4ea961d419c26d80c687979a |
| carrier-output/command.log | 718a52e8a09962acf91c7bd2e062c6578ac94a4cfdcece053fa03015b4d2fadf |
| carrier-output/resource.json | 169d39da25ee6863252d3bca53b26718e356cc731d64f1ebafee20bfe0b6d4a5 |
| carrier-orphan/command.log | 30497abd69b450f2fc9bbb64dde9e466c6811b1a6cef765ab1f14ae99991539a |
| carrier-orphan/resource.json | cb782ea542c1c4e233970aeb869142964e6fa93220c7baff39f108ba7ecd9177 |
| first-launch-resource/command.log | 4046aaff5eeb4fb82ff020daa3be04211fa06c60d2d42dbc20f2fc68879acbdb |
| first-launch-resource/resource.json | 13bbeedcefaa622f0bc8cd3dd9d5efc4c52d7afea82732d36ab02c42898bee1e |
