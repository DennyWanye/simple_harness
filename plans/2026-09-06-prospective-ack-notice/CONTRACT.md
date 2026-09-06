# 新 ACK 的 Host typed 提醒条目

更新：2026-09-06。base `55eb273d`，H079/M618不变。r18原FAIL见 [审计](NATIVE-AUDIT.md)。

实际生产链 `prospective_ack → ack_presented_tx → mandatory exit → SDK terminal → primary.messages.page` 没有提醒正文载体；模型可合法ACK后只回答算式。原AC4 processed=durable ACK不改，不撤销ACK、不增加pending、不调用repair重发。正文质量与ACK分开。

本叶复用现有 `prospective_occurrences` ACK proof，仅新插入增加 `notice_contract="host.prospective.notice/v1"`。旧proof/hash/重放receipt保持原样，旧ACK没有marker不产生新notice。无DDL、通知ledger或SDK制品变化。

读取把新ACK投影成独立 `role="reminder"` 条目，正文直接来自public occurrence的 `action_text`，显示标签“提醒”。稳定notice_id=canonical SHA256 of `["host.prospective.notice/v1", owner, sdk_run_id, occurrence_key, ack_receipt_id, ack_receipt_hash]`；分页位置从该hash固定派生，冲突拒绝。message_ref仍用既有primary/turn/index/content/source承诺。不是assistant/tool消息，不改SDK transcript/terminal S1，也不进入新USER/short来源生产。

page/detail读取必须验证：真实subject/primary/HostRun→SDK绑定；ACK row/hash→同Run presentation→snapshot/group内exact occurrence与正文；真实mandatory exit；如Host已终态则验证公开SDK terminal精确身份及既有settled proof。ACK持久但终态尚未提交时，可从ACK投影提醒，不虚构terminal；FAILED/STOPPED亦可投影，避免成功ACK后又永久丢正文。terminal持久失败后重开仍基于同ACK产生同item，不追加/重发旧ACK。

当前读授权使用实际Host `USER_REVIEW` DisclosureContext（非伪造execution Run），principal必须实际runtime。public occurrence inbox核exact identity/content、当前未suppressed与允许隐私/lifecycle；复用真实已登记outbox→mutation/signal source→S1 provenance，以及 `PrimaryHistoryPolicy` 最终公共 `check_history_visibility` 批次。缺来源/篡改/未知状态failclosed；不把Host存储的旧action文本或已签snapshot当当前许可。当前只沿现SELF UI读语义，未开放对外披露。

原read model每页有界10turn/50item；inbox复用有界最多16×200扫描，来源递归64/256证据上限，超过拒绝，不宣称P99或总库扫描成本已解决。前端沿原owner/epoch/inflight及display invalidation门；仅新增typed role及notice_id验证，稳定message_ref替换读取，不发通知动作或自动ACK。

必要新控（不复跑旧repair14）：真实public mutation/timer/ACK，模型只47仍有独立notice；同item分页/detail/重开稳定且零额外Provider；新ACK后的FAILED/terminal未提交仍可读；旧无markerACK无notice；wrong owner/key/snapshot/terminal拒绝；late memory-only或source suppression后page/detail无正文。前端只接受有真实wire notice_id的reminder并渲染独立标签/正文，旧response经epoch失效仍丢弃。

后端可读≠前端已渲染≠用户已读。源控与UI组件控不追认r18；新原生场景由主在新组合验证，绝不重开已ACK原例。


Dirac首轮修正（源码审查，未执行反例）：public inbox的action/origin固定在原occurrence revision，content_hash却取当前Memory head。合法REVISE/改期导致后者变化，应撤下旧notice、旧detail不可用，page仍可读；不能把合法变化当corruption阻断整页，也不能在旧notice_id下换正文。原origin/action错配仍严格拒绝。新增一条真实public REVISE控制，原ACK/terminal commitments前后精确不变。
