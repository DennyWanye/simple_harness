# C08 原来源与保留摘要准备

2026-09-07；主报告 `fa9e7798`/当前 H0710/M619 首批5unique PASS38.78s：四例 retained＋实际 wrong-assistant，子进程均自然退出，旧 scalar13 未跑；结果和 ARCH 由主固定。本文新增的 **phase 提取封装尚未执行**，不将原 child 的绿色自动转记为新封装已验。未改 scalar helper、dispatcher、原 MD/gold 或阈值。

范围仅 `08-suppressed.md` 的 C08-01/06/11/18。这里的“摘要”具体实现为真实旧 USER→assistant 完整组中的 assistant 摘要/介绍；不是伪造的短期 chunk，也不代表经过大模型摘要质量验证。

| Case | 原 setup SHA-256 | 设置端明确的旧 USER → assistant |
|---|---|---|
| C08-01 | `5e2f5465e45a67b659a9875584685d6e356bd0e3e358ac94b42220811cd22052` | 我的地址是梧桐巷42号测试楼。 → 地址摘要：梧桐巷42号测试楼。 |
| C08-06 | `5aa12863f216618e6c42af3f2c7f043489b99fc4b7b0b55649e5859a53e03d4a` | 物品藏在绿柜 → 备注摘要：物品藏在绿柜。 |
| C08-11 | `5ace3a63a417ee3e8de233b5c14fa4cfa263737cfcf86bc46e85cc786ddca26f` | 我的学校是青麦学堂。 → 旧介绍：学校为青麦学堂。 |
| C08-18 | `43844155eaa42d6baa1976cd58ccd5e2ef04acf829e41633536288b808775df1` | 我以后只选深绿色 → 偏好摘要：以后只选深绿色。 |

这些是按 setup 独立编写、进入 manifest hash 的合成旧组实现，不冒称数据集给出了完整原对话；C08-18 保留其明确的原话。没有新增个人事实，没有使用当前题面或 gold。每例独立目录，只建这一个旧组，不填充十个无关回合凑短期窗口。

## 专用模块和调用顺序

`backend/deskpet/quality/corpus_c08_retained.py`，主基线 `61f474e5`，自有分支 `feat/corpus-c08-retained`。

1. `compile_c08_retained_setup(case_id, setup_text, scenario_clock=instant)` 返回不可变 RetainedSetup，入口重编译验证原 setup/hash、固定旧组与 payload 映射。只收这四例；原 scalar 的 `UNPREPARED_CARRIERS` 保留。
2. `RetainedSummaryProvider(batch, model=original_model)` 提供一次本地 loopback HTTP response，`start/registration/close` 与现 C07 phase 结构兼容。独立 provider ID 为 `corpus-retained-fixture`。只接受一个模型名匹配、非 system 消息恰为固定旧 USER 的物理 POST；失败不重放，不代理外部请求。固定 assistant 输出不含工具调用。SDK 原价格估计/预算仍执行；本地产生的 usage=0 不代表真实模型账单。
3. 主 actual factory 选择此临时 provider，生产分析后台保持已关闭；调用 `execute_c08_retained_group(path, subject, batch, service, runtime, ingestion_worker)`。真实 enqueue、SDK Run、终态写入、USER outbox ACK 后，由 PrimaryConversationAuthority 回读验证原两消息、真实 turn、真实 COMPLETED 与原 USER analysis lineage。此方法不插入任何 terminal、metadata 或摘要证据。
4. 复用主 `collect_turn` 对此设置 Run 单独留证，失败也必须保留 attempt/trace，不能混正式题分母。调用 provider 的 `verify_completed_phase(observations=..., binding=SdkRunBindingV1...)`，核单次真实 handoff、response ID/request hash、provider binding，再关闭 loopback。只有这一物理阶段证据能证明本地请求数量；seed 返回不自行宣称调用数。
5. 主关闭当前 **owned** cognitive runtime 的 manager（保持原 main 分析 authority 配置，不改 installed）。用同 Memory 路径及 clock 打开临时 fixture manager，public builder 配置 `HostEvidenceAuthority(path)`、新 `SetupFixtureDeliveryAuthority()`、`PrimaryConversationAuthority(path, subject=principal.actor_id)`、`HostHistorySourceAuthority(path)`，仍用既有分类及 filter policy。旧组的实际 USER 已由 outbox 正式入库，不得另 ingest 一个替代 setup source。
6. `prepare_c08_retained_seed(path, manager, principal, batch, executed, delivery_authority)` 绑定局部分析执行器；执行原 USER 的真实 job，完全沿其已持久 provider/model/config lineage，不换成员或自造旧 Run。必须观察 APPLIED + ACCEPTED application，公开 replay 原 plan 取得 mutation receipt；A 的 evidence IDs 必须仅为该原 USER。
7. 沿 `PrimaryShortIndexingService.register_group` 公开登记完整旧组，包含真实 terminal ancestor 和 assistant source。先验原文与摘要均 `visible=true`、A 非空；仅调用公开 EVIDENCE suppression 指向原 USER，再验两者拒绝原因均为 `history_suppressed`、A graph 为空。摘要通过真实 source ancestry 被阻断，不能以其它异常/缺来源/空库当成功。
8. 原 Host S1/两条注册身份回读不变，公开 registration 幂等回放成功；原 job 随后 IDLE 仅作已 APPLIED 后无剩余任务的核对。返回原 group、双 visibility snapshot、真实 suppression request/decision、A mutation receipt、before/after graph 等。关闭临时 manager，再由原 main cognitive runtime 公开重开，接真实评分 Provider 与原 current USER。

`setup_complete=True` 仅在上述 seed 函数实际完成后返回。原主 child 已验证该流程；共享评分 dispatcher 尚未接入，不代表四例已完成正式模型评分。

## 已验编排的最小提取接口

新增 `backend/deskpet/quality/corpus_c08_retained_phase.py`，只提取原 child 中的设置阶段，不复制 main 初始化、C08 scalar 或 Provider 评分回包逻辑，也不修改旧控制源码。

```python
phase, seed = await execute_retained_phase(
    main=main, service=service, runtime=runtime, batch=retained,
    worker=worker, directory=directory, provider=retained_provider,
    collect_turn=collect_turn, record=write_result,
)
```

调用形状沿 C07 `execute_recent_phase`；`collect_turn`/`record` 复用 session 现有真实 SDK trace 收集与 ignored 写入函数。输入无 current/gold、base_url/key，不负责创建 main/服务/registry 或启动正式模型请求。返回 `(phase, seed)`，前者只含独立旧组的 Run、Provider invocation、request/trace hash、阶段与重开状态，后者为设置端原始来源和 suppression 证据，**两者都不能加入评分 Context**。

共享 dispatcher 只需以下插入点，由其 owner 后续实现：

1. 对四例先 `compile_c08_retained_setup`。**跳过通用/scalar 预 seed，包含原先 scalar 已支持的 C08-01**；此叶 A 必须从实际旧 USER 任务产生。
2. 在现有 main Provider registry 阶段选择 `RetainedSummaryProvider` 的 ephemeral registration，沿已有 main 初始化到 foreground 就绪、分析后台关闭，不复制初始化流程。`initialize_only` 不执行 retained phase，保持该阶段 NOT_RUN。
3. 调用上述 helper。它核实际 main runtime、同 subject/可信 clock，执行旧组→公开 trace/binding→关闭 loopback→关闭 owned cognitive manager→同 DB 临时 fixture manager seed/suppress/APPLIED 后 IDLE→关闭 fixture→以原 production analysis authority 重开，并公开核 A 为空、两来源明确 `history_suppressed` 且旧组不变。
4. 只有 helper 正常返回 `phase.status=CONFIRMED` 后，调用既有 `admit_scoring_provider`（仍检查旧 resolver 无活动 binding），再走原 `execute_scoring_turn` 的原 current USER。正式统计只读新的 scoring Run，不能合并设置 Run 的 invocation；不改 model/window/预算，不读取 gold 提前判分。

新增 ignored 文件均在 `setup-retained/`：`observation-*`、`seed.json`、`production-reopen.json`、`phase.json`。后者由 finally 写入；若旧组失败且尚未开始 trace 收集，按实际 queue snapshot 的精确 delivery_key 找原 turn，最多在5秒物理界限内收集一次公开 SDK trace。已开始的部分收集不覆写/重跑。失败返回异常，保留尝试数/错误阶段，绝不继续 seed 或评分；cleanup 失败也不返回 CONFIRMED。

helper 只关闭局部 fixture manager 和本地 Provider；整体 runtime/SDK/session/workflow 的异常退场仍由现 session finally 负责。fixture 关闭失败时不重开生产 manager，不自动重试本例。前置契约拒绝发生在 helper 接管阶段前，调用方仍须关闭其已启动的 Provider。`real_model_calls=0` 仅是已核本地 fixture Provider 阶段，不说明 main 的可选 embedder 是否尝试/成功加载。

## 必要后续控制与边界

原5项已覆盖四例真实旧组、job、source ancestry suppression、mapping 拒绝、实际错 assistant 拒绝、生产重开与下一请求无旧内容。原 USER job 的 IDLE 位于正式下一请求之前，不能据此声称下一请求新生成的分析任务也已结清。新封装的 session 消费边界另由主统一核验；不重跑旧5/13来替代这个新增入口。不得用另一份 scalar source 独立 suppression 冒充摘要与 A 同源。

这里只验证保留的 assistant 摘要/介绍，不建短期 generation、不加载 embedder，也不声称向量召回经过测试。未来若原要求另指产品独立滚动 summary artifact，需要补其真实生产来源，不能把本旧消息组改名当该 artifact。零查询不等于后台 suppression gate 已执行；正式题只在终态后按原 oracle 评分。C08-02/04/09 的关联派生物、13 的提醒、15/16 的纪要/检查表及19/20 的关系/别名仍属后继，不从本四例外推。
