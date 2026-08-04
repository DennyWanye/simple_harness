# 关键假设 spike 结果

日期：2026-08-03。所有生产数据库查询均使用 SQLite `mode=ro`；合成分页使用内存数据库；没有修改业务代码或用户数据。

## H-1：真实父子失败/接管因果链

命令：用 `backend/.venv/Scripts/python.exe` 只读查询 `backend/userdata/data/workflow.db` 的
`execution_runs/run_links/child_signal_inbox/task_failure_reports/attempt_failure_sets/attempt_records`。

实际结果：Session `2e69be7e-0b16-4bfb-a774-d61e588c5ec3` 中找到了唯一一条“Root completed + Child failed”历史链：

- Root `a3a63c99...` 最终 `completed`，Child `child-3a2033...` 为 `failed`，且有 attached structural link。
- Child 先产生 `accepted`，后产生已送达的 `terminal` signal。
- Root 账本存在指向该 child、失败 tool call/effect 的 `child_workflow_failed` FailureReport。
- FailureReport 进入 failure set；后续 plan version 2 Attempt 同时携带该 `trigger_failure_set_id` 并 `supersedes` 原 failed Attempt。
- replacement Attempts 成功后 Root 才写入 completed terminal。

结论：**H-1 成立**。`completed_with_recovery` 可以从既有稳定 ID 确定性推导；不能仅凭“child failed + root completed”猜测。

### H-1 A2 只读复跑校准（2026-08-03）

Task 9 首次按当前 `HarnessPublicReadService` 读取同一真实 Root 时，暴露出计划把旧 UI 的
“106 条 activity 记录”误当成 public-fact 数量，同时发现两个真实归约缺口：同一 execution
source stream 被按 fact kind 错误拆开，以及旧 Root 的 replacement Attempt 没有新格式的显式
terminal parent ref。修复后在 state.db 精确临时副本完成 v26 migration、workflow.db 保持只读，
同一 Root 的当前生产读取结果为：

- 366 个完整 public facts，`projection_complete=true`，无 causal-cycle/incomplete 诊断；
- 6 个实际出现阶段：理解、委派、执行、验证与修复、等待用户、交付；
- 29 个唯一逻辑工具，其中 23 个 shell；provider-call/effect 只在语义视图出现一次；
- child 原始 failed 保留，FailureReport/failure set、plan-v2 replacement Attempt 与后续 root
  terminal 构成完整链，aggregate 为 `completed_with_recovery`。

因此真实 fixture oracle 校准为 `366 facts / 6 phases / 29 logical tools / 23 shell`。原 106-record
用例只保留为七类 taxonomy 的合成单元覆盖，不能再冒充真实 Godot Root。这个校准不改变
AC-SRV-6 的“6～8 个可理解阶段”质量门，也不允许丢弃公开事实来迁就旧数字。

## H-2：附属 LLM callsite 是否具备身份来源

命令：`rg -n -C 2 "_make_live_str_llm_call\\(|lambda: local_llm or cloud_llm|_resolve_agent_provider_chain\\(" backend/main.py`，并沿调用处读取 fanout/AgentLoop/Companion authority。

实际 inventory：

| 调用 | 现状 | 可用身份来源 / 必须改动 |
|------|------|-------------------------|
| FactExtractor | message hook 只传 `message_id/content/role`，后台 task 丢失 Session | 扩展 message-written hook/fanout，按 message id 读取或直接携带 `session_id/root_run_id`；不能只靠 ContextVar |
| query rewrite / entity extraction | 长期 service 持有全局 resolver，但调用发生在当前检索链 | Agent 构建/检索入口显式绑定 workload context；ContextVar 只传输已验证身份 |
| GoalChecker | AgentLoop 已持有 session goal/RunContext，但 callback 无参数 | checker 调用时显式传当前 Run workload context |
| problem pre-analysis | product turn 已有 trusted RunContext，构造物却持有全局 resolver | 在 turn 调用边界注入 session-aware provider factory |
| memory tools | tool runtime 有 prepared call / Host Run 身份，module bind callback 无身份 | 从 tool execution context 构造 workload context；自然语言解析失败返回工具错误，不污染 Root terminal |
| MemoryCurator | AgentLoop 内 fire-and-forget，当前 callback 无 Session | schedule 时复制显式 workload context并登记 bounded task；取消/Run 结束后不得继承脏 ContextVar |
| preference interpreter / workflow matcher | CompanionTurnAuthority 已有 turn identity，callback 仍取全局 | authority 调用接口透传 Session/Root workload context |
| Reflection | 定时扫描跨 Session 数据 | 明确定义为 `system_maintenance`，只走 BackgroundModelPolicy，不能冒充任何 Session |

结论：**H-2 部分成立并闭环**。所有会话附属调用在其上游都能获得或回查身份，但 FactExtractor 和 fire-and-forget curator 必须改入口契约；单独增加 ContextVar 会留下串 Session 风险。

## H-3：长账本 keyset 分页

命令：内存 SQLite 创建 1,500 条有序事实，以 `seq > cursor ORDER BY seq LIMIT 256` 分页并在读取时归约阶段。

实际输出：

```json
{"rows":1500,"page_size":256,"pages":6,"first":1,"last":1500,"unique":1500,"complete_ordered":true,"insert_ms":0.981,"page_plus_reduce_ms":2.408,"phase_count":7}
```

结论：**H-3 成立**。本地 256 条内部页可以完整、无重复、按序归约；正式实现仍要使用真实复合 cursor、WAL 快照一致性和压力回归，不能把这次微基准当最终性能验收。

## H-4：真实工具记录的公开投影

命令：只读解析上述真实 Root 的 `execution_effects`，对 23 条 `run_shell` 记录比较原始 payload、现有 `TraceRedactor` 输出和 default-deny 摘要投影。

实际输出摘要：

- 真实语义视图共有 29 个唯一逻辑工具，其中 23 个 shell effect 成功；原始 23 个 shell
  `prepared + outcome` 共 78,322 bytes。
- 现有 `TraceRedactor` 输出 80,242 bytes：它会遮蔽已知敏感 key，但仍保留完整 command/stdout/stderr 结构，不能直接作为公共 UI contract。
- default-deny 摘要只保留步骤号、工具、动作类别/命令动词、状态、timeout、exit code、输出字符数，共 3,470 bytes（约为原始 4.4%），没有暴露命令或输出正文。
- 仅显示 `运行 cat/sed` 仍不够符合人类直觉；正式 projector 必须为每个工具定义稳定 `action_code + safe target label`，例如“读取 project.godot”“运行项目校验”，而不是把原命令截断后展示。

结论：**H-4 的安全边界成立，但原草案的信息表达需收紧**。采用 ToolSpec allowlist + 专用 summary transformer；现有通用 redactor 只能作为第二层文本清洗，不能决定哪些字段可公开。
