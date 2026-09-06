# Closure 后台物理请求守卫

2026-09-06；base Host `666b475b`。本叶只 Host closure，未改 SDK/原库/pin，不恢复旧 compactor，不接 Carver 的 ACK/context_authority。

## 原义务与未完成链

S5 Task6 / S5b AC1：COMPLETED SDK Run 仍有 material dirty、真实最终答复时，同 Run provider/model/config 补一次 closure；Host terminal 前提交 mutate/no_mutation 或显式 pending。当前五态 attempt、lease/CAS、成功重放及 UNKNOWN 禁重发继续有效；main._closure_adapter 默认前台 guard 要求另一种 handoff，不能授权该独立 post-turn 请求。

旧 `closure_request_hash(run,scope,watermark,last_assistant_hash)`、plan_id、evidence_set_key 不变。完整请求另外绑定，不给旧记录重新签名，不拿同 scope 当来源许可。

## 最小 authority 与实际出站

新 `ClosureRequestAuthority` 复用实际 SDK stack public reads、Host PrimaryHistoryPolicy/current disclosure resolver、Host S1 store。main 仅 closure fence/factory 段注入，默认前台 Provider guard不变，provider incarnation/model/price继续原校验。

1. Prepare 读取真实 HostRun/SDKRun/subject/primary/owner/generation、原 turn/hash/permission token、Run binding；披露上下文由既有 resolver 用真实绑定 request_id 解析，不使用虚构前台 handoff 或 closure request grant。保留 scope revision/watermark 和确切原观察。
2. 每个将发送的 scope 文本字段必须有来源证明。复用当前 scope disclosure projection 对实际 create_new 公开 effect/receipt 与生产依赖进行验证；不能仅复用 scope owner 判断。title/goal 与该已验证投影精确一致才可发；非空 resume/后继修改若现有 reader 无完整来源，明确 `closure_source_incomplete` pending，零发送，不删字段伪称完成。material event 摘要要求精确持久事件/hash、明确 source Run、S1 refs及该 Run 完整 public dependencies；未知/缺失/超过现有上限拒绝。最后答复须与当前实际 SDK terminal Run 公共答复及依赖绑定。不相似文本反推来源。
3. 将现 analysis 的可选 observer 模式补入 `reserve_post_turn_attempt(input_observer_tx=...)`，保留原 closure lease/state/binding 准入：与 lease/attempt reservation 同事务保存 Host S1 `closure-attempt-input`，绑定实际 ordinal/request ID、完整 `provider_request_json` 与hash、原观察、源依赖、权限token、scope revision/watermark、binding 和成员。请求 ID 只由旧 request_hash+ordinal 派生，carrier 不反向进入旧 request_hash，避免循环。事务内只重核 Host 已准备事实，不在 writer 锁内等 Memory/SDK外部调用。callback取消与普通故障均rollback后关闭owned连接。
4. 专用 physical guard 在真正 ProductProviderAdapter 出站前读该 durable carrier，要求唯一 purpose=closure/handed_off/未settled attempt，原owner/generation/lease有效，实际request bytes全部一致。每个来源通过当前 PrimaryHistoryPolicy/public Memory，再读新Host snapshot复核原token、head/revision/attempt；不能把 await 前的结果当出站授权。Host/Memory跨库无法组成原子撤权，明确保留这项边界。
5. 来源不完整/当前拒绝时 durable pending；取消原样传播。已成功 attempt 重放仍不物理发送；handed_off/sent_unknown仍不重发。无carrier的旧未成功请求不能当空证明通过。失败原因不写原文或敏感payload日志。

## 改动范围与必要 oracle

预计新 `execution/closure_request_guard.py`、`semantic_closure.py` prepare/observer接线、main closure fence/_closure_adapter小块和专属测试；尽量复用既有 invoker/fence接缝，不增加DDL/持久ledger。若源码证明需要不同接缝，先给Dirac具体差异，不扩展终态ACK路径。

必要控制只围绕本次：真实 production resolver+合法来源正向到达真实adapter transport；完整请求篡改/异Run/缺carrier/同scope无来源均零send；慢visibility期间权限或scope/owner换代、晚遗忘零send；成功重放零新send，UNKNOWN保持禁止重发；prepare/observer取消不吞，不存在输入carrier而attempt已reserve的半提交。现有analysis14/providercold1不重跑。

Dirac契约挑战已纳入。初版实现新增authority/guard及完整请求carrier；members保留S1实际run_id并单列真实Host入场对应SDKRun，包含全体allowed refs。scope pending只允许已知固定reason代码且复核原来源Run；任意旧reason文本及没有完整来源的非空resume/修改goal仍pending，不能标closure功能全完成。初次prepare可生成既有确定性scope projection；physical复用既有projection，不重建内容，但既有 `open_exact(materialized_only=True)` 仍写真实access receipt，先前“physical只读”措辞不准确。不会新建authority账本。原scope观察的全范围扫描成本仍存在，不能把32事件/64输出refs称全局成本有界。

新增8场景已分批通过；另真实两Run CREATE_NEW→RESUME_EXISTING 暴露独立读事务自锁，窄修后单独通过，共9唯一场景，见 [RESULTS](RESULTS.md)。这不是完整closure/长旅程/native验收。

读写边界：prepare在原invoker读取TX退出后、reservation写TX开始前执行；physical在handed_off提交后执行。原 `reader(BEGIN)` 内 `read_run_dependencies→verify_scope_disclosure→open_exact→access receipt writer` 在DELETE journal下可自锁。`4a86ecb0` 保留首个Host snapshot事务，关闭后用无显式事务的自有只读连接执行公共依赖/当前policy读取；material event/S1核验与最后Host snapshot仍各自使用事务，完整carrier/最终owner、lease、原token、scopehead相等要求不变。访问审计未删、未吞locked、未增timeout。真实route `resume_existing` 与scope state的非空 `resume` 字段是不同义务：前者本片有真实正控，后者来源不完整仍pending。
