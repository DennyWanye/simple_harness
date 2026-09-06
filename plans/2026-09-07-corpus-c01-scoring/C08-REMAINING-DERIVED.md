# C08 派生联络、关联、引用及提醒来源

2026-09-07。沿 `9637e750` 后继，在同一干净树的 `feat/corpus-c08-remaining`
准备源码；主报告 `dbb7a83a` 的15/16首次2PASS16.01s属于上一叶，不重跑。
本叶 **5项新控制 NOT_RUN**，无资源、模型、网络、安装或制品操作。

## 原 setup 与实际载体

精确原文/hash仍来自 `corpus_c08.SETUPS`，原08 Markdown、current、gold不改。
设置端没有current/gold参数；原current直到独立评分Run才进入。

| ID | 原 setup 的缺口 | 本叶构造的独立公共载体 |
| --- | --- | --- |
| 02 | 电话A存在还不足以证明派生联络资料受抑制 | A电话claim + D结构化联络资料 `{telephone: 测试内线47}` + 实际assistant联络资料文本 |
| 04 | 称呼A之外的称呼关联 | A称呼claim + D `{address_term: 舅舅阿青, kinship: 舅舅, name: 阿青}` + 实际assistant关联资料 |
| 09 | 金额A之外的引用 | A月预算claim + D `{reference_kind: monthly_budget, amount: 800元}` + 实际assistant预算引用 |
| 13 | 纪念日A之外的真实提醒 | A纪念日claim + P实际Prospective pending revision、公共outbox/source、S5c真实注册ACK + 实际assistant提醒记录 |
| 19 | 收件人A之外的旧对话关联字段 | A旧收件人claim + D `{recipient: 邻居小松, relationship: 邻居}` + 实际assistant关联资料 |
| 20 | “蓝盒”所对应的另一实体没有给出身份 | 不编译、不构造合法seed；不得用当前“青灯阅读器”或gold补目标 |

本叶不是把“原标量已抑制”升级为“派生源已测”：四个D具有真实独立memory ID、payload hash、
mutation operation及同USER证据；assistant也是实际COMPLETED组中的独立S1 registration。
初始化要求空Memory/空历史，前读要求exact两个节点、两个可见history，封闭本fixture的全部
实际派生载体；不声称覆盖任意外部地址簿、文件系统或未知历史资料。

04/19 的载体是 **显式关系字段的 semantic claim**。公开
`SemanticRelationKind` 目前仅 `APPLIES_TO`，供 semantic claim→Procedure 关系使用；
不能借它签发亲属/邻居/实体同一性。这里不构造graph edge、不调用legacy DigitalTwin关系表，
`entity_relation_edge_exercised=false`。原setup称呼关联/旧对话收件人字段可由该实际claim与S1
文本表达；不能据此宣称通用实体关系索引已实现。

## 固定来源与复用接口

`backend/deskpet/quality/corpus_c08_derived.py` 的冻结映射列明每例旧USER、assistant及字段，
均为设置端撰写实现，不冒称原语料已有逐字旧对话。USER显式说出关系成分，模型不猜分号/角色；
没有新增人物、外部联系人ID、未知实体、Procedure授权或成功证据。

现有 `compile_c08_retained_setup` 为新五例纳入完整derived payload及实现默认值到manifest。
原四例及15/16的字段、domain、manifest、kind/defaults保持；新五例phase kind为
`deterministic_retained_derived_carrier`。

复用同 `RetainedSummaryProvider`、`execute_retained_phase` 与实际main初始化；仅增加：

- `expand_operations`：在原USER job中追加D/P，与A同一真实strict_atomic plan/application。
- `verify_extra_views`：公共receipt逐项核operation、源证据、hash/revision/lifecycle及两节点非空。
- `fixture_options`：仅13通过公开S5c initializer/validator绑定实际HostProspectiveSignalAuthority。
- `register_reminder` / `verify_reminder_reopen`：仅13核真实注册ACK及重开后的持久事实。

原source/evidence/terminal都由实际SDK→Host outbox→S1产生；不插造终态、不伪Run/receipt，
不扫SDK SQL。不改shared session/dispatcher、schema、权限或SDK。

## C08-13 的必要时钟及权限边界

原setup只有“4月17日”，没有年份、时分或周期规则。本fixture明示采用 **下一次4月17日
12:00，Asia/Shanghai，单次提醒**；在共同可信时钟2026-09-06 10:00下，目标为
2027-04-17 12:00+08:00。该默认值和完整trigger纳manifest，旧USER也明确请求下次当日中午提醒。
不读取current决定时间，不宣称年度循环。业务clock沿原phase，物理预算/lease不改。

P由真实旧USER job的Prospective CREATE产生。S5c的
`PublicRegistrationAuthoritySource`从实际公开target mutation/outbox读取来源，consumer真实
apply signal并持久ACK；fixture自己不issue grant、不计算SDK outbox ID。source必须回绑P的
实际mutation receipt/ref、operation P、revision1和pending lifecycle。

之后对该USER evidence执行公共suppression，A/P及两个历史来源普通不可见；关闭fixture后
用原production manager重开，普通graph仍为空、history理由仍`history_suppressed`。
实际历史注册ACK/source必须不变。这种权威历史回读是审计/调度事实，不是普通可见性证明。
本叶 **不声称注册已取消、未来timer触发已试或最终提醒外发门已试**；没有伪invalidated ACK。
`timer_fired`、`future_delivery_exercised`、`registration_cancelled`均明确false。

## 待主线一次执行的五项新控制

文件 `backend/tests/quality/test_corpus_c08_derived_main.py`：

`tests/quality/test_corpus_c08_derived_main.py::test_actual_main_derived_carrier_forget_reopen_current`

参数ID为 `C08-02`、`C08-04`、`C08-09`、`C08-13`、`C08-19`，共5 unique。
直接复用既有actual-main child，不复制初始化、不收集原retained5/docs2/dispatcher1/scalar13。
child仅增加公共fixture options与reminder重开核验；旧case对应空options/无reminder分支。

每项检查真实job APPLIED/IDLE、两独立memory及两S1源、遗忘前非空可见、exact suppression、
源物理保留、生产重开、下一原current物理请求无旧值/setup。额外核setup drift/值篡改拒绝；
02附20未知目标仍拒绝。13独立核真实ACK/source/ref与重开不变，trigger使用实际公有字段
`trigger_at`，lifecycle从公共graph/source读取，不从没有该字段的mutation receipt猜取。

只在主current H0710/M619/S0313目标上首跑，使用既有Python和默认资源runner锁，raw写新的
ignored批次。普通隔离控制通过不等于真实模型质量；Provider阻塞仍独立保留。
共享dispatcher五例allowlist接入由主/Hegel协调；本叶不擅自开放或改统计分母。
