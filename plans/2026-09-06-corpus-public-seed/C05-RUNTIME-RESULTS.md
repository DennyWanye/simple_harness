# C05 新增运行准备控制

2026-09-07，主候选 H0710/M619/S0313，源码23fca939，分页修复82581c83（业务delta f929deca）。新增07/08/10/11四控制及TOOL消息call identity一控制，共5个唯一控制分批通过。

r1 **3 PASS / 2 FAIL，9.44s**：07实际活动Scope入队、08实际complete归档、TOOL原调用ID透传通过；10/11在实际搜索中无第二页。只读原失败Host数据库确认unicode61按完整中文词匹配，原“旧书/海报”片段查询零结果；改用所有原setup完整标题对称OR查询，仍要求实际limit=1、原cursor与真实B,A顺序，不改索引/ID/结果排序。

r2 **2 PASS，4.76s**，只复10/11。r2启动时业务代码已是f929deca、文档cherry冲突尚未收尾；执行期间业务代码未变化。另有不被这些fixture加载的main/observability未提交修改，后独立提交d60a94f4。此证据不声称整树不可变提交测试。

C11此固定身份的并列排序通过，不保证所有新opaque ID下仍B,A；不同实际排序必须失败，不挑ID重建碰运气。这是确定性Provider/实际SDK和Host公共准备链路，无真实模型调用，无原生操作，不能计入240条模型质量通过数。C05正式main setup/scoring/followup仍待。

资源：r1 PG83131 exit1/10.255s/peak214528KiB/minDisk4557MiB；r2 PG83471 exit0/5.619s/peak208288KiB/minDisk4540MiB。都remaining=[]、cleanup=null、stop=null；无预算拦截。旧绿未重跑，原失败保留，防熄屏保持。

命令：共同使用当前installed target优先的PYTHONPATH、共享run_resource_bounded.py，2GiB/180s。r1运行test_corpus_c05_prepare.py中的test_runtime_case_public_admission_archive_and_real_pages四参数及test_setup_http_tool_message_preserves_actual_wire_call_identity；r2仅前者[C05-10]和[C05-11]。

| 本机原始证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-c05-runtime-source/r1/command.log` | `ec1da420e7149e849a210a62bf8e2008621e1b5ca5c3bc903a6cf8780c854202` |
| `.local-test-evidence/2026-09-07/corpus-c05-runtime-source/r1/resource.json` | `735c81d5f6cef23c64ddcd908b576647e6cc0b1df2f754b53dbc573dbe638bb8` |
| `.local-test-evidence/2026-09-07/corpus-c05-runtime-source/r2/command.log` | `897873c08a854fe9a160087cc820b734db2279fe438f34e50de562c2f3c219e6` |
| `.local-test-evidence/2026-09-07/corpus-c05-runtime-source/r2/resource.json` | `171ca9fdfbb202fdaf6ed85a997bad95e1e127483f2dbda12304f02cb0bbca06` |
