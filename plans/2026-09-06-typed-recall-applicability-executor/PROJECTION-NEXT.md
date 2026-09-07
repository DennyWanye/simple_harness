# 剩余原 projection：构造与字节契约分离

2026-09-06，只读调查；不测试、不改冻结fixture/SDK/阈值，不重跑applicability三格。
原AC：testcase/human-memory-program/TC-HM-13-typed-recall-result.md:78明确要求五类rich-source→minimal projection，
evidence/classification/conflict/cross-scope/source/extra typed字段全部精确剥离，不能仅执行已最小化seed。

## 原格完整hash保持

| 原格 | 冻结payload_hash |
|---|---|
| selection-budget/projection:episode | `d45ff7d7fd315f3c26ae81954aff12b4bec6420c03e459f8248be309f66ebce2` |
| selection-budget/projection:semantic | `6ddc898cede2944ef03cff4c4e46d62b1d9ab29bf349770d4aa95c7a0ea3e365` |
| selection-budget/projection:procedure | `756bf2a8b670fb8e52556de57a9ff69394dfaab000898c44ce3eb25afea02ab7` |
| selection-budget/projection:prospective | `f3218e1a14ea1c2f47781b304b445a9e2ff84247781df674661723b603fdfdaa` |
| selection-budget/projection:short_horizon | `97b3c21be8ec13de46b8ddc3b139513ba39e6e3c80559338de98ee7f675a047a` |

## 确切接口差异

固定Memory f2a6a706（0.6.13）src/simple_harness_memory/backends/sqlite_v5.py:4965–5026为实际typed recall公开payload producer。
Harness固定073对应src/simple_harness/runtime/memory_protocol.py公共DTO定义：
- Episode:2121、Memory:4994：原occurred_interval.start/end ISO对象，实际occurred_start/end数值，两者键/字节不同。
- Semantic:2194及to_json、Memory:5005/9435：原qualifiers={}，公共tuple[str]转JSON list，空值也是[]，不能原hash通过。
- Procedure:2333、Memory:5009/9447：原applicability={tool:git,version:2}，公共tuple[str]存/读list，合法git@2并非原object。
- Prospective:2462、Memory:5020：原trigger={kind:event,event:release_succeeded}，实际strict event DTO有trigger_kind/event_authority_ref/condition/condition_hash。
- Short:原occurred_at为ISO字符串；Memory:3638公开短候选payload使用float occurred_at。该条只读producer定位，未新增执行证明。

上述不是“还缺Host提醒接口”，注册/signal或applicability snapshot无法改这些字节。禁止用normal_projection转换后
重算hash冒称原literal hash通过；也不为测试改SDK历史wire。两种后继选择均须主协调明确契约：
(1) 原始字节hash必须保持：需要独立兼容projection产品端口/明确输出契约，现0613 public port不满足；
(2) 接受旧语义→当前public wire显式映射：保留原payload/hash并先独立校验，再另记actual public payload/hash，
不得称原字节相同，须由原AC所有者决定映射适用性。当前不自作此决定，五格仍保留原阻断义务。

## 不被字节问题掩盖的fixture缺口

normal_inputs.py:profiles仅挑allowed_payload_fields；CaseManager.seed默认personal scope及evidence-case-*；
现fixture没有完整原rich source，因此即使预期wire解释达成，也不能直接删FULL_SOURCE_RECORD_CANARY_AND_CROSS_SCOPE_SETUP_NOT_ESTABLISHED。
可合法公开补：独立S1 evidence_id=secret-evidence、required privacy SENSITIVE、公开mutation、显式task/personal scope及
授权recall context。source_ref必须绑定实际SDK生成ID，不能强行mem-procedure等literal替代SDK权威；需同样保留原标签→实际ID映射。
extra_typed_field不能塞入strict typed DTO再期待被剥离；可存于真实S1 rich输入，但它只证明S1未透出，不能宣称typed record含该字段。
Semantic原resolved还需真实conflict resolution链，不能用uncontested代替。
这些需独立完整source/receipt/public投影证据，对照只改一项的泄露反例，不能借minimal seed返回安全载荷而通过。

## 下一可执行范围

当前scope内无需再跑上述已知不同wire的五格制造重复BLOCKED。优先对剩余公开可构造的executor做独立叶；
Procedure observed晋升必须真实terminal observation链，不能借本次APPLICABILITY_SNAPSHOT增加成功次数。
本报告不宣称SDK产品隐私漏洞：当前证据是冻结literal与公开协议差异及runner fixture不足。
