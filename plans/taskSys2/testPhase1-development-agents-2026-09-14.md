# testPhase1 开发子代理使用记录

截至2026-09-14 15:33 CST，共12个子代理已返回且关闭，同时最多3个。主会话保持GPT-6 Astra/high。此处仅Codex开发子代理，独立于本地被测模型调用。

|子代理|实际模型/推理|运行跨度秒|未缓存输入|缓存输入|输出|交付验收与返工|
|---|---|---:|---:|---:|---:|---|
|Popper|gpt-5.6-terra/medium、gpt-6-astra/high|538.21|135246|1424128|12840|parent accepted exactref/current projection and catalog pagination; real multilingual consumer PASS5calls24405tokens|
|Sartre|gpt-5.6-sol/high、gpt-6-astra/high|1584.02|156585|1451136|28656|T3 lifecycle10 and T4 bookkeeping29 parent PASS; live lifecycle PASS; parent fixed published API compatibility|
|Bacon|gpt-5.6-sol/high|388.93|90741|1886464|16345|parent8 meter tests PASS; parent added known-failure settlement/cache observations; resumed cost not inferred|
|Planck|gpt-5.6-terra/medium|136.41|78006|406528|6522|parent229PASS2bad exact-word assertions; parent fixed2 then30PASS; six-file patch accepted|
|Volta|gpt-5.6-sol/high|363.03|108083|4066816|15316|bounded diagnosis accepted; parent implemented durable exact-write reconciliation;8PASS including original hang; no child code|
|McClintock|gpt-5.6-sol/high|313.74|53714|1200128|15431|parent accepted coordinator after 126 combined tests; frozen real16 matrix running; no claim of benchmark success|
|Kepler|gpt-5.6-sol/high|139.35|48927|972288|6156|parent reproduced prebind ordering failure, fixed startup binding/audit, validated31+28 focused; final196 successor scope PASS|
|Hypatia|gpt-5.6-terra/medium|166.40|61263|610816|5707|7 legacy defects addressed; parent44PASS1too-broad dict equalityFAIL; parent corrected worker-key assertion then126combinedPASS|
|Galileo|gpt-5.6-sol/medium|149.87|95988|1341440|6245|parent accepted after correction of concurrent perAttempt nested field; 9Host regressions PASS24.39seconds|
|Ohm|gpt-5.6-sol/medium|46.06|39695|275712|1846|20seconds nested Attempt key fix; parent9HostregressionsPASS24.39seconds|
|Noether|gpt-5.6-terra/medium|155.83|56523|615936|7080|聚合脚本已用于16次记录；父代理增加SDK工具对账与有效闭环判据，保留2次R自选失败，未重解析晋级|
|Boyle|gpt-5.6-sol/high|166.08|59611|1086208|7739|accepted production change; parent23+16+196focusedPASS; parent critic recovery witness required3 oracle/fixture corrections, not attributed to child production code|

合计未缓存输入984,382、缓存输入15,337,600、输出129,883。推理tokens是输出子集，不再加一次。缓存输入不能称为免费。

前两项实际模型集合来自turn_context，不能据此拆分各模型独立用量。恢复子代理时出现Astra/high，已停止以恢复方式延续低价代理。其余新建代理显式选定模型，无整段历史继承。

耗时是首次会话到最后完成的跨度，含等待与父代理反馈；不同代理有重叠，不求和为墙钟耗时。Ohm代码诊断约20秒，完整会话记录46.06秒；以完整记录为准。父代理协调、验收与返工未独立计时，因此不声称净节省百分比。

当前可观察结论：Terra适合范围清楚的断言迁移和统计辅助，但夹具仍需父代理校正；Sol承担计量、启动恢复、矩阵控制等跨层任务，仍须父代理独立运行测试。对同一个小问题追加代理有上下文成本，后续优先让父代理直接收尾。没有同任务同条件的主代理独做对照，不能据此宣称某模型更省钱或固定提速。

本地数值回执：`.local-test-evidence/2026-09-14/gap-phase1/usage-latest.json` SHA256 `fa85e5d19f9c84f0fd95c49b48fdac524e84e1964c49cd8a5fe0ac96e6d24241`。不提交消息、日志或原始回执。

最终验收补充：12个子代理均已关闭，无新增代理调用。父代理核对发现R两题官方终态成功但自选JSON解析失败，因此最终报告增加有效闭环判据；从SDK补齐缺失网关outcome和R异常未返回的工具计数。上述审阅与文档时间未独立计时，不能从子代理耗时推算净节省。原数值回执仍保留15:33检查点，token计数未改。
