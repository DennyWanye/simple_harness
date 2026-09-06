# 工具多消息：公开因果来源读取

最后更新：2026-09-06。内部reader通过SDK公开audit分页、provider projection outbox、read_provider_invocation和read_effect读取实际来源。Host primary_effect_identities提供已在handler前登记的effect ID；其自身不授权结果。reader不访问SDK私表、不推算内部effect ID；用公开audit_reference正向匹配opaque引用，不将opaque字段当原ID。结果只含来源身份/哈希，既不签发terminal receipt，也不构造ConversationToolCausalLink，不因此改变短期入库。

完整当前turn transcript由真实Provider response及已结算effect重新投影并逐项比较；同raw call跨turn不能凭文本相等配对。校验公开审计结果hash、真正effect evidence_ref/hash、Run/Provider/call/turn/version、全部effect消费与完整消息次序。缺/UNKNOWN/不匹配/不完整记录拒绝。支持的文本结果映射受真实SDK ReAct当前投影约束；其他特殊投影保守拒绝，不猜测或忽略。

读取限制：audit最多32页×256，每run provider/effect heads最多256；provider projection公共接口只能按全局sequence读取，最多8192条后拒绝。此上限防无界扫描，但不是优化后的run索引或p99证明。公开audit分页会生成SDK自己的本地投影缓存；不声称零磁盘写入。配置无新flag；尚无终态producer调用这个reader，下一步需明确版本化Host凭据映射及原子S1存储/旧v1重放兼容，再接入整组索引。

验证使用Host实际dynamic ProductEffectExecutor/工具路由、H073/M0614/S0313和一个确定性Provider，真实工具tool_search两轮复用同raw_call_id，父ordinal2/4、tool3/5、两个不同effect/internal call ID，实际结果均保留。1个集成测试含重读一致和5个来源/结果/整组篡改控制，不报6个独立测试。r5为1PASS1.01秒，PGID38126峰170080KiB、elapsed1.518秒、exit0无残留。非真实模型/native、非短期整组index完成。

首尝试r1因共享锁busy75，没有启动子进程或创建证据目录。保留三次失败：r2误把公开审计opaque request ref当SDK request ID；r3测试夹具未启用生产dynamic工具路径，Host effect index实际为空；r4正控已过，但一个负例在SDK DTO构造阶段因TaskExecutionEnvelope不一致而失败，同时工具query输入遗漏。后继采用真实另一effect替换负例、合法query和dynamic路径，r5通过。r2/r3/r4资源均exit1且无残留；不把夹具错误描述为SDK bug，也不把重复执行累加为完成度。

每批使用145默认共享OS锁、2GiB/180秒。实际命令体：`PYTHONPATH=backend PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 <primary-m0614 python> -B -m pytest backend/tests/memory/test_primary_tool_causality.py -q -p no:cacheprovider -p pytest_asyncio.plugin --basetemp .local-test-evidence/2026-09-06/tool-causality/r5-db`。独立源复核待续。

| 本机ignored文件 | SHA256 |
|---|---|
| .local-test-evidence/2026-09-06/tool-causality/r2/command.log | b88ab0c77e113672c914bebda2be3bcceb91a18fcd9ba03cb63a44100562ba5a |
| .local-test-evidence/2026-09-06/tool-causality/r2/resource.json | a1799d4d974b9ab3d4ba9363dd80b99885aa62558d8c20128ba38bd03d571113 |
| .local-test-evidence/2026-09-06/tool-causality/r3/command.log | 29e82b85608fce94616e6ff2bbbe72b07b212f40de637704cc7b83c6e668af75 |
| .local-test-evidence/2026-09-06/tool-causality/r3/resource.json | 9ec22037ff2584756375c13721f85dbdddf29997423d51848bfa48290b8f2f30 |
| .local-test-evidence/2026-09-06/tool-causality/r4/command.log | 06632d1e7c47f72221f82528a87c13afe1a6fdabb63dc936986593806dfde212 |
| .local-test-evidence/2026-09-06/tool-causality/r4/resource.json | 310e382c2a8b5d91ee5eab9deb8821ab0497f27bc9fb0b3385c389df668c9dd6 |
| .local-test-evidence/2026-09-06/tool-causality/r5/command.log | 818471def591c9f64bfe0125b52112f949d00ec9633edcbbf0414328a7f152ef |
| .local-test-evidence/2026-09-06/tool-causality/r5/resource.json | 628c967b54fe99d7af955ddcd9a8034d7da724187da84cb7daed3a21afe95583 |
