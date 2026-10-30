# 任务书 D2（给 gpt-6.1-sol / Codex）：任务书 D 的补修

日期：2026-10-31。发包人与审核人：Claude 会话。在**同一工作树** `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-D`、同一分支 `sol/D-continuation` 上继续，基线是你的 `4fa97dc7e`。先 `cd /Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-D/sdk/simple-harness-sdk`。

## 0. 审核结论

任务书 D 的主体审核通过：持久受理、串行队列、合并窗口、策略、三个崩溃点、语音播报接缝都对，327 项终验可信。两个独立审阅各抓到阻断级，加一条按仓库硬规则必须修的，全部在本任务书里补。**车道放宽**：第 2 节列的文件都可以改。审阅者的两个探针测试在本机草稿目录，可直接拿来改成正式测试：
`/private/tmp/claude-501/-Users-taiwan-PROJECTS-SimplaHarness/323af475-6a44-4404-9489-48beed94af2b/scratchpad/rv/test_rv_voice_race.py`、`.../rv/test_rv_followup.py`（只读参考，正式测试写进 `test_continuation.py`）。

## 1. 阻断级（必须）

### 1.1 续办回合要在主会话自己的 Agent 上跑（主会话事后看不到结果与汇总）

现状：`TreeSessionChatService._agent` 为每条续办输入新建一个 `creation_key=continuation:...` 的 Agent。汇总跑完后结果被记成"已应用"，下一句用户输入回到主 Agent，`_commit_input` 不再附这些结果，`_complete` 也不重注历史——主对话永久失去这份结果（探针实测：用户问"刚才那份报告结论是什么"，模型收到的消息里既没有汇总也没有结果）。

改法：
- 删掉 `TreeSessionChatService._agent` 这段覆盖；续办回合走主会话现有的 Agent（和用户回合同一个执行者、同一份历史）。工具收窄只在**绑定**这一处做：`_bind(pool, agent, session_id, allowed)` 按本回合 `source == "continuation"` 传收窄后的 `allowed`（`_registered_tools()` 现有的收窄逻辑保留，它已经按 `_current_input` 的来源算），网关"只缩不放"挡住写命令。模型在说明里仍可能看到 `delegate_task`，调了会被拒——这是现有路径，不加新拒绝。
- **删掉 `HostSettings.continuation_max_model_calls_per_turn`**（连同校验、文档、测试）：主 Agent 的单回合模型调用上限就是会话策略的 `max_model_calls`，不需要第二个旋钮；`Continuation.model_limit` 直接取 `session.policy.max_model_calls`。续办用量预扣 `reserved_model_calls` 相应取这个值。
- 加测试：子结果到达 → 自动汇总 → 用户下一句"刚才结果怎么样"，断言发给模型的输入（`ScriptedProvider` 记录的请求）里含汇总正文或结果资料，且两个回合用的是同一个 `agent_id`。

### 1.2 语音接入窗口的竞争：同一条续办被文字回合再跑一次、重复计费

现状：`_run_inputs` 在**拿锁前**就把队列行标成 `running` 并 `claim` 扣额，然后才进 `_send` 等 `_send_lock`。`attach_voice` 正排队等这把锁时，协程取到下一条续办、标 running、扣额、挂在锁上；语音接入后 `_schedule` 看到 `running` 的续办照样 `claim`（额度已在）并播报、应用；语音断开后挂起的 `_send` 拿到锁继续提交——模型只收到"请只汇总以下结果"的空壳却被调用一次，`finish` 再用这次的数字覆盖语音那次的 `continuation_usage`。违反 I12，重复执行、重复计费。

改法（三条都做）：
- `_run_inputs` 先 `async with self._send_lock`（或等价地先拿锁），拿到后再检查 `self._voice is None`、**重读**这一行的状态，然后才标 `running` 和 `claim`；`_send` 若带 `_queued`，拿锁后先 `complete_if_applied(queued)` 并重读状态，已是 `done / failed` 直接返回已有回执，不调模型。
- `Continuation.voice_pending()` 只认 `queued`，不认 `running`。
- 测试：把审阅者的 `test_rv_voice_race.py` 改成正式测试——语音接入与后台续办同时争锁，断言语音应用后文字模型调用数不再增加、`continuation_usage` 不被覆盖、该续办只有一个 `applied` 记录。

### 1.3 用量只能多算不能少算（仓库硬规则，用户 2026-09-24）

现状：文字续办异常时 `fail()` 只标 `unknown=True`、`model_calls` 仍是 0；`finish()` 收到失败回执也按 0 记；只有语音"已交线路"那条路按预扣上限结清。

改法：拿不到准确数时按**预扣上限**记（`reserved_model_calls`），标 `unknown=True`；有回执时用回执里的 `model_calls`，但不低于已知的调用数。策略的 `used_model_calls` 据此计算。加测试：续办回合抛异常 → `continuation_usage.model_calls == reserved_model_calls`。

## 2. 一起改的（非阻断但要做）

1. **播报来源用结构化字段，不解析正文**：`_commit_announcement` 现在靠 `json.loads(handle.text)` 认 `source == "task_result"`。改成 `VoiceBinding.announce(text, *, priority=10, source: str | None = None, input_id: str | None = None)`，`SpeechHandle` 带 `source / input_id`，`_commit_announcement` 只看这两个字段；`result_source="task_result"` 只给真正的任务结果播报，`needs_response` 等普通播报不打这个标。`"cont-" + digest([session_id, sorted(event_ids)])` 三处重复，收成 `Continuation.input_id_for(event_ids)` 一处。
2. **子会话 directive 的消费条件**：去掉"父会话 `_current_input is None` 才执行"这一条（任务书要求主与子互不阻塞）。"有没有在途委派"不要每次轮询都对每条历史委派调 `host.snapshot`（单次约 20 毫秒，历史越多卡越久）：一条委派已经终态 ⇔ 它在收件箱里有结果或行上有 `error`，用账本里的这两个事实判断；只有既无结果又无错误的行才查快照，并把结果写回行上（下次不再查）。
3. **语音期间策略 hold / deny / 关闭续办时，子结果仍要能被听到**：`_schedule` 对带 `child_key` 的 `result_ready` 事件，只在该结果会走续办（`continuation_results` 为 pending/queued/applied）时跳过原有播报；被暂缓、拒绝或续办关闭的，照旧按原来的 `result_ready` 播报。
4. **去掉旧数据默认值**：`queued.get("idempotency_key", input_id)`、`r.get("idempotency_key", r["input_id"])` 之类只为没有该字段的旧行服务的回落都删掉；`child_send` 的行现在已经带全字段。
5. **一条路径**："未完成的旧回合"现在既有 `_send` 抛 `turn_pending`，又有 `_run_inputs` 自动补跑。二选一并写在记录里：建议 `send()` 只要会话里有任何未完成回合或排队输入就一律走队列（排在后面），`_send` 不再有补跑分支。
6. **续办轮次上限的语义**：`continuation_max_turns_per_session=50` 现在是会话终身计数，长期主会话用满后自动汇总永久停摆。改成"自上一次用户回合以来的连续自动汇总次数"（用户每说一句就重置），事件名、默认值不变，文档改。
7. `_input_errors` 只给 `wait=True` 的等待者用：读走即删；`wait=False` 的错误已经持久在队列行的回执里，不要再往字典里堆。
8. `continuation.py:367` 用原生 SQL 删 `voice_deliveries`：给 `SessionLedger` 加 `delete(namespace, key)`（若没有）并改用它。
9. `inbox.py` 文件头说明"父下一次输入把它当资料附上"已不完整，改成现状（父下一句附上 **或** 自动汇总）。
10. §4.2 的"重复处理不生成两次 Mission"补一条崩溃注入测试：directive 回合里委派已建、队列行还没记 `done` 时中断（用 `Session._checkpoint` 接缝），重开后不再建第二个 Mission。

## 3. 不动的

命令表、池编号、工具说明文字、任务层、工具层、连接器；不加兼容分支；不提交部署清单；不推送、不合并、不跑全量回归。

## 4. 完成标准

- 以下文件全绿（改源码后先重生成清单）：`test_session_async.py`、`test_continuation.py`、`test_session_tree.py`、`test_session.py`、`test_session_replay.py`、`test_host_sessions.py`、`test_realtime_bridge.py`、`test_chat.py`、`test_chat_cancel_recovery.py`、`test_tool_catalog.py`、`tests/agents/test_tool_exposure.py`、`tests/test_public_api.py`。语音文件要 `--extra realtime`。
- 改到的文件 `ruff check` / `ruff format --check` 绿。
- 记录追加到 `记录-任务书D-Sol.md` 末尾一节"D2 补修"：改了什么、第 2 节第 5 条选了哪条路径、测试命令与通过数、提交号。中文。拿不准的按最合理做法做完，不停下问。
