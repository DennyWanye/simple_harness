# 真实 Manual workspace challenge 的主对话入口

2026-09-07，source-only / NOT_RUN。独立 `feat/manual-workspace-binding-ui`，base `0e146792`。不合当前原生候选，不改 H079/M619/SDK failed schema，不扩目录授权范围。旧原 root 续改 Auto 与 public Manual service 结果保留；Manual 原生仍未完成。

## 现有事实与复用

context_route.create_new 的真实 append 已产生 task_workspace_manual_challenges/proposals 和原 S1，原复用 root 的 filesystem identity pin 也在 S1。失败前 record_tool_invocation 将 binding_challenge、new scope、exact SDK Run/effect/proposal hash 持久化在 Host 原 context_route journal。ToolResult.failed 只携带 code/message，因此不从工具文本抽授权。旧 ProjectDirectoryCard 是另一套 legacy decision/nonce，不复用其授权身份。

已有 WorkspaceBindingRuntimeAuthority.decide_manual_binding 与 Host `binding.manual.decide`：写真实用户决定 S1，验证原 challenge/proposal/subject、当前 Manual policy、时限、root 和 base binding revision，生成真实 decision/grant/binding ACK；同决定重放复用，冲突拒绝。本叶只加可信 pending 读取与 UI 身份绑定，不另造决定或 grant ledger。

## 最小接口

- `primary.bindings.pending({primary_ref})`：认证 subject 的当前 writable primary 下，有真实 context_route rejection 关联的原 Manual challenges；UI 最大32条，超限显式 unavailable，不装作完整。同 Host read TX 关联 original invocation/effect、foreground Run→SDK binding、challenge→proposal→原 S1，并重算各自规范 hash、核 subject/scope/root identity；不查 SDK 私库。无可靠 journal 关联的 orphan challenge 不自动获得可操作 UI 身份，不能猜属于某个 Run。
- 返回每项 `primary_ref/run_ref/sdk_run_ref/generation/effect_ref/challenge_ref/challenge_hash/scope_ref/proposal_hash`、原真实 root 的显示路径及 identity、expires_at_millis、pending/expired/denied/bound 状态。路径仅供用户识别授权目标，不作为客户端提交的 authority。原始模型正文不回传。当前政策非 Manual、root 已变化、payload损坏或 binding revision 过时不可批准；真实 ACK 以原 store 判定，不将决定已记录等同 binding 已完成。
- `primary.bindings.decide({上述身份字段,decision:allow|deny})`：服务端重读同原 challenge/journal，exact 匹配用户正在操作的身份后，调用现有 `service.decide_manual_binding`。不接受客户端 path、grant、nonce替换或model bool。原 port 再核当前 policy、root、时限及 durable binding；任何跨读取 await 后先重核身份再委托。用户只需本次卡片一次明确决定，不要求其再从模型回复复制 ref。

主对话直接挂新独立卡片（不限定 current Run 仍active，否则工具拒绝后Run结束会丢入口）；按已验证 primary/owner identity查询，显示原目录与新Scope。收到状态变化、重新连接、focus/显式刷新时重新读取；旧连接响应不得覆盖新身份卡片。允许/拒绝按钮仅操作当前 exact pending 项。超时显示结果未确认并重读原状态，不自动另发 allow。真实 bound ACK 后显示新 Scope 引用及“继续新任务”的明确下一输入指导；不伪造模型 context_route，不恢复旧 complete Scope。新 Run 或仍未绑定 task 的原 Run 须真实 resume_existing(newScope) 才能获得对应 task route。拒绝/过期维持不可写。

## 实施范围

新增 `memory/primary_workspace_bindings.py`、`primary/PrimaryWorkspaceBindings.tsx` 与必要专属控制；service/API最小两个转发；PrimaryChatView只挂新卡片。当前主测试/原生候选不改。不重写旧 primary decisions 或 project_directory legacy协议。必要 schema 只限 UI request/response，Host 数据库无 DDL。

读取32条仅约束输出，现journal/challenge跨表查找不称全 subject 扫描成本有界，也不宣称P99。原 challenge已持久但 invocation未完成落盘的崩溃窗口不能伪造关联；该恢复边界明确保留。

## 必要新 oracle（均 NOT_RUN）

1. 真实 Manual context_route 原root append拒绝→真实 pending公开读取同原challenge/root/newScope→用户 exact allow→原 binding ACK→真实新Scope route及file effect，不改旧Scope。
2. foreign subject/primary、错challenge hash/Run/effect混绑、expired/root替换拒绝，零新grant/文件；deny原决定持久且不可被allow覆盖。
3. response丢失后reopen/list识别实际已记录决定与bound状态，同决定幂等，不重发不同challenge；同源读取证明不能靠模型伪造。
4. UI使用实际wire DTO控制：卡片从pending读取产生、一次显式点击提交原identity；断线/重连、旧响应、超时未知不显示成功，不自动allow。真实native由主后续candidate验证，模拟transport不代替原生。

工具搜索说明单独提交 `ea58f019`：生产 projection纠正 OR词频说明、legacy toolset忽略事实和 tool_search→describe→activate 名称/流程。没有修改SDK排名或冻结manifest；不宣称修复r24模型循环。该小hunk也未测试。
