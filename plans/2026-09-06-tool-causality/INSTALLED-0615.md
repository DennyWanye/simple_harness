# 空assistant完整工具组安装组合通过

最后更新2026-09-06。组合源码935d3e12，实际安装H073/M0615/S0313，旧H073/M614环境保持。Memory源码7f9983dc3ced27a963fb207b17a4dc9470dbcae4、wheel69544677c44a09fb53cbe18df2c2cfe6e33b892eb34cb32b1cb2cf3c6445294d已由主审和Dirac分别核对：两次offline build相同、73包成员与固定Git源一致、76个非RECORD成员与独有target一致，七项原始证据hash一致。

主新建约7MiB的小环境，共享通用依赖；三SDK自己安装且169/76/121成员逐字节等于vendor，-I探针确认三包版本及来源。未修改旧环境、没有源码覆盖。Memory候选manifest仅存制品/来源/构建manifest哈希，原始构建报告保留ignored。

22项测试与2个子测试通过13.05秒：原tool组空/非空两个参数均实际运行11轮，核完整父子消息、公开注册、short非空召回、重开引用、遗忘tool来源后无命中；同组写故障回滚、两类篡改拒绝、公开因果reader、SDK身份与可信时钟邻居。原M614 r4失败永久保留，不能改写原版本为绿；本结果是后继安装版修复。确定性Provider及实际Host/SDK/工具/存储，不是网络真实模型或native。

统一默认锁、2GiB/180秒以及1GiB磁盘准入/256MiB停止。setup PG44203/exit0/峰93088KiB/.678秒；tests PG44329/exit0/峰380528KiB/13.92秒；均无进程残留或清理异常，最低磁盘3424MiB。

测试命令：新primary-m0615/venv/bin/python -B经scripts/run_resource_bounded.py，PYTHONPATH=backend、禁插件autoload；pytest -p pytest_asyncio.plugin -q选择test_primary_tool_message_ingestion.py、test_primary_tool_causality.py、test_sdk_candidate.py、test_runtime_clock.py，basetemp为ignored tests-r1-db。身份安装脚本与证据如下。

完整文本工具组来源链的这个缺口关闭；非文本artifact组、实际模型/native、H074 typed-use、240质量和其余原计划任务继续，未切换用户主checkout或发布。

| 本机ignored证据 | SHA256 |
|---|---|
| .local-test-evidence/2026-09-06/primary-m0615/prepare_env.py | e1c2f27c49d24aaf469192b8dbb6ecb0a8c45ae2eb47e9860f5428cd07313a1d |
| .local-test-evidence/2026-09-06/primary-m0615/installed-identity.json | 040e31ce228e834bc1d7e2479ed3350d2a4ea0578cb7e5d2905db9047e59f90c |
| .local-test-evidence/2026-09-06/primary-m0615/setup/command.log | e977605c6e664fa5d24045e558bce06f612761de8c2ecab65f5611bfc7f82c48 |
| .local-test-evidence/2026-09-06/primary-m0615/setup/resource.json | f6972ff6148eabd98584f6a1011d2fec3ac450d016cae03ef85ee024deb0586c |
| .local-test-evidence/2026-09-06/primary-m0615/tests-r1/command.log | 438ee39849257f468f6a638fabdf6abd09e063aa6db53d0cc04ff89c1562a988 |
| .local-test-evidence/2026-09-06/primary-m0615/tests-r1/resource.json | 05df7cb0f38c6da24ff52a98c8d35a89980935bbe97e0c902cd2d6aed2607af8 |
