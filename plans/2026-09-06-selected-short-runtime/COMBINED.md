# 短期选中来源与审计入口组合

2026-09-06。源2d98e083独审限定ACCEPT，合成为7fafe03a；文档6501fd27合成为fe006f59。三处相邻冲突仅并存audit/conversation authority的构造参数、实例字段及factory注入，未覆盖任一权限检查或显式长期选择行为。

主组合单进程实跑 **42PASS/21.12秒，exit0**；看护墙钟21.69秒、峰值243712KiB（238MiB），进程已退出。专用H0.7.3/M0.6.12/S0.3.12环境，没有SDK源码覆盖/模型/native/新构建。2GiB/180秒限额未触发。

通过的模块：test_selected_short_runtime、test_human_access、test_model_recall_selection、全部test_primary_control_binding（含真实WS审计），以及test_actual_semantic_revise_and_reopen[valid]。它们证明两种authority在实际factory共存、选中来源/重开/裁剪隔离、显式长期不附加短查询、审计grant/分页/WS关闭拒绝和实际语义修订路径继续工作；不等于新physical short outbound或原生UI测试。

命令：工作目录主组合，PYTHONPATH=backend，专用venv Python通过本地bounded_pytest.py执行上述测试路径，参数 `-q --tb=short -p no:cacheprovider --basetemp=.local-test-evidence/2026-09-06/short-audit-combined/tmp`。原始文件仅在本树ignored `.local-test-evidence/2026-09-06/short-audit-combined/`。

| 文件 | SHA-256 |
|---|---|
| identity-bounded.log | e62b8fd2abec966afce0481ab6dad61244b5a908dba6fcabc9892c69548ab3f2 |
| identity-bounded-resource.json | df456ee28edac683f9b96969e89ffd99e9ac037dc4541f1b2e104282ff1b9ca7 |

两消息完整因果组/真实public索引仍由fixture建立，生产worker、新模型short请求协议和多消息tool producer均未由本组完成。401矩阵、240真实质量与程序/提醒仍独立保留。未切换主工作目录运行环境、未发布。
