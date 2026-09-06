# 当前 primary 的历史 tool 压缩与 exact page

2026-09-06，base `33809aae`，`feat/primary-compaction-source`。源码准备，尚未测试。原义务来自 Memory program S5 Task1/4：最近10完整causal groups、大结果typed summary+exact ref、冻结预算和当前出站来源检查；Task6的语义收口继续使用已存在closure/mutation链，不额外调用LLM压缩器。

实际链：`main`→`PrimaryForegroundContextPort.prepare`→`PrimaryHistoryStore.read(limit=10, completed_only=True)`→`_context_messages`→SDK immutable start→每轮`ProductRunContextAuthority._plan_turn_messages`→physical guard。初始历史工具组目前整组quoted USER，工具原文超预算时整组被裁掉。每轮旧planner虽生成`page:causal:index:hash`，该字符串没有匹配的durable resolver；`ContextPageInStore`当前是request内存引用，重启不能恢复这些引用。

本叶只补已完成历史组：

1. 原最近10组/whole-group裁剪不变。对已验证terminal S1的tool消息，超过既有16384-byte阈值才生成结构摘要：原tool name/call ID、完整内容字节数/hash、最多1024 UTF-8 bytes原文excerpt、exact reference。该摘要是原文片段，不自称语义总结/成功/新授权；assistant/USER文本与组顺序保留。source缺失或legacy非S1不造ref。
2. Reference绑定当前SDK Run、terminal evidence ID/envelope hash、消息ordinal、完整content SHA-256；page offset写入ref。按既有1024 excerpt边界分页，不扩大原分区阈值。模型仍只提供原`context_page_in(reference_id,source_hash)`二字段。
3. page reader从当前Host Run绑定及SDK公开start读取实际初始消息。只接受其中真实保留组产生的ref，重建整个quoted group与start字节精确比较。terminal S1/receipt、Host terminal identity和SDK公开terminal/transcript一致后才读取；不能任意ref全库搜索/按文本相似查找，不能跨Run复用，不能把同subject当权限。
4. 用实际当前Run请求事实解析DisclosureContext，原`PrimaryHistoryPolicy`检查该terminal完整来源；慢检查后重新解析当前disclosure。返回实际page内容、hash、offset/next ref及同一source binding。SDK持久普通tool result；无新Host ledger或SDK receipt。
5. `read_run_dependencies`的已有context_page_in分支独立重建page并与实际SDK effect.arguments/result精确比较，来源进入原evidence集合；最终physical guard继续查Memory/当前token。篡改、跨Run、未入场、来源遗忘均拒绝。无内容error不增加来源，也不成为成功证明。旧skill/scope page行为保留。
6. 主接现有ContextPageInStore一个专用reader；默认primary生产启用。Carver仍owns primary_history的A7来源继承、primary_visibility、ACK/terminalhook/main policy/coordinator，不改这些区域；本叶owned新`primary_context_pages.py`、primary_context投影、primary_dependencies的page分支、context_page_in_tools小分流与main注入小hunk。

必要新控制：真实大tool完成组→新Run看摘要→公开page-in取尾页→实际出站内容/来源一致；close/reopen同Run仍可解析；wrong hash/foreign Run/ordinal tamper拒绝；page读取后forget，final guard拒绝零发送；实际完整多tool组顺序与预算不变、未完成/非S1不造引用。只测新控，不重跑原closure9/resume5。

剩余明确保留：当前未终结Run中旧`page:causal:*`仍缺真实来源resolver，不能由本叶历史page宣称已修；跨Run语义状态来自真实task_scope_update与closure，不用摘要自动写认知/权限。initial compact TaskScope directory及真实Provider token低估校准单列，不借此调整oracle或宣称全部S5/compaction完成。跨Memory/Host的final检查非原子撤权事务，保留原最终fence。
