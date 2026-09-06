# 模型短期召回组合验证

最后更新：2026-09-06。业务源cb743007经独审限定ACCEPT后fast-forward组合树；测试资源入口最终145baed3独审两P1真实反例修复后也已fast-forward。无合并冲突或业务再实现，不据此重跑全部叶子测试。

当前组合为H073/M0613/S0313；仅Memory在组合专用venv由本树vendor以offline/no-deps/reinstall从0612更新至0613。安装后所有169/75/121成员（除RECORD）再次与本树vendor逐字节一致，direct_url均对应组合树，绝非SDK源码覆盖。

实际命令：工作目录simple_harness-primary-candidate，PYTHONPATH=backend，解释器`.local-test-evidence/2026-09-05/primary-candidate/venv/bin/python`。资源入口使用资源叶固定e2f6d7ce绝对路径（当时版本），2GiB/180秒默认共享锁；命令体为model-short叶ignored `bounded_pytest.py --child -q backend/tests/sdk_adapters/test_sdk_candidate.py backend/tests/memory/test_short_index_worker.py`，仅复用该child模式的禁止模型导入，不运行其外层看护。源完全相同的53项不无意义重跑。

这两模块22 PASS/9.90秒，PID26185 exit0，采样峰值271328KiB，resource确认无剩余组；补测当前候选身份及新SDK下实际短期worker装配/关闭行为。e2资源入口后发现probe/退出snapshot/spawn信号竞态，最终145修复的真实反例及独审见[test资源记录](../2026-09-06-test-resource-cleanup/RESULTS.md)；本批未触发相关故障且事后独立ps核实该组已消失，不追溯冒称本批使用145。

默认启用模型显式长期+短期选择，一次typed预算和完整当前来源；晚遗忘/缺证据阻止物理出站。真实非空混合结果在公共mutation fixture验证，不代表混合真实模型出站质量。全部完成证据限SDK/Host受控HTTP链，非native/401/240或整体program。

失败/取消/timeout提案的完整质量观测、工具多消息producer、全主体projection成本、Procedure/Prospective完整链、Service durable审计、真实模型/原生新组合仍待续。用户原主checkout/runtime未切换，无push/tag/发布。原240中文r4及旧矩阵/阈值保持。

本机ignored根`.local-test-evidence/2026-09-06/model-short-combined/`：

| 文件 | SHA256 |
|---|---|
| installed-identity.json | 0d5428f0a1444ddc2c28c2cd937078f47671b35aaeeb0ffbc462d53741a23e7e |
| checks/command.log | c0240f8e93dc20607cdee41c156a95706e3a48f259430a4da30b6906219a4912 |
| checks/resource.json | 33cad511427e279b1e6f63384d7632fae8ff119008e4deb089e950cf3d82f493 |
