# Context使用与源码故障执行器组合

最后更新：2026-09-06。主将Dirac限定ACCEPT的source10后继（bcc93644/主复核5819b15e）与Context六格后继0f469cdd合到独立runner，固定9bad3a43。仅bridge执行代码指纹清单发生合并冲突，明确同时保留source_oracle与context_use_oracle；两个A2分派独立，冻结fixture/pin/阈值未改。主读取两个完整oracle，并分别核对12份source、10份context原始证据哈希。

合并固定9bad3a43必要组合回归 **12 passed / 4.15s**：source实际十格及30篡改为1项，Context实际六场景及21篡改为1项，加10项严格传输输入/分层分派检查。不是12个新增产品场景、不是新401正式Run，也不把旧182、新source10和Context4拼成一次全量。两个缺Harness消费证明的格仍阻断。

通过145统一入口、默认共享锁、2GiB/180秒，PGID32908峰107936KiB、耗时4.308秒，exit0、无残留/cleanup error。借用已核M0613 artifact ownvenv，移除PYTHONPATH、-B、禁止pytest插件自动加载；无新venv/模型/native。

实际子命令：`<M0613 artifact venv>/bin/python -B -m pytest testcase/human-memory-program/tests/test_typed_recall_source_oracle.py testcase/human-memory-program/tests/test_typed_recall_context_use_bridge.py testcase/human-memory-program/tests/test_typed_recall_bridge.py::test_two_layer_dispatch_retains_exact_inventory_and_failures -q -p no:cacheprovider -p pytest_asyncio.plugin`。

| 本树ignored证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-06/context-source-combined/r1/command.log` | `0c3d05039522ee6443a7e1a0678e5e3a68ee139ca60bdf8d43d3cbbbe23bf2e2` |
| `.local-test-evidence/2026-09-06/context-source-combined/r1/resource.json` | `339f2a3b07e0a35c28163768ad122b358887b5fb99cc15399adf341c52af1277` |
