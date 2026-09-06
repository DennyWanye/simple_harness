# C11 到期来源准备契约

2026-09-07。源码准备，NOT_RUN。原20条setup原文/hash固定，只读取setup，不接受current或gold。

17条标量来源映射；12旧摘要派生、16真实Prospective过期、19未知数值的三组来源仍拒绝。14仅原材料已知“旧便签”标记，不创造旧便签正文。

有效时间以公开MemoryMutationOperation.valid_time_interval表达，Semantic生命周期仍ACTIVE，不用SUPERSEDE伪装expired。原有FixtureSetupExecutor新增valid_time_for_spec默认(None,None)接缝；C11覆盖时间，已有其他准备默认行为不变。

原setup未明确到期瞬间的，夹具明确取scenario_minus_one_second；未给年份的月日使用可信场景时间的上海年份；“某日结束/仅某月”取下一日00:00。这些具体化纳manifest/defaults，不声称原文给出了精确历史时刻。01按原9月1日00:00上海，14按原2026-09-06 10:00等于now，13同时保留200旧额度到期和9月10日才生效800元，不从本轮500反向补历史。

fixture时钟从最早旧到期前1秒开始，真实S1/ingest/durable job/APPLIED+ACCEPTED/原public receipt/非空graph后，仅推进时钟到原scenario，不修改数据库状态，当前graph需排除过期/未来节点。关闭fixture后原生产runtime重开，普通typed recall仍不得返回；原receipt及S1保留。没有模型/TaskScope/前台history/原生/未来timer证明。

新增控制为原文编译加17个公共准备参数，首次主currenttarget统一运行，遇共同首红即停；不重复旧C01/C04测试。
