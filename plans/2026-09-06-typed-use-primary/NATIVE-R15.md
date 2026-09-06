# 原生 r15：Cytoscape 有向关系与筛选

更新：2026-09-06。固定 Host `1e8f219b0fcfa0a5c8aa499dd40933c3993e76aa`，H078/M618/S0313，沿用原生二进制 SHA-256 `bdc57848a184a4da12832f93c3fb79419315cb839ae6aa7bbe4c07073c584901`。Tauri 自管当前源码 backend，端口18120；隔离 userdata，未切换用户主树。

本轮限定 **PASS**：通过真实原生窗口打开关系图，看到2条记忆、1条关系；点击画布箭头后，详情显示“user:self · response_style · 简洁 → 适用于 → 简洁答复步骤”，与方向一致。输入 `response_style` 后显示1条记忆、0条关系，关系详情隐藏；清空筛选恢复2条记忆、1条关系及原选中详情。这里是暂时隐藏、恢复选择，不称清空内部选择状态。

数据边界：2节点/1条 APPLIES_TO 由公开SDK与真实Host S1准备，不是本轮真实模型提取。准备阶段实际 durable analysis job 首次APPLIED、随后IDLE，执行次数1，并核对公开图前后一致；不是仅凭两次IDLE判定完成。准备r1因注册analysis authority冲突失败，r2改为构造器注入后通过；不删除job、不停用生产worker。r15界面验证没有发出外部模型请求。

一次粘贴工具回报clipboard超时；随后AX和截图确认文字已输入、筛选已生效，因此没有重复输入。完成采集后正常Cmd+Q退出，PG14481已空：exit0、453.793秒、峰值1,327,584KiB，remaining=[]，cleanup_error=null。最低磁盘1,701MiB，后续继续使用现有小环境，不复制大环境。

本轮不代表240条质量评测、模型图谱抽取质量或完整长旅程通过。r14时间提醒仍FAIL，正在修复并用原userdata复验。

本机 ignored 证据索引与 SHA-256：

- `.local-test-evidence/2026-09-06/native078618/primary-ui-uuvwv5cb/01-graph.ax.txt`：`c3f932e4425daed7a29c491e59ba4801973ede8af2be77f4a4a90c05e32996de`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-uuvwv5cb/01-graph.png`：`e477dd26f69c9dea8cb920e59754291293bf61ab41b4c9267f530220ee77cd83`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-uuvwv5cb/02-edge-click.ax.txt`：`f2a031a81f5f3d375058061799a68f1df6f952a39fd927113b37a2afb2dcc601`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-uuvwv5cb/02-edge-click.png`：`ec33c5d98fac0379324869255638a2fc111ac3830b99ecdb4b73cf0241f4f385`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-uuvwv5cb/03-edge-details.ax.txt`：`f2a031a81f5f3d375058061799a68f1df6f952a39fd927113b37a2afb2dcc601`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-uuvwv5cb/03-edge-details.png`：`a7094e4cae9acc0856b94a1cece297ffb6dd38aed5636d97f40c5ff96b6d11ba`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-uuvwv5cb/04-filter.ax.txt`：`243a3dc0c391de8da14b226be9e2ff499a3d19446bd0fe6bb8a292893ef00fb6`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-uuvwv5cb/04-filter.png`：`77c436b74e78dfbb0e873114823066070813380d67d81b62a31e22efbebc4508`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-uuvwv5cb/05-filter-cleared.ax.txt`：`ad48fac9b85141ba534d488971631806e029bdc86ac7f9d47f560f95ae928ecc`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-uuvwv5cb/05-filter-cleared.png`：`644a3ba660086c09230445e52c7f5dd04337be8e12e755cb6b874b47261db86d`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-uuvwv5cb/launch.json`：`a327485c3326bc2070622d8b68325e5123b05989a2fba332ce84ef2962e3b730`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-uuvwv5cb/native.log`：`0c8005d9351528d3f520cffa4d175ca6da77cd44f50590c720b1d4411b38b88d`
- `.local-test-evidence/2026-09-06/native078618/r15-graph/resource.json`：`500788df4a903d02acb8a09b8fe022a7ea49961d2d07b446784adf09872229f3`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-e0dlvpwh/graph-seed.json`：`455ffb34c2eb3021781b13ad27be4517baef0020d5f9e41d194632f9131b99d6`
- `.local-test-evidence/2026-09-06/native078618/graph-prepare-r2/resource.json`：`299a5e37161cbb9e0838c231d8d964e7a45a9e63a51995d05ad4b03e8e3cb9eb`
- `.local-test-evidence/2026-09-06/native078618/prepare_graph.py`：`434d6f3727d641a57ede769ab34a71c205121b84bf46615860f2e3b308c5e90b`
