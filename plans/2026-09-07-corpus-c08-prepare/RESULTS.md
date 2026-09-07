# C08 遗忘场景：实际准备与边界

2026-09-07。源码 `7022e8e0`＋显式部分状态 `287176d0`，当前首批13项控制通过；不计入模型质量通过数。

原20条setup逐字固定，只接收setup与scenario clock，不接收当前问题或gold。01/02/03/04/05/07/08/09/10/12/14/17共12条简单事实通过专用公共prepare实现；05作为上周Episode，未指定日时以clock-7d作明确fixture默认。06/11/13/15/16/18/19/20要求保留旧摘要、提醒、检查单或独立关系/别名来源，当前明确拒绝准备，不用一个简单semantic替代这些要求。

准备使用既有Host synthetic S1 producer和真实SDK durable analysis job，fixture lineage明确no-language-model。必须同时观察APPLIED、ACCEPTED application及抑制前非空graph的exact memory ID/revision/content hash，才执行公开EVIDENCE suppression并精确核receipt。之后公开graph隐藏该事实，原S1必须逐字保留。重开manager后仍隐藏，原request返回同一持久化decision。没有SQL改写SDK、伪造成功receipt、把空库当已忘库或声称真实用户点击过遗忘。

新增控制：原20条编译/8条显式缺口/拒绝篡改边界1项，以及12条真实准备、抑制与冷回读。只证明fixture和公开graph suppression，不代表所有ordinary read/export/Context路径、人工审计权限或240条模型质量通过。正式scoring dispatcher尚未接入。

Dirac首测前校准：上述12条中01/02/04/09还明确提及摘要、派生联络、称呼关联或金额引用。结果中显式给出`setup_complete=false`与未实现载体，不能把scalar抑制通过当这4条完整setup就绪。另8条只完成简单事实准备仍需真实评分，整个20条没有宣称通过。


H0710/M619/S0313实际运行：唯一首批 `tests/quality/test_corpus_c08_prepare.py`，**13 PASS / 5.55s**，无旧绿复跑。原文编译边界1＋12标量真实SDK分析/抑制/冷回读控制，其中4明确`setup_complete=false`，另8拒绝准备。源与结果由Dirac限定审查；正式main/scoring相位仍未接。结果不是12个完整case质量PASS。

默认共享锁1024MiB/120s，PG79749 exit0 / 6.204s，峰163744KiB，最低磁盘4668MiB；remaining[]、cleanupnull、stopnull，已释放给下一单项验证，防熄屏不停止。

| 本机证据相对路径 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-c08-prepare/r1/command.log` | `8cad42984eae098f6dbdd164958c0a707fe4cc354a9c5b22283c50385c09a7ba` |
| `.local-test-evidence/2026-09-07/corpus-c08-prepare/r1/resource.json` | `cec51ac6c4a98c34e1c2be3d3f9f6c73fe0d518d6b9a7a5fe2a7b09e326d8d75` |
