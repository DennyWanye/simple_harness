# 原Prospective剩余6 lifecycle续接

2026-09-06，复用simple_harness-typed-recall-source-oracle，feat/typed-recall-prospective-lifecycle，base ea57e720。
前片13格/Procedure4/主组合15项不重跑；未改H073/M0613/原矩阵与阈值，无新venv/checkout。

## 原输入与实际路径

- in_progress：原pending CREATE→真实public registration ACK→synthetic event matched signal→exact授权REVISE in_progress。
- completed：同上真实triggered前史→exact授权REVISE completed；不伪称实际外部action已执行，是synthetic fixture显式状态更新。
- rescheduled：pending CREATE/ACK→exact授权REVISE rescheduled→新revision的实际public outbox registration ACK。
- cancelled：pending CREATE/ACK→exact授权REVISE cancelled。
- expired：原trigger为event release_succeeded，无trigger_at/expiry字段；只验证explicit_user已授权的REVISE expired，
  绝不把它描述为真实时间到期signal。时间与authority expiry已前片独立测试。
- candidate：合法CREATE candidate不产生registration outbox，不能发行凭空ACK；原candidate回合真实no_recall保留，
  再独立公开candidate→pending授权REVISE/真实ACK作同memoryID正对照；对照不覆盖/改写原candidate观察。

## 独立证明义务

所有最终state/payload/epistemic/verification来自冻结原输入；新adapter只从实际public receipt得到ID/revision，
按实际event signal结果推进revision，不能假装signal是第二次MemoryMutation。
每个synthetic scheduler输入保持独立来源记录、run/target/trigger/clock/hash；继续使用前片public authority port。
对最终outbox ACK必须绑定对应实际revision，禁止沿用旧authorityID/nonce/registration claim。
原负例必须有实际成功CREATE/REVISE receipt后调用真实recall，setup异常只能BLOCKED。
候选正对照与其他合法前史分别记录并验证，不能用后来pending PASS替代原candidate无披露断言。

初次契约记录源码准备状态；现ea030952经限定源码审查并通过新test/原6格，见[RESULTS.md](RESULTS.md)。
固定源码后Dirac只读审查，再145默认锁2GiB/180s必要新tests+原6格定向验证；不跑无变更13格。

## 固定源码实现与待测清单

新增独立lifecycle adapter/oracle；normal runner仅对原6格dispatch，旧13格路径保持。
信号oracle新增显式registration_state/ack_identity参数用于rescheduled与candidate正对照的真实ACK，默认值不变。
所有signal证明是实际调用记录的时序prefix/suffix视图，没有构造新的API结果；最终state/target/revision由独立原输入路径确定。
一个新增集成test方法实际跑6原格，含6state篡改、stale-target、删除新revision outbox、candidate正对照篡改；
后继ea030952另增两个完整wire自洽P1篡改，最终11项负向检查，非11个pytest方法。
通过固定源码review后只跑该新方法+原6formal，不跑无变更的旧13；若失败保留raw后固定修复复审。
