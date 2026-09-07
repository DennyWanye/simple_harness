# Host 组合候选 SDK 0.7.3 安装验证

最后更新：2026-09-06。固定组合源码78647bb0，集成叶子756011ca。独立只读审查限定通过；没有新P0/P1。

## 结果

单进程执行operation_audit整个测试目录及SDK candidate/composition两个模块：**100 passed /31.62s，exit0**。
看护进程墙钟33.03s，峰值264256KiB（约258MiB），未触发2GiB/180s停止条件；所有测试进程已结束。
运行时禁止本地模型模块导入，没有真实Provider、embedding下载、native应用或构建。

使用主组合专用venv，无SDK source overlay。独立逐文件核对安装与本树vendor：Harness0.7.3=164、Memory0.6.12=72、Service0.3.12=112，全部字节一致且direct_url指向本树制品。Memory/Service未重装。

## 验证范围

- 新终态审计页核对Host真实raw SDK事件身份、payload hash、状态及Run；旧v1审计记录保持原样。
- 实际普通/legacy scoped正负例、foreign Run、非空committed-turn生产、同cursor重启、Memory审计读取/恢复、前置拒绝与candidate身份组合通过。
- late-source负例在SDK读返回之前切换源，由既有第二次source校验拒绝；不能把它描述成新增matcher内部所有await窗口的独立故障覆盖。
- 原22pass2fail、新21pass2fail与窄修复2pass历史证据保留；本次100是新组合运行的实际计数，不与历史相加。
- 不代表所有Agent操作已覆盖，也不代表受控审计UI、Memory实际应用、native或全Host回归通过。

## 命令与本地证据

工作目录为本组合树；PYTHONPATH=backend。专用Python：`.local-test-evidence/2026-09-05/primary-candidate/venv/bin/python`。
通过本地bounded_pytest.py执行：`backend/tests/operation_audit backend/tests/sdk_adapters/test_sdk_candidate.py backend/tests/sdk_adapters/test_composition.py -q -p no:cacheprovider`，basetemp为证据目录/tmp。

原始文件只保存在本树 `.local-test-evidence/2026-09-06/primary-audit-073/`。

| 文件 | SHA-256 |
|---|---|
| identity-bounded.log | c6b60f80b3df572b73c9ea739f66aa07a185b179a1d50f0c3abcbdceab8f487a |
| identity-bounded-resource.json | 5bb8ca09113fa543301a9810b3ab0e4c438961a7ff2db95dc50b64ab6d6e4977 |
| installed-identity.json | a54718f8e8a56a14f11c1df7971df526e4865f404e883c76bdbc3d3194c7cf78 |
