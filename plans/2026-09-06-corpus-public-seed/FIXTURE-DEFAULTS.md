# 全240统一 fixture 具体化约定（供主与Dirac审）

2026-09-06；只取setup/独立scenario_clock，不读类别gold、预期答案或Provider结果。
不修改原语料/阈值。这里只是合成数据具体化，不冒称真实历史观察。

1. 原文明确日期、范围、到期点、时区及修订关系优先，不能被默认覆盖。
2. 过去episode未注明具体日期：occurred_start=scenario_clock-24h；结束未知保留None。
   participants依setup；没有描述的goals/results/impacts留空，不造观察内容。
3. 明确“未到期提醒”但未给时刻：trigger=scenario_clock+24h，使用统一场景时区。
   提醒动作只保留setup的“未到期提醒”占位描述，不造具体现实动作；尚无Host触发事实。
4. 未另写状态时用原总索引的当前有效/同主体/无冲突/直接证据规则。
   语义的作用范围、条件例外和其他人物属性保留在payload/qualifiers，不假造TaskScope。
5. C01-06必须经CREATE旧B+真实public REVISE→A；labels保留同memoryID不同revision。
   改正授权来自限定该fixture的明确源映射和实际Host S1+public B receipt/current head；
   专属fixture issuer持久化于现Host evidence，再由SDK同事务消费action authority。
   不扩生产中文纠正词表、不声称模型已授权、不把授权包作为USER/普通召回来源。
6. setup literal与SHA固定；SDK实际memoryID/revision/type/contentHash/evidenceID回读单列。
   输出不得整体传给Provider。graph仅设置/审阅端，不能当typed recall。

若默认改变实际用例语义，保留具体问题，不能以gold调整默认。非SELF20例等Hegel，
Procedure语料映射由Singer处理；都不删除或跳算通过。
