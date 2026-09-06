# 真实 Manual workspace challenge 的主对话入口

2026-09-07，source-only / NOT_RUN。独立 `feat/manual-workspace-binding-ui`，base `0e146792`。不合当前原生候选，不改 H079/M619/SDK failed schema，不扩目录授权范围。旧原 root 续改 Auto 与 public Manual service 结果保留；Manual 原生仍未完成。

## 现有事实与复用

context_route.create_new 的真实 append 已产生 task_workspace_manual_challenges/proposals 和原 S1，原复用 root 的 filesystem identity pin 也在 S1。失败前 record_tool_invocation 将 binding_challenge、new scope、exact SDK Run/effect/proposal hash 持久化在 Host 原 context_route journal。ToolResult.failed 只携带 code/message，因此不从工具文本抽授权。旧 ProjectDirectoryCard 是另一套 legacy decision/nonce，不复用其授权身份。

已有 WorkspaceBindingRuntimeAuthority.decide_manual_binding 与 Host `binding.manual.decide`：写真实用户决定 S1，验证原 challenge/proposal/subject、当前 Manual policy、时限、root 和 base binding revision，生成真实 decision/grant/binding ACK；同决定重放复用，冲突拒绝。本叶只加可信 pending/status 读取与 UI 身份绑定，不另造决定或 grant ledger。

## 最小接口

Dirac三项校准已落实到源码：proposal/challenge.run_id 是 Manual 派生域，不与 foreground/SDK Run 比等；Run关联来自真实journal/foreground binding，另外核 proposal.idempotency_key 为原 context-route:{sdkRun}:{effect}及S1。状态追加 allow_recorded（允许决定已提交、binding待完成），只允许原同决定的恢复，已bound不因原challenge过期变成未授权。human_memory_request_boundary覆盖exact回读和service.decide全部await，慢policy读取后嵌套复核当前连接；列表发送也走同连接最终边界。原 S1/challenge/journal并非同写TX，仅在同readTX核关联，orphan窗口不掩盖。

- `primary.bindings.pending({primary_ref,cursor?})`：认证 subject 的当前 writable primary 下，有真实 context_route rejection 关联的原 Manual challenges。只分页未过期且未决定的项，以及 allow 已提交但 binding ACK 尚未落盘的项；bound/denied/expired 历史不挤占待办页。每页最多32项，用原 invocation_id 作不透明 cursor，并在同 read TX 读取原 recorded_at，以 `(recorded_at,invocation_id)` 严格倒序 keyset 续页；游标只定位，不授权，必须属于同 subject/primary。超过32个真实 pending 通过 next_cursor 继续，不再整体 unavailable；同时间戳、第一页项处理完后继续页仍不靠 offset 跳行。同 Host read TX 关联 original invocation/effect、foreground Run→SDK binding、challenge→proposal→原 S1，并重算各自规范 hash、核 subject/scope/root identity；不查 SDK 私库。无可靠 journal 关联的 orphan challenge 不自动获得可操作 UI 身份，不能猜属于某个 Run。
- `primary.bindings.status({primary_ref,challenge_ref})`：按 exact challenge 回读原状态，不受 pending 过滤；仍完整核原来源及当前连接。用于决定响应丢失、冷重开核对与已绑定结果的真实恢复。查无唯一原关联即拒绝，不能将 pending 中消失解释成成功。
- 返回每项 `primary_ref/run_ref/sdk_run_ref/generation/effect_ref/challenge_ref/challenge_hash/scope_ref/proposal_hash`、原真实 root 的显示路径及 identity、expires_at_millis、pending/expired/denied/allow_recorded/bound 状态。路径仅供用户识别授权目标，不作为客户端提交的 authority。原始模型正文不回传。当前政策非 Manual、root 已变化、payload损坏或 binding revision 过时不可批准；真实 ACK 以原 store 判定，不将决定已记录等同 binding 已完成。
- `primary.bindings.decide({上述身份字段,decision:allow|deny})`：服务端重读同原 challenge/journal，exact 匹配用户正在操作的身份后，调用现有 `service.decide_manual_binding`。不接受客户端 path、grant、nonce替换或model bool。原 port 再核当前 policy、root、时限及 durable binding；任何跨读取 await 后先重核身份再委托。用户只需本次卡片一次明确决定，不要求其再从模型回复复制 ref。

主对话直接挂新独立卡片（不限定 current Run 仍active，否则工具拒绝后Run结束会丢入口）；按已验证 primary/owner identity查询，显示原目录与新Scope。收到状态变化、重新连接、focus/显式刷新时重新读取；旧连接响应不得覆盖新身份卡片。允许/拒绝按钮仅操作当前 exact pending 项。超时显示结果未确认并重读原状态，不自动另发 allow。真实 bound ACK 后显示新 Scope 引用及“继续新任务”的明确下一输入指导；不伪造模型 context_route，不恢复旧 complete Scope。新 Run 或仍未绑定 task 的原 Run 须真实 resume_existing(newScope) 才能获得对应 task route。拒绝/过期维持不可写。

## 实施范围

新增 `memory/primary_workspace_bindings.py`、`primary/PrimaryWorkspaceBindings.tsx` 与必要专属控制；service/API三个窄转发；PrimaryChatView只挂新卡片。当前主测试/原生候选不改。不重写旧 primary decisions 或 project_directory legacy协议。必要 schema 只限 UI request/response，Host 数据库无 DDL。

每页32条仅约束输出，UI只保留当前页加至多1个原点击决定的exact状态，不积累全历史；当前页与status是各自当前snapshot，不宣称跨请求原子。没有冻结全历史的快照cursor，新项回首页读取。读取32条仅约束输出，现journal/challenge跨表查找不称全 subject 扫描成本有界，也不宣称P99。原 challenge已持久但 invocation未完成落盘的崩溃窗口不能伪造关联；该恢复边界明确保留。

## 必要新 oracle（均 NOT_RUN）

1. 真实 Manual context_route 原root append拒绝→真实 pending公开读取同原challenge/root/newScope→用户 exact allow→原 binding ACK→真实新Scope route及file effect，不改旧Scope。
2. foreign subject/primary、错challenge hash/Run/effect混绑、expired/root替换拒绝，零新grant/文件；deny原决定持久且不可被allow覆盖。
3. 允许决定已落盘而append中断，list必须返回allow_recorded；只重试同原决定完成binding，不生成第二decision。已有bound在时钟越过challenge期限后仍可exact读取/重放；同源读取证明不能靠模型伪造。独立进程reopen控制已写源码，尚未执行，不由一次重新bind service冒充。
4. ACK 必须调用公开 `WorkspaceBindingSetReceipt.verify_grant(grant)`，包括原 base_binding_set_revision；带重新计算hash但错base的恶意读取记录仍拒绝。DENY 复用原store的 `verify_challenge`，仅保留其明确非授权DENY例外，不转成grant。
5. 34条已bound/expired历史之后33条真实pending可分页，第一页决定后原cursor继续不漏；控制使用真实Host S1/authority/store写入，额外journal是显式Host投影fixture，不声称模型执行了这些额外工具。
6. 真实签名连接在慢policy read中重绑，原pending/decide均拒绝且零decision；新连接可回读。原运行栈全部关闭后独立子进程重建factory/authority，exact bound读取与同allow重放保留原decision。
7. UI使用实际wire DTO控制：卡片从pending读取产生、一次显式点击提交原identity；断线/重连、旧响应、超时未知不显示成功，不自动allow。真实native由主后续candidate验证，模拟transport不代替原生。

工具搜索说明单独提交 `ea58f019`：生产 projection纠正 OR词频说明、legacy toolset忽略事实和 tool_search→describe→activate 名称/流程。没有修改SDK排名或冻结manifest；不宣称修复r24模型循环。该小hunk也未测试。


## ad188 审查后本次源码修正（2026-09-07）

两P2在本叶处理，不留“只支持32同时pending”的限制。新增status与分页消费，原ACK/DENY公共验证复用；保留三处初始challenge校准。当前7个backend控制（原4加rebind/cold-process/paging）和4个UI控制均 **NOT_RUN**；exact正向附带独立错base反例，分页包含原DENY拒绝例外与冲突。未运行pytest/tsc/build/native，不占共享资源；没有以旧7项root续改绿替代本叶证据。当前实现仍有无journal的orphan来源不可操作、后台SQL扫描成本未量化、真实native入口未验边界。


## 06fe 父卸载 P1 修正（source-only）

Dirac确认旧 `lastDecision` 在卡片effect局部；真实 PrimaryController断线会置primaryRef=null，父View卸载卡片。后端pending已排除bound，故旧孤立port重连控不足，不能称真实UI可恢复。本次新增 `PrimaryBindingRecovery`，由稳定PrimaryChatView持有：只保存每个精确 `(verifiedOwnerKey,primary_ref)` 最近一次用户点击的完整identity，不保存root路径、nonce、grant、决定结果或正文；连接变化/卡片不可见/卡片卸载不销毁该引用。新签名连接重新确认owner与primary后，卡片仅用该namespace引用读 `primary.bindings.status`，还需全部identity匹配；没有自动decide，也不将缓存引用视为授权。其他owner或primary不读取/显示此引用。

替换原孤立port lost-ACK重连测试为 `views/PrimaryBindingRecovery.test.tsx` 真View/controller/boundPort/条件卸载控制：服务端bound但ACK丢失→断线卡片确实卸载→旧响应不显示→不同owner、不同primary各自重挂零status→回原owner/primary，pending=[]后必须exact status才显示原newScope/继续指引，始终仅原1次decide。当前仍7 backend+4 UI **NOT_RUN**，无测试/build/安装。此状态仅在当前View生命周期内保留；整个应用进程退出后的UI自动发现旧bound引用不在本控保证，已有独立进程backend控制仅证明给定exact原引用可查，不外推原生冷启动交互闭环。
