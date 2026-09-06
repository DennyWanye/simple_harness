# C08 原来源与保留摘要准备

2026-09-07；新增专用源码，NOT_RUN，主当前 installed target 后续统一验证。未运行网络/模型/测试，未改 scalar helper、dispatcher、原 MD/gold 或阈值。

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

`setup_complete=True` 仅在上述 seed 函数实际完成后返回；当前源码尚未执行，不代表四例已 ready。实际主 dispatcher 还需实现阶段调度、异常退场和 setup trace 独立保存；本叶不改共享文件。

## 必要后续控制与边界

主当前 H0710/M619 target 统一验证四例真实旧组、job、source ancestry suppression。至少核一次错误 setup/摘要 mapping 被拒、实际错 assistant 无法作完整组、USER/assistant 原 ID/hash 在遗忘后仍存、原 USER job 无残余 pending；正式 Context 下一轮不得夹带旧摘要。不得用另一份 scalar source 独立 suppression 冒充摘要与 A 同源。

这里只验证保留的 assistant 摘要/介绍，不建短期 generation、不加载 embedder，也不声称向量召回经过测试。未来若原要求另指产品独立滚动 summary artifact，需要补其真实生产来源，不能把本旧消息组改名当该 artifact。零查询不等于后台 suppression gate 已执行；正式题只在终态后按原 oracle 评分。C08-02/04/09 的关联派生物、13 的提醒、15/16 的纪要/检查表及19/20 的关系/别名仍属后继，不从本四例外推。
