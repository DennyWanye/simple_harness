# 验收顺序与证据边界

主体BW01–BW16完成前不运行批量SDK/回归/模型；单个阻塞micro-test由执行日志说明原因。包参考测试只验证规格，不计入产品通过。

1. `sdk-cases.json`保留48个ID并修订V13实际restore方案；C08只做late accounting。`findings-sdk-tests.json`逐F补实际反例，允许复用原测试但每条断言要对应。
2. `inherited-coverage.json`保留原66 Given/When/Then逐条、`occ-coverage.json`保留12项。Original内容是追踪，实际applicability/currentness以1.1裁定；任何实质冲突由disposition写明，不静默删除。
3. 16原SDK mutations＋12项F定点变异分别保留；同一个行为测试可杀多个变异，不能import/syntax错算KILLED。
4. 状态化200×50、真实SQLite竞争/子进程、legacy/fullH1按已批准runner执行；fixture使用完整本地schema，reference parent fixture不能代替。
5. 原生Host在隔离副本运行；C07 API pytest不替click/screenshots。各测试原始证据留ignored，Git仅摘要/nodeid/hash。
6. 真实模型用`model-scenarios.json`固定M01–M04×3，应用模型实际当前用户批准profile，provider不可stub。12局全部oracle通过且无安全违反；失败保留不能换题；环境无效不伪造PASS。
7. 最终独立代码审阅者必须实际可调用且不是作者；作者自审、接收方计划评审、实现核验三个栏分开。默认ON在完成同交付中验证，不另等一次开关批准。

所有target nodeid都是实施目标；actual_nodeids当前空，执行后才填写同源码fingerprint证据。引用原raw/cost的历史记录可保存，不等于可用于新接受或恢复权限。
