# Prospective public registration/signal successor

2026-09-06，base3de9879f，复用原source-oracle树/feat/typed-recall-prospective-public。
初次固定为源码准备；现已完成限定验证，见[RESULTS.md](RESULTS.md)。无新checkout/venv/模型/native，H073/M0613冻结不改。

## 原输入与可证范围

原typed-recall-v3的19个Prospective authority阻断：11 epistemic默认pending + 8 lifecycle；另projection独立。
输入来自original source_record的event release_succeeded；没有原时间触发值，不得偷偷替换事件为定时器。
当前最小实现原pending（11+1格）和triggered（1格）：前者只需scheduler registration ACK，
后者必须真实消费synthetic event signal产生matched decision与新revision。
其余6 lifecycle、projection、trigger-missing等独立原格不在此片，原BLOCKED保留；不伪称19全部关闭。
32非法epistemic组合保持原输入与setup拒绝，不得把前置失败当recall通过。

## 公开生产seam与fixture事实

CaseManager仅新增ProspectiveSignalAuthorityPort；public register_principal_owner/read_outbox取得实际
memory.prospective.registration.requested，原target/revision/trigger/hash绑定outbox id+payloadhash。
独立synthetic_scheduler_input先记录fixture自身的ACK/event事实，包含subject/run/target/trigger/clock。
随后issue_prospective_signal_authority→apply_prospective_signal并真实重放；不读SDK私表、不用SDK测试helper。
这是SDK协议测试的合成scheduler，绝不声称真实Host监听到release_succeeded或提醒已触发。
SDK信任Host authority port；signal的source receipt由本fixture issuer负责，它不是SDK替外部来源验真。

pending保持原state不变，ACK不增加revision；triggered沿原pending→triggered路径以实际signal替换直接REVISE，
不造第二条用户记忆/虚假成功terminal。normal oracle独立核实际唯一CREATE输入pending与final signal结果，
原recipe的triggered/state/预期不改。source content/evidence仍是原CREATE，selected revision绑定actual signal result。

新独立oracle无SDK import，从原event、独立synthetic输入、实际public outbox/result与所有hash检查exact
source/run/clock/expiry/ref/replay/trigger；pending只认可ACK，triggered还要求实际matched/applied。
normal_expected已有WITH_TRIGGER_SIGNAL只在该proof通过后传True。无proof仍BLOCKED。
Procedure源码和其四格结果保留；bridge需与Hegel并存source/context_use/procedure/prospective所有oracle指纹。

## 待固定审查后145默认锁下必要验证

- 原pending/triggered、explicit_user source_bound/user_confirmed及unverified：公开调用+完整独立oracle。
- 没有registration时真实no_recall以及event拒绝；注册后future signal/expired authority拒绝；
  actual synthetic event应用、close/reopen、同request零candidate reads与fresh request真实新revision。
- 独立time fixture（不是原401格）：原事件fixture不改，另DB以明确trigger_at/UTC输入；early time_due拒绝、等值触发成功。
- source/run/clock/trigger篡改必须命中独立输入绑定，不可借无关早期拒绝遮掩。

源码固定送Dirac只读挑战后，3方法有界tests→合法原13格定向正式执行；任何实际失败保留raw、固定修复复审。
每批145默认共享OS锁2GiB/180s，BUSY不启动child、不换锁；旧结果不覆写。通过后RESULTS/ARCH更新。
