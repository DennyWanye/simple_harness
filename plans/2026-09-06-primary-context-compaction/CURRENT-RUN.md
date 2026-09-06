# 同叶继续：当前 Run 大结果

2026-09-06，源码调查确认，以下接线尚未实现/测试，不是无限后继占位。历史terminal S1首批验证后接同一page入口，冻结H077不改。

真实消费者是 `ProductRunContextAuthority.prepare_snapshot`：公开Context load、核对prior_context_revision、`_plan_turn_messages`裁剪、记录snapshot、SDK reservation/physicalinvoke。`causal_groups._item`在tool结果超过16384 bytes时生成`page:causal:index:hash`，但没有resolver；raw index还会随已裁历史前缀变化，不能据其字面值授权。

已有公开事实足够设计Host接线：

- SDK `ToolEffectRecord`保存真实effect/internal call/raw call/turn ordinal/state/version/result/evidence ref；公开operation audit给effect与parent provider关联。
- `ProviderInvocationRecord.request_json`由实际coordinator reservation持久化，携带真正发送的snapshot消息；公开projection receipt供确切invocation定位与版本/hash复核。不能只拿模型自己输入的ref或同Run命中当入场证明。
- `read_primary_tool_causal_sources`已用上述公开事实比对whole public transcript，虽然当前注释写completed，函数本身并不依赖Run terminal。需在新source控明确验证prepare时所有已纳入工具都settled、无pending兄弟effect遗漏，而非仅改注释宣称可用。
- Host ExecutionEvidenceIngress只对objective事件额外登记S1；普通tool的完整result不能假定已有S1。当前Run采用`settled_public_effect`来源分支，历史仍为`terminal_s1`，不伪造terminal或补签不存在的S1。

最小后续实现：

1. 在当前snapshot预算前加专用source projector callback；只替换真正已settled tool result的大内容，保留原role/name/raw call ID、原assistant工具调用和当前USER/open group。先按当前Host input anchor重建public transcript，再用真实effect/parent关系映射ordinal，不以相同raw call ID/相似文本猜测。`project_primary_transcript`已有公开文本/credential redaction，page与摘要使用同一投影规则，原始result hash与投影content hash分别保留。
2. 来源descriptor固定run/effect/version/result hash/parent provider identity/public内容hash；不绑定未来page effect或未来snapshot hash，避免hash循环。request JSON正常持久化这个descriptor，不新建ledger。
3. 现`context_page_in`增加该来源分支：用调用工具的实际parent provider request JSON确认summary/ref确曾入场；parent response中的call ordinal/arguments必须与当前真实effect匹配，再重读目标settled effect并精确重建公开内容。同Run未入场/当前还pending/foreign/仅相同call ID拒绝。旧无request_json明确不可验证，不重写旧记录。
4. 输入来源通过已有`read_run_dependencies(before_effect_id=目标effect)`严格前缀继承。page本身若来自更早page，前缀必须严格递减，不能包含自己/未来effect；原当前policy和最终token检查保留。物理guard对本次实际summary/page重新核源，裁掉片段不生成新授权。
5. 首个新真实控制：同Run大tool已settled→新snapshot引用→模型publicpage-in→实际MockTransport尾页；重复raw call ID跨Provider turn不混源；pending sibling不能冒已settled结果；forget/权限变化后零外发；原Run重开复用真实parent request JSON不依赖内存映射。只新增决定性控，不重复历史已绿与原9/5。

没有新增LLM摘要调用、后台compactor或第二authority。这个分支完成前，当前Run旧`page:causal:*`仍是明确未完成产品路径；历史page绿不能替代它。
