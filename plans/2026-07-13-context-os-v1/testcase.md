# DeskPet Context OS V1 — Testcase

> 唯一验收来源：[`acceptance.md`](./acceptance.md) 的 AC-CTX-1～19。本文是可执行测试说明，不以读代码、WebSocket 直注、直接 import backend、脚本回放或 boot-log 推断替代 Windows Computer Use 真 E2E。

## 0. 结果与证据约定

- 自动化结果目录：`plans/2026-07-13-context-os-v1/test-results/automation/`。
- 真机结果目录：`plans/2026-07-13-context-os-v1/manual-results-<YYYY-MM-DD>/`，截图放其 `screenshots/`，Tauri/backend 原始日志放 `logs/`，逐 case 记录放 `REPORT.md`。
- 每个自动化 case 必须保存命令、退出码、通过/失败数；不可把 focused green 冒充全量 green。
- 每个 UI 动作前必须先截图确定控件中心点，再记录：`坐标=(x,y)|动作=<click/paste/key>|期望=<可观察结果>`。中文输入使用剪贴板 + `Ctrl+V`；禁止用协议直注。
- 每个 UI case 至少保存 `before-<case>-<step>.png`、`after-<case>-<step>.png` 与对应 Tauri/backend 日志片段。日志必须能以 `purpose + request_id + attempt_id` 对上 UI 请求。
- 任何失败至少以三种真实 UI workaround 重试（重新聚焦、重新截图取坐标、重启该 case 的隔离 fixture）；仍失败记 FAIL，不可降级成间接证据。
- 日志和截图不得包含账号密码、token、完整敏感工具参数或未脱敏文件正文。

### 0.1 真机单步记录模板（HARD）

真机报告中的**每一个**交互步骤必须原样使用以下五段；“点击并输入”“打开并选择”等复合动作要拆成多个五段步骤：

```text
BEFORE: screenshots/<CASE>-<NN>-before.png；窗口标题=<title>；可见状态=<state>
DECLARE: 坐标=(<screen-x>,<screen-y>)|动作=<单次 click / Ctrl+V / Enter / key>|期望=<唯一可判定 UI 变化>
ACTION: Windows Computer Use 真鼠标/键盘；中文仅 Clipboard.SetText + Ctrl+V
WAIT: 每 1s 截图轮询，普通响应最长 90s、工具 180s、compact 300s；终止条件=<UI 文本/卡片/状态>
AFTER: screenshots/<CASE>-<NN>-after.png；实际=<state>；request_id=<id 或尚未产生>
```

超时后依次重试：①重新点击本轮截图测得的控件中心；②`Alt+Tab` 回 DeskPet 后重新聚焦再粘贴；③仅重启本 case 的 fixture/DeskPet（按 case 前置决定）。三次的 before/after 均保留；不得延长到无限等待或改用 WebSocket 注入。

计数 oracle：`REPORT.md` 每 case 先声明 `planned_ui_actions=N`；每个 action 必须恰有 5 条 `BEFORE/DECLARE/ACTION/WAIT/AFTER`，case 末由 evidence runner 输出 `actual_ui_actions` 与五类标签计数，要求五类均等于 N。fixture CLI 控制单列为 `fault_controls`，不计 UI action。E2E-02 的 21 个 prompt固定为 63 个 UI actions（每 prompt 的 click、Ctrl+V、Enter）和 315 条五段记录，另加建会话/开 Trace 等动作后更新该 case 的 planned 总数；任一标签少一条即 FAIL。

### 0.2 本轮必须实现的 E2E prerequisite

这些 fixture 是执行 E2E 前置，不是被测功能的替代品。若文件尚不存在，Task 5.3 开始前先按本规格实现并以自动化自测验收：

| 绝对文件 | 职责与固定接口 |
|---|---|
| `F:\projects\deskpet\scripts\e2e\context_os_mcp_fixture.py` | 由 **DeskPet MCP host spawn 的唯一 stdio proxy child**；实现 MCP initialize/list/call，但不持有 epoch/counters/allow-list，只通过 daemon HTTP（失败时不回退本地状态）读取/更新状态。注册 500 个 deferred tools `mcp_ctx_fixture_tool_001..500`；`tool_001` 接受 `{marker:string, sequence:int}` 并返回同值、`invocation_id`、SHA-256。不得读取 DeskPet DB。 |
| `F:\projects\deskpet\scripts\e2e\context_os_fixture_daemon.py` | **单一状态 owner**；监听 control 18991，持有唯一 epoch/counters/spec versions/faults，绝不 spawn/监护 MCP child。状态只在 daemon 内存中修改，事件由 daemon 单 writer 写 JSONL。 |
| `F:\projects\deskpet\scripts\e2e\context_os_fixture_ctl.py` | daemon 的无状态 CLI client；不另起状态、不发送 DeskPet 用户请求。命令：`start/status/reset/allow-session/deny-session/pause-after-describe/resume-describe/disconnect/reconnect/hot-replace/set-model/set-fault/clear-fault/stop`。 |
| `F:\projects\deskpet\scripts\e2e\context_os_provider_seam.py` | 独立测试 provider seam；支持 `pause_before_attempt`、fallback、force-finish 与模型 catalog/fault，事件写 `provider-seam.jsonl`。它不能改变 MCP epoch/counters。 |
| `F:\projects\deskpet\scripts\e2e\launch_context_os.ps1` | 唯一 Tauri launcher；参数见下节；只启动一个 Tauri，由它 spawn 唯一 backend 和 Vite，并把 stdout/stderr 重定向到指定日志。 |
| `F:\projects\deskpet\scripts\e2e\extract_context_attempt.py` | 输入 `--log --request-id --out`；按下面算法生成单 request JSONL，不改变运行态。 |
| `F:\projects\deskpet\scripts\e2e\run_context_os_evidence.py` | 固定自动化 evidence runner；读取下面 manifest，逐 nodeid 独立执行并生成规范 txt/json；0 collected、缺 assertion、缺文件或非零退出立即 FAIL。 |
| `F:\projects\deskpet\scripts\e2e\context_os_payload.py` | 只生成确定性 UTF-8 文本到剪贴板文件，不发送请求。`compact --round N` 输出固定 marker/chunk/hash，供真人 Ctrl+V。 |
| `F:\projects\deskpet\scripts\e2e\fixtures\context-os-automation-manifest.json` | AT-01～10 到固定 pytest nodeid/Vitest file/tsc/perf command 与 assertion id 的唯一映射。 |
| `F:\projects\deskpet\scripts\e2e\fixtures\context-os-8k.toml` | 仅测试配置：`context_os_v1=true`、effective model window=8192、compaction model 默认 `follow_session`。 |
| `F:\projects\deskpet\scripts\e2e\fixtures\context-os-mcp.json` | MCP 进程定义，command 指向上述 venv Python 和 fixture；仅挂入传入的测试 session allow-list。 |
| `F:\projects\deskpet\backend\tests\fixtures\context_os_off_golden.json` | 校准 HEAD 的 OFF 八类 task 工具 names/order；生成后纳入 review，测试不得运行时从 ON policy 推导。 |

固定拓扑只有一条：先独立启动 daemon；DeskPet 根据 `context-os-mcp.json` spawn **恰好一个** `context_os_mcp_fixture.py` stdio proxy child；proxy 的 initialize/list/call/handler-counter 请求以 HTTP 调 daemon 读取同一 epoch/spec/counters。daemon 不 spawn child，control CLI 不 spawn child，测试人员不手动 spawn child。child 崩溃/断连后仅由 DeskPet MCP host 按其既有生命周期在下一次 resolve/reconnect 重建。禁止 daemon/proxy 各自维护 counters。

single-child oracle：daemon `GET /health` 返回 `registered_proxy_pids`（由 proxy 启动/退出 register/unregister，只作诊断），任何时刻长度必须为 0 或 1；Tauri log 对同一 MCP server 只有一个 `spawn pid` 且该 pid 等于 health 中唯一 pid；Windows `Get-CimInstance Win32_Process` 按完整 command line 过滤 `context_os_mcp_fixture.py` 数量必须为 1。数量 >1 立即 FAIL；断连后旧 pid 必须退出，reconnect 后才允许出现一个新 pid。

initial MCP registration 时，proxy 的 list response 必须为每个 ToolSpec 附带不可变 provenance metadata：`fixture_epoch`、`fixture_spec_hash`（canonical name+description+input schema+spec_version 的 SHA-256）和 `fixture_spec_version`。DeskPet 在 `backend/deskpet/tools/registry.py` 注册时把三者保存进内存 catalog snapshot/ToolSpec metadata；`PreparedToolSet` capability ref 与 session snapshot 只持久化这些 refs/hash/version，不持久化完整 handler/schema。初始 `registry_revision` 的 evidence 必须和 daemon `/catalog-meta` 相同。

强制双 stale hook：① `backend/deskpet/tools/tool_search.py` 的 `tool_activate` 在 nonce/policy/budget 后、提交后继 revision前；② `backend/deskpet/agent/agent_loop.py` 每个 provider attempt 在 validate prepared set 后、序列化 payload前。两处均以 tool/ref 集合调用 `GET /catalog-meta?tools=<sorted names>`（500ms），逐项比较当前 daemon `fixture_epoch/spec_hash/spec_version` 与 registry/PreparedToolSet metadata。任一不一致或 hook timeout：原子 invalidate 对应 MCP specs、刷新 catalog snapshot 使 `registry_revision` 单调 +1、丢弃 activation candidate/禁止旧 schema 出站，并返回可恢复 `tool_catalog_stale` 要求下一 request 重新 list/resolve；不得在同 request 静默热更新继续执行。oracle 必须记录 `old/new fixture_epoch,old/new spec_hash,old/new registry_revision,stale_phase(pre_activate|pre_provider)`。

Session allow-list 是 **DeskPet host-side visibility hook**：测试 config 把 `ToolRegistry.visibility_scope`/resolver 的 test provider 指向 `GET /visibility?session_id=<host session id>&tool=<name>`；daemon 返回 `{allowed,fixture_epoch}`。MCP handler 不接收、猜测或读取 host session scope；执行前授权仍由 DeskPet Registry 用当前 host session eligibility 复核。由此可验证 A 可见、B 不可见，而不是要求 MCP 协议凭空传 session。

`pause_after_describe` 的触发点也在 host：`backend/deskpet/tools/tool_search.py` 的 `tool_describe` bridge 在 strict eligibility/schema hash 校验完成、向模型返回 describe result **之前**，以当前 host `session_id/request_id/tool/schema_hash` 调 `POST /describe-observed`。daemon 若 barrier armed 则保持该 HTTP response pending，直到 `resume-describe` 或 disconnect；因此 executor 能在“describe 已确定、activate 尚未发生”窗口注入断连。MCP proxy/handler 不负责猜测 describe 事件。日志 oracle 为 host `describe_hook_enter` → daemon `describe_barrier_enter` → control disconnect → host stale terminal，且无 activate/handler。

test-only 接线和 env 固定如下，缺任何键时 production path 不启用 hook：

| env | 值/行为 | 产品接线位置 |
|---|---|---|
| `DESKPET_CONTEXT_OS_E2E_DAEMON_URL` | `http://127.0.0.1:18991` | `backend/main.py` 构造 test hook；注入 `backend/deskpet/tools/registry.py` visibility provider 与 `backend/deskpet/tools/tool_search.py` describe observer |
| `DESKPET_CONTEXT_OS_E2E_PROVIDER_URL` | `http://127.0.0.1:18992/v1` | `backend/main.py`/provider config，仍走 `backend/providers/openai_compatible.py` adapter |
| `DESKPET_CONTEXT_OS_E2E_HOOK_TIMEOUT_MS` | `500`（describe barrier 已进入后允许 control 自身等待 300s，不使用普通 500ms client timeout） | registry/tool_search 与 optional-component hooks |
| `DESKPET_CONTEXT_OS_E2E_FAULT_HOOKS` | `1` | `backend/deskpet/agent/assembler/components/memory.py` 的 L3、`project_rules.py`、snapshot store 调用点、`context_compressor.py` 调用点 |
| `DESKPET_CONTEXT_OS_E2E_MCP_CONFIG` | `F:\projects\deskpet\scripts\e2e\fixtures\context-os-mcp.json` | DeskPet MCP host 的既有 server config loader；由 host spawn stdio proxy |
| `DESKPET_E2E_PROVIDER_DIRECT` | `1` | 测试时禁外部 relay/upstream；provider base URL 使用 18992 |
| `DESKPET_CONTEXT_OS_E2E_CASE_ID` / `DESKPET_CONTEXT_OS_E2E_SESSION_ID` | launcher 固定写入当前 case 与唯一 session | 后端与 dev+非 frozen+loopback hooks 一起构成五重信任门；E2E-02/10 仅对该 session 把固定 canonical `mcp_context-os-e2e_mcp_ctx_fixture_tool_001` 作为 direct selector（provider/MCP 回执仍按 remote 名 `mcp_ctx_fixture_tool_001` 对账） |
| `DESKPET_CONTEXT_OS_E2E_MAX_ITERATIONS` | 仅 E2E-10 为 `4`；其余 case 必须 unset | `backend/main.py` 仅在同一五重信任门通过时覆盖 companion AgentLoop 上限；前三轮为真实 fixture call，第 4 轮由 Context OS 最终 iteration 保留为 tools=None force-finish |

启动顺序必须 daemon health→provider `/v1/models` health→Tauri。startup health 失败则 launcher FAIL，不启动 UI。运行中 visibility hook timeout/非 2xx/坏 JSON 必须 deny/fail closed (`tool_policy_unavailable`)，不得展示/执行工具；describe observer timeout必须返回可恢复 `tool_catalog_stale`，不得继续 activate；fault hook timeout按“fixture unavailable”令该 case FAIL且产品请求 safe-fail，不擅自注入故障。上述 env 和分支仅在 `DESKPET_DEV_MODE=1` 且 URL 为 loopback 时允许，否则启动拒绝。

control API：`GET /health`、`GET /catalog-meta`、`GET /visibility`、`GET /fault/<name>`、`POST /describe-observed`、`POST /proxy-register`、`POST /proxy-unregister`、`POST /reset`、`POST /allow-session`、`POST /deny-session`、`POST /pause-after-describe`、`POST /resume-describe`、`POST /disconnect`、`POST /reconnect`、`POST /hot-replace`、`POST /model`、`POST /fault`、`DELETE /fault/<name>`、`GET /counters`。`disconnect` 令 daemon 状态变 disconnected、epoch+1；proxy 下次 RPC 收到 410 后自行关闭 stdio/退出，DeskPet 观察 EOF；它**不会**暂停 provider。`reconnect` 只把 daemon 状态改 ready、保持 counters、epoch+1，绝不 spawn child；下一次 DeskPet resolve 才由 MCP host spawn 一个新 proxy。`resume-describe` 只释放 daemon 中 describe barrier。每次响应含 `fixture_epoch`。

独立 provider seam 固定监听 `127.0.0.1:18992`，是完整 OpenAI-compatible **data plane + test control plane**：

- `GET /v1/models`：返回已 catalog 的模型 `{id,object:"model",context_window}`；至少 `ctx-primary`、`ctx-fallback`，专用模型仅在 control catalog 后出现。
- `POST /v1/chat/completions`：校验 `Authorization: Bearer ctx-e2e-local-key`，接收 DeskPet 真实 OpenAI request（model/messages/tools/tool_choice/stream）；未知/故障模型返回 OpenAI error JSON 与对应 4xx/5xx。
- non-stream：返回标准 `id/object/created/model/choices[].message(content|tool_calls)/finish_reason/usage`。当用户 prompt 明确指定 fixture tool/marker/sequence 时，返回合法 `tool_calls[].id/type/function{name,arguments}`；收到对应 tool result 后返回最终文本。
- stream：返回 `text/event-stream` 的标准 `chat.completion.chunk`，工具调用按 name/arguments delta 输出，末尾 finish_reason，再 `[DONE]`；断流/force-finish/fallback 必须分别有独立 attempt 事件。
- usage 的 prompt/completion/total tokens 为 seam 对收到的**实际 wire payload**确定性计数，并在事件中记录 request body SHA-256；不得用工具数乘常量。
- control：`POST /__control/pause-before-attempt`、`/resume-attempt`、`/fallback`、`/force-finish`、`/catalog`、`/model-fault`、`DELETE /__control/model-fault/<model>`、`GET /__control/state`。control 与 `/v1/*` 分路由，不计 provider request。

provider adapter 在 test-only seam 下增加可信 header `X-DeskPet-Purpose`、`X-DeskPet-Request-Id`、`X-DeskPet-Attempt-Id`；seam 以 header purpose + canonical messages 中最后一个 marker 匹配，不从自然语言模糊分类。固定 provider chain 只有 `ctx-primary → ctx-fallback`，两者 window=8192；除明确 fallback scenario 外所有请求必须使用 primary。scenario state 以 `(request_id,marker)` 隔离，状态转移表如下：

| scenario / matcher | 当前状态与请求约束 | seam response | 下一状态 / arm 清除 |
|---|---|---|---|
| normal：purpose=`agent_response`，marker 匹配 `CTX-NORMAL-*|RAW-*|DECISION-A-*|GOAL-B-*|CTX-FAULT-*|CTX-RESTART-*` | S0；任意合法 tools/none；仅在更具体 matcher 未命中后选择 | nonstream 或 stream 文本 `ACK:<marker>`；`finish_reason=stop` | DONE；无 arm |
| tool：purpose=`agent_response`，marker=`CTX-CALL-*`/`CTX-DISCLOSE-*` | S0，tools 必须含 `mcp_ctx_fixture_tool_001` | assistant tool_calls，arguments 为 prompt 中 marker/sequence；`finish_reason=tool_calls` | S1，等待同 tool_call_id result |
| tool-result | S1，messages 尾部必须是匹配 tool_call_id 的 tool result 且含 invocation_id | 文本 `TOOL-ACK:<marker>:<invocation_id>`；`finish_reason=stop` | DONE；清该 request state |
| fallback：control arm marker=`CTX-FALLBACK-1` | S0/ctx-primary | HTTP 503 OpenAI error `fixture_primary_unavailable`，不返回 choice/usage | S1；arm 保留 |
| fallback retry | S1/ctx-fallback，新 attempt_id、同 request/marker | `ACK:CTX-FALLBACK-1`；`finish_reason=stop` | DONE；仅 terminal 后清 arm；若再次 primary 或复用 attempt FAIL |
| force-finish：control arm marker=`CTX-FORCE-1` | F0～F2；每个请求 `tools` 非空，且前一 tool result 已存在 | 每步真实返回 `mcp_ctx_fixture_tool_001` tool_call，sequence=901/902/903，唯一 tool_call_id；`finish_reason=tool_calls` | F1/F2/F3；arm 保留，fixture handler 必须真实累计 3 |
| force-finish final | F3；host 新 attempt 且 `tools` 键缺失或 `null`（不是空数组伪装），messages 含前三个完整 call/result | 文本 `FORCE-FINISH-ACK`；`finish_reason=stop`，event 记 `observed_tools_none=true` | DONE；terminal 后清 arm；若 tools 仍非 null 则 409 scenario error |
| compressor：purpose=`context_compression`，marker=`CTX-COMPACT-*` | C0；`tools` 必须 null；model 为 resolved compression model | 严格 JSON 文本，含 objective/decisions/completed/pending/artifacts/blockers/source_ranges 和 marker hash；`finish_reason=stop` | DONE；一次 cycle/range 一次 response；故障 arm 在 error terminal 后清 |
| raw只读召回：purpose=`agent_response`，唯一 marker=`CTX-RAW-RECALL` | R0；canonical messages 必须同时含 `RAW-01`、`RAW-06`、`RAW-12` 的原始 user/assistant rows；不得靠 tool result 提供 | 文本逐项返回 `RAW-01/RAW-06/RAW-12`，`finish_reason=stop`，event 记 `raw_markers_seen=[...]` | DONE；不得产生 tool_call/receipt/snapshot 写 |
| page-in只读召回：purpose=`agent_response`，唯一 marker=`CTX-PAGEIN-RECALL` | P0；messages 当前 coverage 不含 `CTX-CALL-02` 正文，tools 必须含 `session_history_page_in` | tool_call `session_history_page_in`，参数为当前 session 的合法 reference/range；`finish_reason=tool_calls` | P1；等待匹配 tool result |
| page-in result | P1；尾部 tool result 必须来自 `session_history_page_in`、同 tool_call_id，且正文/结构化结果含 `CTX-CALL-02` | 文本返回 `CTX-CALL-02` 的 marker/sequence/result；`finish_reason=stop` | DONE；只读，不创建/更新 goal/todo/artifact |
| task只读回读：purpose=`agent_response`，唯一 marker=`CTX-TASK-READBACK` | T0；messages/protected snapshot 已有 `GOAL-A-0713/DECISION-A-0713/PENDING-A-0713/ARTIFACT-A-0713` | 文本原样返回四 marker 与 artifact identity；`finish_reason=stop` | DONE；tool_calls 必须为空，不创建/完成任何状态 |
| restart只读回读：purpose=`agent_response`，唯一 marker=`CTX-RESTART-READBACK` | T0；重启后 remount 内容已有同四 marker | 文本原样返回四 marker、artifact path、pending 未完成；`finish_reason=stop` | DONE；tool_calls 必须为空，snapshot revision 不因纯读增加 |
| 多入口只读：purpose 保持入口实际值，唯一 marker=`CTX-ENTRY-TEXT|CTX-ENTRY-CODE|CTX-ENTRY-WEB|CTX-ENTRY-RESEARCH|CTX-ENTRY-VOICE` | E0；header purpose 必须等于该入口 contract 的实际 purpose，不强制改成 agent_response | 文本 `ACK:<marker>:purpose=<purpose>`；`finish_reason=stop` | DONE；不得因 seam 统一 purpose |
| 故障只读：purpose 保持入口实际值，唯一 marker=`CTX-FAULT-L3|CTX-FAULT-RULES|CTX-FAULT-SNAPSHOT|CTX-FAULT-COMPACTOR` | E0；对应 fault 已 armed 且本 request trigger_count=1 | 文本 `ACK:<marker>:purpose=<purpose>`，或按 AC-5/8 返回受控 budget BLOCK；不得返回写工具 | DONE/error terminal；清 fault 后无写副作用 |
| goal ack：purpose=`agent_response`，marker=`GOAL-A-0713` | slash command 已由 host 建 goal；provider request 如存在不得带伪 goal tool | 文本仅确认 `GOAL-A-0713`；`finish_reason=stop` | DONE；权威 goal oracle 仍来自 host card/store |
| todo：purpose=`agent_response`，marker=`PENDING-A-0713` | S0；tools 必须含 `todo_add` | tool_call `todo_add`，arguments 精确 title/marker、`completed=false`；`finish_reason=tool_calls` | S1 result 后文本 stop；DONE |
| artifact：purpose=`agent_response`，marker=`ARTIFACT-A-0713` | S0；tools 必须含计划选择的真实 artifact tool；fixture manifest 固定其 name/schema（若为 `file_write`，path 限定隔离 workspace） | 单个 artifact tool_call，参数含 marker/Markdown；`finish_reason=tool_calls` | S1 result/receipt 后文本 stop；DONE |

所有成功 response 的 usage 固定算法：`prompt_tokens=ceil(canonical_request_body_utf8_bytes/4)`，`completion_tokens=ceil(canonical_response_choice_utf8_bytes/4)`，`total_tokens=两者之和`；stream 只在最后一个带 usage 的 chunk报告一次。每个 response event 保存输入/输出 canonical bytes/hash 与上述算式，测试按字节重算。scenario 匹配优先级固定为 **四类只读族（raw/page-in/task+restart/entry+fault）→ force-finish/fallback/compressor → 特定写 tool(goal/todo/artifact) → 通用 tool → normal**；因此只读 routing marker 即使 prompt/messages 中引用 `CTX-CALL-02`、artifact、todo 或 goal 等**被查询的数据 marker**也绝不触发写 matcher。未匹配、状态越序、tool schema 不存在、routing marker 重复占用、错误 model/purpose 均返回 409 `fixture_scenario_mismatch`，不得用无 marker 的 normal 兜底掩盖测试错误。所有 E2E 用户 prompt 必须带本表之一的唯一顶层 routing marker；同一 prompt 出现两个顶层 routing marker 立即 409，报告在发送前区分 routing marker 与被查询的数据 marker。

arm 规则：control arm 必须携带唯一 marker，初始 `armed=true,trigger_count=0,state=S0/F0`；只允许匹配一个 request_id；每个 data-plane hit 原子递增 trigger_count/转移 state；disconnect/超时不自动清；仅表中 DONE terminal 或显式 `DELETE /__control/...` 清除并记录原因。case 收尾要求所有 arm=false、state=DONE/cleared，且 trigger_count 等于表中预期。

接线固定为 DeskPet → 本地 provider seam，不绕过正常 provider adapter/request builder：测试 config 的 provider `base_url=http://127.0.0.1:18992/v1`、model=`ctx-primary`；relay/upstream 禁用（`DESKPET_E2E_PROVIDER_DIRECT=1`），API key 仅注入进程环境 `DESKPET_CLOUD_API_KEY=ctx-e2e-local-key`，不得写 config/report。若生产接线强制 relay，则 launcher 仅可把 relay upstream 指向 18992 并保留同一 auth/header，必须在日志标明 hop；禁止 seam 再转发外部网络。seam 启动时设置 no-proxy/拒绝非 loopback upstream。

`pause_before_attempt` 事件只能由 provider seam 在 `/v1/chat/completions` 收到并完成 auth/body hash 后、写任何 response bytes 前触发；MCP daemon 无此控制。provider-seam JSONL 必含 `provider_event_index,request_body_hash,stream,auth_ok,model,tool_names,response_id,usage,pause_state`。

故障控制统一由 daemon/provider seam 提供且一次只启一个：`l3_timeout`、`project_rules_io_error`、`snapshot_cas_timeout`、`compactor_model_error`（daemon 的 backend optional-component fault hook）；`provider_fallback`、`force_finish`、`pause_before_attempt`（provider seam）。每个 fault event 必须写 `fault_name,armed_at,triggered_request_id,trigger_count,cleared_at`；case 后 clear 并断言 `trigger_count=1`。

专用压缩模型流程必须先 `POST /catalog {model:"ctx-compact-fixture",window:8192}`，启动/刷新设置后确认 UI catalog 可见，再用 `POST /model-fault` 将**同一已选模型**置 unavailable。不得把未进 catalog 的虚构 id 当“调用失败”。

daemon 日志固定写 `<result>/logs/fixture.jsonl`，provider seam 写 `<result>/logs/provider-seam.jsonl`。fixture 原始事件字段至少为 `ts_ns,event_index,event,fixture_epoch,session_id,request_id,attempt_id,tool,spec_version,schema_hash,invocation_id,marker,sequence`，其中 proxy/MCP 协议没有传递的 `session_id/request_id/attempt_id` **允许且必须写 null**，不得伪造 host ids。proxy 在实际 handler 入口生成全局唯一 `invocation_id`（UUID），把它同时写 daemon 原始事件并放入 MCP result；DeskPet host 收到 result 后把同一 invocation_id 写 tool_result/receipt event。control API/脚本只制造 allow-list、断连、暂停、模型/组件故障，不得调用 DeskPet 对话 API、工具 handler 或写 SessionDB。

fixture prerequisite 自测命令：

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest F:\projects\deskpet\backend\tests\test_context_os_e2e_fixture.py -q
```

预期：单 daemon/单 child、共享 epoch/counters、host visibility hook、describe barrier、disconnect EOF/reconnect、hot-replace epoch/schema hash、provider pause 独立性、catalog-before-fault、六类 fault、handler JSONL 与 reset 幂等全部通过。

### 0.3 request/attempt 日志切片与 oracle

1. 每次发送前记 `start_offset=<tauri-dev.log 当前字节数>`；UI 出现用户 bubble 后，从新增日志中找到该 session 最新 `context_attempt planned`，取得 `request_id`。
2. 仅保留满足 `request_id=<精确值>` 的结构化 host 行；再补入带同一 `attempt_id` 的 provider/usage 行。fixture handler 关联算法固定为：从 DeskPet **tool_result/receipt** 行取非空 `invocation_id`（dispatch 前尚不能知道 proxy 将生成的 id），再精确 join `fixture.jsonl.invocation_id`；禁止用 marker、tool name 或时间近似关联。extractor 生成派生 `fixture_enriched` 行，把 join 到的 host `session_id/request_id/attempt_id/tool_call_id/receipt_id` 加到新字段 `host_*`，并保留 `raw_session_id/raw_request_id/raw_attempt_id=null` 与原始行 hash；绝不改写 fixture.jsonl。没有 invocation_id 的非工具 attempt 不拼 fixture 行；工具 result 缺 id或匹配 0/2+ 原始行即 FAIL。
3. 三个源先各自保留原始单调序号：Tauri/backend=`event_index`，provider seam=`provider_event_index`，fixture=`event_index`。合并排序 key 固定为 `(attempt_ordinal, causal_rank, source_seq, source_tiebreak)`：`causal_rank` 为 planned=10、provider_received=20、sent=30、tool_dispatch=40、fixture_handler=50、tool_result=60、usage=70、terminal=80；同 rank 的 `source_tiebreak` 固定 backend=0、provider=1、fixture=2。跨源 wall-clock 只展示，不参与排序。若因果边缺失或一个 invocation_id 匹配 0/2+ fixture 行，切片 FAIL，不自行猜顺序。
4. 输出 `logs/requests/<CASE>-<request_id>.jsonl`，首行写 join metadata（source path/hash/start/end offsets、排序版本 `ctx-evidence-v1`）；原日志只读保留。
5. 每个 attempt 的必填字段：`purpose,request_id,attempt_id,session_id,provider,model,status(planned|sent|terminal),message_hash,tool_payload_hash,logical_schema_fingerprint,adapter_id,adapter_version,wire_payload_hash,schema_tokens,message_tokens,attachment_tokens,summary_tokens,window_tokens,output_reserve,reasoning_reserve,cache_fingerprint,loaded_reasons,trimmed_reasons,compression_cycle_id,authoritative_input_tokens,authoritative_output_tokens`。不适用字段必须为 `null`，不可缺键。
6. 工具 case 额外要求 `capability_scope_id,capability_scope_revision,snapshot_db_revision,registry_revision,policy_fingerprint,tool_name,tool_schema_hash,invocation_id,receipt_id`；scope revision 与 DB revision 必须字段名和值域分离。
7. PASS oracle：每个 planned 最多一个 sent、每个 sent 恰有一个 terminal；fallback/force_finish/compact retry 使用新 attempt_id；authoritative usage 只回填对应 id；UI ContextTrace 的 hash/token/reason 与切片一致。

### 0.4 自动化 assertion 证据格式

每个 AT 输出两个文件：`automation/<AT>.txt`（完整 stdout/stderr）与 `automation/<AT>.json`。JSON 必须含 `case,head,command,started_at,ended_at,exit_code,tests_passed,tests_failed,assertions:[{id,ac,oracle,actual,evidence}]`。测试通过数不能代替 assertion；下文追踪矩阵中的每条 AC 至少有一个 `assertions[].id`。

evidence runner 的唯一入口：

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe F:\projects\deskpet\scripts\e2e\run_context_os_evidence.py --manifest F:\projects\deskpet\scripts\e2e\fixtures\context-os-automation-manifest.json --out F:\projects\deskpet\plans\2026-07-13-context-os-v1\test-results\automation
```

manifest 固定 nodeid 映射（允许实现时在同名文件增加测试，但不得把这些 id 改成宽泛目录）：

| AT | 必跑 nodeid / command |
|---|---|
| 01 | `test_context_fragments_compat.py::test_prefix_lifecycle_order_and_no_duplicate`; `::test_stable_prefix_bytes_ignore_dynamic_fields`; `::test_l3_failure_preserves_complete_l2_tool_group` |
| 02 | `test_task_context_projector.py::test_authority_precedence_and_idle_no_snapshot`; `test_context_snapshot_store.py::test_create_cas_cancel_and_idempotent_flush`; `test_context_precompact_flush.py::test_flush_failure_prunes_then_blocks_without_global_memory_write` |
| 03 | `test_context_precompact_flush.py::test_single_compressor_owner_range_once_and_off_fallback`; `::test_tool_groups_and_receipts_are_not_promoted` |
| 04 | `test_context_request_planner.py::test_whole_request_budget_and_chain_attempt_recalculation`; `::test_prepared_payload_hash_parity_across_budget_report_and_transport` |
| 05 | `test_session_history_planner.py::test_full_raw_fit_ignores_l2_top_k`; `test_context_segment_store.py::test_coverage_gap_overlap_and_stale_fail_closed`; `test_session_history_page_in.py::test_page_in_is_strictly_session_scoped`; `test_project_rules_component.py::test_rules_only_load_for_matching_workspace_path`; `test_context_os_e2e_fixture.py::test_readonly_raw_and_pagein_scenarios` |
| 06 | `test_compression_model_resolver.py::test_follow_session_and_dedicated_failure_no_fallback`; `test_config.py::test_context_os_default_on_and_single_off_switch`; `test_context_entry_matrix.py::test_all_entries_share_contract_and_safe_fail`; `test_context_os_e2e_fixture.py::test_readonly_entry_and_fault_scenarios_preserve_purpose` |
| 07 | `test_tool_capability_resolver.py::test_resolver_once_and_scope_filtered_deferred`; `test_prepared_tool_set_contract.py::test_immutable_revision_and_wire_hash`; `test_tool_capability_hydration.py::test_search_describe_activate_target_and_fail_closed_edges`; `test_context_os_e2e_fixture.py::test_registration_metadata_and_pre_activate_catalog_stale` |
| 08 | `test_tool_capability_execution_guard.py::test_policy_scope_stale_and_missing_context_fail_closed`; `::test_snapshot_cas_cancel_and_durable_effect_contracts`; `test_context_os_e2e_fixture.py::test_pre_provider_catalog_stale_refreshes_registry_revision`; `::test_provider_scenario_state_machine_all_paths`; `::test_readonly_task_and_restart_scenarios_have_no_writes` |
| 09 | `tauri-app/src/components/ContextTracePanel.context-os.test.tsx`; `tauri-app/src/components/ModelContextCard.test.tsx`; bundled-node `tsc -b tauri-app` |
| 10 | full backend pytest；full Vitest；bundled-node typecheck；`context_os_bench.py` command from §2 |

上述 nodeid 若尚不存在，是实现阶段 prerequisite；runner 必须先用 `pytest --collect-only -q <nodeid>` 验证恰好 collection=1，再运行。

## 1. 共用前置与测试数据

### 1.1 自动化前置

1. 在仓库根目录 `F:\projects\deskpet` 执行，使用 `backend\.venv\Scripts\python.exe`。
   - 预期：解释器存在，数据库 fixture 使用临时目录，不读取真实用户库。
2. 保存 `git status --short` 与 HEAD 到自动化结果目录。
   - 预期：测试前基线可追溯；测试不得清理或覆盖既有 dirty changes。
3. 前端使用项目可用的 bundled Node/npm；记录绝对路径。
   - 预期：Vitest 与 TypeScript 命令可复跑。

### 1.2 Windows Computer Use 前置

1. 创建结果根，例如 `F:\projects\deskpet\plans\2026-07-13-context-os-v1\manual-results-2026-07-13`，其下建立 `userdata/logs/screenshots/logs/requests`。读取 gitignored 的 `F:\projects\deskpet\LOCAL-DEV-CREDENTIALS.md`，但不得复制到报告或日志。
   - 预期：测试数据与日常数据隔离；凭据只经 onboarding UI 输入，输入前截图、输入期间不截图、登录完成后再截图。
2. 先确认 8300/5373/18991 无监听者，再运行 fixture 和唯一 launcher：

```powershell
$R='F:\projects\deskpet\plans\2026-07-13-context-os-v1\manual-results-2026-07-13'
F:\projects\deskpet\backend\.venv\Scripts\python.exe F:\projects\deskpet\scripts\e2e\context_os_fixture_ctl.py start --port 18991 --log "$R\logs\fixture.jsonl"
F:\projects\deskpet\backend\.venv\Scripts\python.exe F:\projects\deskpet\scripts\e2e\context_os_provider_seam.py start --port 18992 --log "$R\logs\provider-seam.jsonl"
powershell -ExecutionPolicy Bypass -File F:\projects\deskpet\scripts\e2e\launch_context_os.ps1 -BackendPort 8300 -VitePort 5373 -UserDataDir "$R\userdata" -Python "F:\projects\deskpet\backend\.venv\Scripts\python.exe" -BackendDir "F:\projects\deskpet\backend" -Config "F:\projects\deskpet\config.toml" -Log "$R\logs\tauri-dev.log"
```

   - 预期：launcher 设置 `DESKPET_BACKEND_PORT=8300`、`DESKPET_VITE_PORT=5373`、`DESKPET_USER_DATA_DIR`、`DESKPET_DEV_MODE=1`、`DESKPET_PYTHON`、`DESKPET_BACKEND_DIR`、`DESKPET_CONFIG`，以及 §0.2 表中的 `DESKPET_CONTEXT_OS_E2E_*`、`DESKPET_E2E_PROVIDER_DIRECT=1`、进程级 `DESKPET_CLOUD_API_KEY=ctx-e2e-local-key`；测试 MCP config、visibility hook URL、provider base URL 均写入 Tauri 继承的 env。在 `tauri-app` 运行 `npx tauri dev --config <临时 devUrl=5373 JSON>`；不手动起 backend/Vite，不访问外部 provider。
   - 启动 oracle：`logs/tauri-dev.log` 出现 `Dev python=F:\projects\deskpet\backend\.venv\Scripts\python.exe backend_dir=F:\projects\deskpet\backend`、backend 8300、Vite 5373、migration version 与 Context OS owner。出现 `Bundled exe=`、10048 或第二个 Vite 即 FAIL。
3. 首次启动若出现 onboarding：先保存无凭据的 before；声明账号框坐标并点击，粘贴账号；声明密码框坐标并点击，粘贴密码（这两步不截图 after）；声明登录按钮坐标并点击，等待最长 60s；只在登录窗关闭、主界面出现后保存 after。
   - 预期：进入主界面；报告只记“DEV login success”，不得保存账号、密码、token、keychain 列表或含秘密日志片段。
4. 默认配置保持 `context_os_v1=true`、压缩模型 `follow_session`。8K case 完整关闭此实例后，以同命令把 `-Config` 换为 `F:\projects\deskpet\scripts\e2e\fixtures\context-os-8k.toml`、端口换 8301/5374、userdata 换 `userdata-8k`；不得改产品默认窗口。
   - 预期：启动日志各自记录 active owner、resolved window、migration version，无正文。
5. 准备本地 MCP fixture：仅 Session A 授权，至少 500 个 deferred descriptors；目标工具 `mcp_ctx_fixture_tool_001` 写入带 invocation id 的 handler receipt；支持暂停、断开、热替换 spec/version。
   - 预期：fixture 有独立的 register/describe/activate/handler 计数器，能证明未调用、调用一次与重复调用。
6. 打开应用主窗口、设置页、ContextTrace 入口各一次并截图；记录输入框、发送键、会话新建/切换、设置、ContextTrace 控件的实际中心坐标。
   - 预期：所有后续动作使用本轮截图测得坐标，不套用历史固定坐标。

## 2. 自动化回归用例

每个 case 按“执行命令 → 检查退出码 → 检查关键断言 → 保存输出”执行；预期均为退出码 0 且关键断言成立。

固定命令（不得用文件 glob 悄悄跳过不存在的测试；缺文件即 prerequisite FAIL）：

```powershell
$PY='F:\projects\deskpet\backend\.venv\Scripts\python.exe'
& $PY -m pytest backend/tests/test_context_fragments_compat.py backend/tests/test_task_context_projector.py backend/tests/test_context_snapshot_store.py backend/tests/test_context_segment_store.py backend/tests/test_session_history_planner.py backend/tests/test_session_history_page_in.py backend/tests/test_compression_model_resolver.py backend/tests/test_context_request_planner.py backend/tests/test_context_precompact_flush.py backend/tests/test_tool_capability_resolver.py backend/tests/test_prepared_tool_set_contract.py backend/tests/test_tool_capability_hydration.py backend/tests/test_tool_capability_execution_guard.py backend/tests/test_deskpet_tools_registry.py backend/tests/test_prompt_cache.py -q
& $PY -m pytest backend/tests -q
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' tauri-app/node_modules/vitest/vitest.mjs run --root tauri-app
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' tauri-app/node_modules/typescript/bin/tsc -b tauri-app
& $PY scripts/perf/context_os_bench.py --mode on --compare plans/2026-07-13-context-os-v1/context-perf-baseline.json
```

每个 AT 的细分测试若未单列文件，使用 `-k '<assertion id or behavior>'` 从上述文件运行并把实际 collection 数写入证据；0 collected 为 FAIL。

### AT-CTX-01 生命周期、稳定 prefix 与 L1/L2/L3（AC-CTX-1/2/3）

1. 运行 `test_context_fragments_compat.py`、assembler/component、provider cache/adapter 测试。
   - 预期：每个 prefix fragment 均有六项生命周期元数据；顺序固定；conversation/control event 未进入 prefix；跨层无重复。
2. 以同 persona/rules/L1、不同 session/time/L3/model runtime 生成连续两请求。
   - 预期：stable bytes/fingerprint 相同；支持 cache control 的 adapter 只在稳定边界打 breakpoint，不支持者 payload 仍合法。
3. 构造含 `assistant.tool_calls` 和多个对应 `tool_call_id` result 的历史，并令 L3 timeout/empty/error。
   - 预期：工具组不可拆，newest-tail 完整；L3 失败不删除 L2。

### AT-CTX-02 Task projection 与 snapshot CAS（AC-CTX-4/5/10/19）

1. 运行 `test_task_context_projector.py`、`test_context_snapshot_store.py`、`test_context_precompact_flush.py`。
   - 预期：Goal/Workflow/Receipt/Artifact 权威字段胜过 derived 摘要；闲聊不创建空 snapshot。
2. 分别测试 create、revision=0 CAS、并发 stale revision、取消发生在 commit 前/后、重复 flush。
   - 预期：仅一个 revision 成功；capability scope revision 与 DB row revision 类型/字段不可互换；已提交但未激活/未发送记录标 diagnostic/prepared；重复 flush 不重复追加事实。
3. 注入 DB timeout/写回失败并使请求接近预算。
   - 预期：只做可逆 pruning，保留 current request/protected task/recent complete tool group；仍超限返回可恢复 budget error；不写全局 `MEMORY.md`。

### AT-CTX-03 单 owner 压缩与工具组治理（AC-CTX-6/7）

1. 运行 compressor/context manager/precompact/tool-group/receipt 测试，spy 所有摘要调用。
   - 预期：ON 路径只有 `ContextCompressor`；一个 cycle 可有多个不重叠 segment，但 source range 只提交一次且 owner/model/cycle id 相同。
2. 同一输入重复触发 compact，并模拟一半 segment 成功后重试。
   - 预期：已提交 range 不重复写；失败 range 可重试；accepted/pending receipt 不会变成 completed。
3. 设置 `context_os_v1=false`。
   - 预期：reactive owner 关闭，legacy preflight 可用且两者不双跑。

### AT-CTX-04 全请求预算与 provider attempt 重算（AC-CTX-8/11/17）

1. 运行 `test_context_request_planner.py`、预算、attempt store、provider adapter/payload hash 测试。
   - 预期：system/fragments、L2/current user、真实 schemas 或 `tools=None`、附件、tool groups、summary、output/reasoning reserve 全部计入；首轮 schemas 不漏算。
2. 构造两 provider/model 不同窗口的 fallback chain。
   - 预期：公共 pruning 以最小 effective input budget 为线；每个真实 attempt 重算 window/reserve，compact/retry/fallback 不复用旧 usage。
3. 核对 planner、budget、snapshot、report、request builder 的 `PreparedToolPayload`。
   - 预期：逻辑 schema fingerprint、adapter id/version、wire hash/tokens 对账；不支持 schema 只淘汰该 attempt。

### AT-CTX-05 全 raw、coverage tree 与 page-in（AC-CTX-9/10/16）

1. 运行 `test_context_segment_store.py`、`test_session_history_planner.py`、`test_session_history_page_in.py`、project-rules 测试。
   - 预期：能放下时装载所有 eligible raw rows，不受 `l2_top_k` 限制；旧 derived summary 不冒充 raw。
2. 构造超窗连续 message-id 范围、工具组边界、stale hash、summary failure、gap 与 overlap。
   - 预期：合法树 `gap=0/overlap=0`；非法树 BLOCK/保留 raw，不静默丢弃；reference 不计内容 coverage。
3. 用 Session A 的 reference 在 A/B、合法/篡改 range 上 page-in。
   - 预期：仅 A 的合法连续范围成功；跨 session、stale hash、越界 fail closed；返回精准原始行。
4. workspace/code scope 命中与不命中项目路径。
   - 预期：只在命中时加载规则，compact 后再次命中可 remount；普通聊天不加载路径规则。

### AT-CTX-06 多入口、safe-fail、默认 ON/OFF（AC-CTX-12/13/15）

1. 对 text/code/web/research/voice 入口运行 contract tests。
   - 预期：共享 fragment/lifetime、预算、attempt report；L3/snapshot/rules/compactor 单点失败仍保留当前用户原文与最近连续尾部，超限明确 BLOCK。
2. 运行配置/IPC/设置前端测试，覆盖缺省、`follow_session`、专用模型、非法模型、调用失败。
   - 预期：默认 follow_session；requested/resolved/actual 可对账；专用模型失败不静默切主模型、不提交新有损 summary。
3. 验证出厂 `context_os_v1=true`，再在隔离进程置 false 并跑 OFF golden。
   - 预期：只有一个总开关；OFF 八类 task 工具 names/order 与 baseline 完全一致，读取 legacy `tools`，无双写/双压缩。

### AT-CTX-07 单一工具集合与渐进激活（AC-CTX-17/18）

1. 运行 `test_tool_capability_resolver.py`、`test_prepared_tool_set_contract.py`、`test_tool_capability_hydration.py`。
   - 预期：每 request resolver 只调用一次；draft finalize 不二读 registry；PreparedToolSet immutable/versioned；main 不按名字重建 schema。
2. 以 500 deferred specs 运行 scripted loop：search→describe→activate→target。
   - 预期：初始仅 direct+三 bridge；search 只见 scope 内 compact descriptors；describe schema/hash 精确；激活仅生成一个后继 revision，预算超限不提交。
3. 覆盖未 describe nonce、过期 nonce、跨 session nonce、policy/spec/version 改变、disabled/env-hidden/mode-denied。
   - 预期：全部 fail closed，隐藏 inventory 不可枚举。

### AT-CTX-08 执行守卫、stale 与既有闭环（AC-CTX-19）

1. 运行 `test_tool_capability_execution_guard.py`、registry、durable execute_prepared、artifact/receipt/verify/timeout/breaker/force_finish 测试。
   - 预期：Registry 在 PermissionGate/handler 前复核 request scope、session eligibility 和 strict policy；缺上下文、legacy dispatch、policy unavailable 均 fail closed。
2. 在 prepare 后 unregister/热替换工具或改变 policy/session eligibility，再执行。
   - 预期：返回 stale/denied，旧新 handler 计数均为 0；`force_finish` 仍为 `tools=None`。
3. 对 active task 重复 prepare/activate/attempt CAS，对闲聊执行相同请求。
   - 预期：task snapshot 仅保存工具名/hash/reason/tokens/adapter 等非敏感摘要且 revision 单调；闲聊不创建空 row；Artifact/Receipt/VerifyGate 不回归。

### AT-CTX-09 前端 ContextTrace 与隐私（AC-CTX-11/15/16/17/19）

1. 运行 ContextTrace、ModelContextCard、IPC Vitest 与 TypeScript typecheck。
   - 预期：显示 loaded/trimmed reason、层级 tokens、window/reserve、cache/schema fingerprint、coverage gap/overlap、requested/resolved/actual model、scope/DB revision；辅助调用与 agent response 分栏。
2. fixture 注入 token、文件正文、完整工具参数。
   - 预期：UI 仅显示 redacted preview/hash，不显示秘密与正文。
3. 先收到 planned，后收到 sent/terminal；重放同 event 并乱序送达。
   - 预期：状态只合法前进、attempt 不重复、authoritative usage 绑定原 id。

### AT-CTX-10 固定矩阵、全量回归与性能（AC-CTX-1～19）

1. 运行 plan Task 5.2 列出的 focused pytest 矩阵。
   - 预期：全部 green；每项结果映射回本文件 AC 表。
2. 运行 `backend\.venv\Scripts\python.exe -m pytest backend/tests -q`、前端 Vitest 与 typecheck。
   - 预期：无本变更引入失败；既知环境 redline 单独列原始证据。
3. 运行 `scripts/perf/context_os_bench.py --mode on --compare plans/2026-07-13-context-os-v1/context-perf-baseline.json`，并启用 500 deferred specs。
   - 预期：short-chat assembly P95 增量 ≤20ms，无辅助 LLM call；初始 schema bytes 比 legacy 全量至少降 80%；无 deferred 命中不增加 provider iteration；激活 bytes 与 report attribution 一致。

## 3. Windows Computer Use 真 E2E

### E2E-CTX-01 默认大窗口全 raw 与普通承接（AC-CTX-1/2/3/9/11/16）

**前置**：按 §1.2 以默认大窗口、`follow_session` 启动；fixture 均不注入故障。以下每次新建、切换、展开、输入、发送都先按 §0 记录当次真实坐标。

1. `坐标=(x_new_session,y_new_session)|动作=click|期望=建立 Session RAW-A`；截图。
   - 预期：新会话可输入，ContextTrace 尚无旧会话内容。
2. 连续 12 轮在输入框粘贴唯一事实 `RAW-01`～`RAW-12`，每轮发送前记录输入框坐标并在回复后截图。
   - 预期：不触发无关工具；普通上下文承接自然。
3. `坐标=(x_trace,y_trace)|动作=click|期望=打开本会话 ContextTrace`。
   - 预期：所有 eligible 未删除 messages 为 raw coverage，`gap=0/overlap=0`，无 page-in/compact，stable prefix fingerprint 在未改稳定内容的相邻轮一致。
4. 粘贴`CTX-RAW-RECALL：逐条复述 RAW-01、RAW-06、RAW-12 的值并说明是否调用历史工具`。
   - 预期：三项准确，不调用 memory/page-in；日志中实际 messages/schema 与 trace hash 对账。

### E2E-CTX-02 8K、20+ 工具调用与有界 compact（AC-CTX-5/6/7/8/10/14/16）

**前置**：关闭默认窗口实例后按 §1.2 启动隔离 8K 实例；使用新的 userdata/session，确保无旧 summary。fixture allow-list 加入 COMPACT-A，`tool_001` 在线且 counters=0。

1. 以隔离 8K fixture 启动并确认日志 resolved window=8K；新建 Session COMPACT-A。
   - 预期：仅 `ContextCompressor` active owner。
2. 在真实 UI 依次发送下表 21 个 prompt。每行固定拆成：输入框 before→输入框坐标 click→after；before→Ctrl+V prompt→after；before→Enter→最长 180s 等待含 marker 的 assistant/tool 卡→after；随后切片 request_id。不得一次粘贴多行批量触发。

| # | marker | 必须原样粘贴的 prompt | 预期工具/结果 |
|---|---|---|---|
| 01 | CTX-CALL-01 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-01", sequence=1；不要自行猜测结果。` | tool_001；回显 marker/1 |
| 02 | CTX-CALL-02 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-02", sequence=2；不要自行猜测结果。` | tool_001；回显 marker/2 |
| 03 | CTX-CALL-03 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-03", sequence=3；不要自行猜测结果。` | tool_001；回显 marker/3 |
| 04 | CTX-CALL-04 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-04", sequence=4；不要自行猜测结果。` | tool_001；回显 marker/4 |
| 05 | CTX-CALL-05 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-05", sequence=5；不要自行猜测结果。` | tool_001；回显 marker/5 |
| 06 | CTX-CALL-06 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-06", sequence=6；不要自行猜测结果。` | tool_001；回显 marker/6 |
| 07 | CTX-CALL-07 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-07", sequence=7；不要自行猜测结果。` | tool_001；回显 marker/7 |
| 08 | CTX-CALL-08 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-08", sequence=8；不要自行猜测结果。` | tool_001；回显 marker/8 |
| 09 | CTX-CALL-09 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-09", sequence=9；不要自行猜测结果。` | tool_001；回显 marker/9 |
| 10 | CTX-CALL-10 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-10", sequence=10；不要自行猜测结果。` | tool_001；回显 marker/10 |
| 11 | CTX-CALL-11 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-11", sequence=11；不要自行猜测结果。` | tool_001；回显 marker/11 |
| 12 | CTX-CALL-12 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-12", sequence=12；不要自行猜测结果。` | tool_001；回显 marker/12 |
| 13 | CTX-CALL-13 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-13", sequence=13；不要自行猜测结果。` | tool_001；回显 marker/13 |
| 14 | CTX-CALL-14 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-14", sequence=14；不要自行猜测结果。` | tool_001；回显 marker/14 |
| 15 | CTX-CALL-15 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-15", sequence=15；不要自行猜测结果。` | tool_001；回显 marker/15 |
| 16 | CTX-CALL-16 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-16", sequence=16；不要自行猜测结果。` | tool_001；回显 marker/16 |
| 17 | CTX-CALL-17 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-17", sequence=17；不要自行猜测结果。` | tool_001；回显 marker/17 |
| 18 | CTX-CALL-18 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-18", sequence=18；不要自行猜测结果。` | tool_001；回显 marker/18 |
| 19 | CTX-CALL-19 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-19", sequence=19；不要自行猜测结果。` | tool_001；回显 marker/19 |
| 20 | CTX-CALL-20 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-20", sequence=20；不要自行猜测结果。` | tool_001；回显 marker/20 |
| 21 | CTX-CALL-21 | `必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-CALL-21", sequence=21；不要自行猜测结果。` | tool_001；回显 marker/21 |

   - 预期：fixture counters 精确 `handler=21`，21 个唯一 invocation_id；每个 request 的 assistant tool call 与 tool result `tool_call_id` 配对且 sequence/marker/hash 正确；receipt 状态真实，未接受项不变 completed。任一 prompt 未触发指定工具即该行 FAIL，不用其他工具补数。
3. 使用 payload generator 构造 deterministic compact rounds：每轮 `context_os_payload.py compact --round N --bytes 6144 --out <result>\payloads\compact-N.txt` 生成 `CTX-COMPACT-RNN-BEGIN` + 重复的 64-byte ASCII chunk + `CTX-COMPACT-RNN-END sha256=<hash>`，精确 UTF-8 长度 6144 bytes。执行者只通过 UI 输入框 Ctrl+V 该文件内容并 Enter，每轮按五段模板等待最长 300s。
   - stop 算法：N 从 1 到 12；每轮结束切片 request。记录开始本步骤前已有的 cycle ids；若首次出现新的 `compression_cycle_id!=null` 且同 request terminal，则记 `trigger_round=N` 并立即停止添加 payload；若 N=12 仍无新 cycle 则 FAIL（不继续无限灌入）。
   - 预期：至少一个 cycle；每个 cycle 恰有一个 owner，cycle 的触发 request 只包含 round N 一次，hash 与 generator manifest 相同；可有多个 segment/cycle，但 source ranges 全局不重叠且每个 range 只提交一次；snapshot flush committed event 的 causal rank 先于对应首个 lossy-summary event。8K 下完整保留 21 个工具因果组可能需要重复有界压缩，不把“强行只压一次”作为正确性条件。
4. 打开 ContextTrace。
   - 预期：raw+summary 内容 coverage 为 `gap=0/overlap=0`；reference 不冒充 coverage；recent complete tool group 保留。
5. 追问`CTX-PAGEIN-RECALL：请从当前 session 精确 page-in 并返回 CTX-CALL-02 的 marker、sequence 和结果；这是只读查询`。
   - 预期：如需细节真实调用 `session_history_page_in`；答案与 compact 前一致，不调用无关工具。

### E2E-CTX-03 MCP 渐进披露、session/goal 隔离（AC-CTX-17/18/19）

**前置**：默认窗口实例、500-spec fixture 在线且计数器清零；Session A/B 均为本轮新建。fixture 控制窗与 DeskPet 窗的每次点击分别记录坐标。

1. 启动 500-spec MCP fixture，仅授权 Session A；在 A 设置 goal，再新建无 goal 的 Session B。
   - 预期：fixture register 成功；B 的 trace/payload 无 `goal_task_*`。
2. 切回 A，粘贴`必须调用 mcp_ctx_fixture_tool_001，参数 marker="CTX-DISCLOSE-A", sequence=301；不要自行猜测结果`并发送。
   - 预期：真实顺序为 `tool_search → tool_describe → tool_activate → mcp_ctx_fixture_tool_001`；初始 payload 只有 direct+bridge，激活后 schema hash 只变化一次。
3. 对账 scope revision、provider schema hash、handler invocation/receipt。
   - 预期：三者 request/session 一致，handler 正好一次，Permission/Receipt 走真实链路。
4. 切到 B 发送相同请求。
   - 预期：B 搜不到/不能激活目标能力，A 的 nonce/revision 不可复用，handler 计数不增加。

### E2E-CTX-04 describe 后断连（AC-CTX-18/19）

**前置**：MCP fixture 开启 `pause_after_describe`，目标 handler 计数为 0；使用全新 Session C，禁止复用 E2E-03 nonce。

1. 让 fixture 在 describe 完成后暂停；从 UI 发送目标任务，看到 describe event 后截图。
   - 预期：尚无 activate/handler receipt。
2. 在单独 PowerShell 中先运行 `context_os_fixture_ctl.py disconnect`（child 收到 EOF、epoch+1），再运行 `context_os_fixture_ctl.py resume-describe` 只释放 daemon barrier；**不得**在本 request reconnect。这是故障注入控制，不替代 DeskPet 内已由 UI 发起的请求。
   - 预期：pre-activate `/catalog-meta` 发现 registered fixture_epoch/spec hash 不一致，registry revision 精确 +1；旧 child 已断，activate 返回可恢复 `tool_catalog_stale`，目标 handler 未执行、无伪成功 receipt；resume-describe 不重生 transport。
3. terminal 后运行 `context_os_fixture_ctl.py reconnect`，确认新 child/新 epoch；下一 request 再从 UI 重试。
   - 预期：重新 resolve/describe/activate 后成功，旧 nonce 不复活，handler 仅新增一次。

### E2E-CTX-05 已暴露工具在 attempt 前失效（AC-CTX-17/19）

**前置**：fixture 在线；独立 provider seam 开启 `pause_before_attempt`；新 Session D 已获目标能力但尚未执行第二个请求。MCP daemon 不承担此 pause。

1. 先在 UI 完成一次 activate，让目标成为后继 PreparedToolSet direct schema；暂停下一 provider attempt。
   - 预期：trace 记录当前 immutable revision/schema hash。
2. provider seam 记录 `attempt_paused` 后，在单独 PowerShell 运行 `context_os_fixture_ctl.py hot-replace --tool mcp_ctx_fixture_tool_001 --version 2`，再运行 `context_os_provider_seam.py resume-attempt`；这是故障注入控制，不替代 DeskPet UI 请求。
   - 预期：pre-provider `/catalog-meta` 对比记录 old/new epoch/spec_hash，registry revision 精确 +1；旧 schema 未出站，run 返回 `tool_catalog_stale`；旧/新 handler 都未被旁路调用。
3. 新 request 再次发起。
   - 预期：新 revision resolve 后可恢复，旧 capability ref 不再有效。

### E2E-CTX-06 压缩模型设置与失败不偷换（AC-CTX-6/11/15）

**前置**：8K 实例。先通过 provider seam `catalog --model ctx-compact-fixture --window 8192`，刷新设置并用 UI 确认它可选择；此时模型可正常调用。新 Session MODEL-A 无旧 coverage。

1. `坐标=(x_settings,y_settings)|动作=click|期望=打开设置`；再点击“上下文/压缩模型”下拉并选专用模型，保存后重进设置。
   - 预期：选择持久化；重进不重复写/不重置；默认选项仍可选 `跟随当前会话模型`。
2. 在 8K 会话通过真实输入触发 compact。
   - 预期：UI/report/log 的 requested/resolved/actual provider/model 完全一致。
3. 保持 UI 已选的 `ctx-compact-fixture` 不变；通过 provider seam 执行 `model-fault --model ctx-compact-fixture --error dedicated_model_unavailable`，再按 deterministic rounds 重复触发 compact。
   - 预期：不静默换主模型、不提交新有损 summary；保留 raw/既有 coverage并显示诊断，仍超限时返回可恢复 budget error。

### E2E-CTX-07 Goal/workflow、任务切换与回切（AC-CTX-4/5/10/14/19）

**前置**：8K 实例；Goal/Workflow/Receipt/Artifact 真实链路可用；TASK-A/TASK-B 均为本轮新会话。

1. 新建 TASK-A，按五段模板依次发送：①`/goal 为 Context OS E2E 创建一份结果清单，目标标记 GOAL-A-0713`；②`决定：清单格式使用 Markdown，决策标记 DECISION-A-0713`；③`创建一个待办：核对重启恢复，标记 PENDING-A-0713；现在不要完成它`；④`创建 Markdown 产物，正文为 ARTIFACT-A-0713，并告诉我可点击的产物位置`。每条最长等待 180s 并切片 request。
   - 预期：真实 goal/task/receipt/artifact UI 卡出现；objective/decision/pending/artifact 四个 marker 可见，pending 明确未完成，记录 artifact identity/path。
2. 继续发送足量长文本触发 compact；等待最长 300s 后打开 ContextTrace。
   - 预期：compact 前后四类 marker 与 artifact identity 一致，active snapshot 有 revision/CAS receipt；权威 store 状态没有被 derived 摘要覆盖。
3. 点击新建 Session TASK-B，输入`新任务 B：只讨论旅行打包，标记 GOAL-B-0713；当前目标和下一步是什么？`。
   - 预期：只回答 B，不漂回 A；B payload 无 A 的 goal tools/snapshot。
4. 点击会话列表切回 TASK-A，追问`CTX-TASK-READBACK：请逐字给出 GOAL-A-0713、DECISION-A-0713、PENDING-A-0713 和 ARTIFACT-A-0713 的路径；只读，不创建或完成任何项目`。
   - 预期：从 snapshot/权威事实源恢复 A，字段准确；不会把 pending 提升 completed。

### E2E-CTX-08 应用重启恢复（AC-CTX-5/10/14/19）

**前置**：承接已通过 E2E-07 的 TASK-A，保留其隔离 userdata；记录重启前 snapshot DB revision（仅由 ContextTrace/UI 展示取证，不直接查库）。

1. 复用 E2E-07 的 TASK-A；点击 task/receipt/artifact 卡确认 `PENDING-A-0713` 未完成、receipt 为 accepted、artifact 可打开。逐动作按五段模板记录；然后通过窗口关闭按钮正常关闭 Tauri。
   - 预期：日志 flush/DB commit 结束，无 orphan deskpet/Vite。
2. 仅按共用启动纪律重启 Tauri，点击 TASK-A。
   - 预期：仍是 Dev python 当前代码；pending 未变 completed，accepted receipt 状态不伪造，artifact 可定位。
3. 粘贴`CTX-RESTART-READBACK：重启恢复检查，逐字给出 GOAL-A-0713、DECISION-A-0713、PENDING-A-0713、ARTIFACT-A-0713 的位置和下一步；只读，不完成 pending`并发送。
   - 预期：三个 marker 与路径准确，不回漂 TASK-B，不调用无关工具；pending 仍未完成，snapshot DB revision 单调不重置。

### E2E-CTX-09 多入口与可选组件降级（AC-CTX-9/12/14）

**前置**：默认窗口实例；text/code/voice 的真实 UI 入口可见；web/research 按架构基线是消息入口内的
父 AgentLoop 工具 scope（不是独立 venue）；故障 fixture 可一次只关闭一个可选组件。

1. 分别从真实 UI 提交唯一只读 marker：text=`CTX-ENTRY-TEXT`、code=`CTX-ENTRY-CODE`、voice=`CTX-ENTRY-VOICE`；
   web=`CTX-ENTRY-WEB` 与 research=`CTX-ENTRY-RESEARCH` 从消息面板以明确的联网快查/深度调研意图进入各自
   父 AgentLoop 工具 scope。prompt 均为`<marker>：只回复 marker 和本入口 purpose，不调用写工具`。每个入口
   记录控件坐标与动作前后截图。
   - 预期：trace 均有相同 fragment/lifetime、预算、attempt report 合同；purpose 保持实际 provider 调用值；
     web/research 不伪造成独立 venue，各入口专属行为仍正常。
2. 逐次运行 `context_os_fixture_ctl.py set-fault --name l3_timeout|project_rules_io_error|snapshot_cas_timeout|compactor_model_error`（一次一个），再从 UI 分别发送唯一 marker `CTX-FAULT-L3|CTX-FAULT-RULES|CTX-FAULT-SNAPSHOT|CTX-FAULT-COMPACTOR` 的只读短请求；每次 terminal 后运行 `clear-fault`。
   - 预期：每个 fault `trigger_count=1`；未超预算时当前原文和最近连续尾部仍出站；失败 reason 可见且无旧内容污染。snapshot/compactor 故障接近预算时按 AC-5/8 返回可恢复 BLOCK，不静默摘要。
3. 在 code workspace 请求命中一个路径，再普通聊天提同路径词。
   - 预期：只有 code/workspace 命中加载项目规则；再次命中可 remount。

### E2E-CTX-10 ContextTrace 真实性、force-finish 与隐私（AC-CTX-2/8/11/17/19）

**前置**：默认窗口实例；fallback 与 force-finish 由独立 provider seam 可控触发；launcher 仅为本 case 注入 direct fixture tool，并固定 AgentLoop `max_iterations=4`：前三轮与 seam `tool-rounds=3` 对齐，第 4 轮为 Context OS 保留的 tools=None 收尾 attempt；不得复用上一 turn 的 activation scope；使用无真实秘密的假敏感字符串。

1. 发送 marker=`CTX-NORMAL-1` 的普通响应、marker=`CTX-CALL-TRACE-1` 的工具响应各一次；再通过 provider seam arm `fallback --from ctx-primary --to ctx-fallback --marker CTX-FALLBACK-1`，由 UI 发送含 `CTX-FALLBACK-1` 的请求；然后 arm `force-finish --marker CTX-FORCE-1 --tool-rounds 3`，由 UI 发送含 `CTX-FORCE-1` 的请求。全部 terminal 后打开 ContextTrace。
   - 预期：fallback 恰为 primary 503→fallback stop；force-finish 先有 3 轮真实 tool_calls/result（fixture handler +3），再有新 attempt 且 request `tools=None`、response stop。每个 attempt 有独立 purpose/request_id/attempt_id、provider/model、messages hash、window/reserve、authoritative usage，arm terminal 后清除。
2. 展开 loaded/trimmed/coverage/tool capability 详情。
   - 预期：显示 reason、token breakdown、cache/schema fingerprint、adapter/wire hash、scope revision 与 DB revision；数值来自实际 payload，不是“工具数×常量”。
3. 使用包含假 token、完整工具参数和本地文件正文的测试输入后再次查看 trace。
   - 预期：仅 redacted preview/hash；截图与日志无秘密正文。

### E2E-CTX-11 默认 ON 与唯一 OFF 回退（AC-CTX-13/14）

**前置**：使用第三份隔离 userdata；已保存校准 HEAD 的八类 task 工具 names/order golden；每次配置切换后完整关闭再仅启动 Tauri。

1. 清除隔离 userdata 的局部 feature override 后启动，通过设置/日志观察。
   - 预期：Context OS V1 默认 ON，只有一个总回退开关，无 shadow/双写/双压缩。
2. 关闭 `context_os_v1`，重启后用 UI 依次发送固定分类 prompt。为便于从 provider 日志做一一对应，每条自然语言 prompt 增加只用于 E2E 识别的 `CTX-OFF-<TASK_TYPE>：` 前缀：`chat=你好，陪我聊一句`、`recall=回忆我们刚才说的标记`、`task=帮我创建一个待办`、`code=读取工作区 README 并概括`、`web_search=搜索今天的测试关键词`、`plan=为两步任务列计划`、`emotion=我有点难过，安慰我`、`command=/help`。夹具仅对这些前缀返回 parser-valid 的确定性分类，业务正文保持不变；每条均切片 request。
   - 预期：每类 task_type 与 `backend/tests/fixtures/context_os_off_golden.json` 对应。2026-07-14 真机校准来自真实 OFF 启动栈，并在 `manual-final-20260714-e2e11-audit-off` 通过 UI 完整重放：registry revision=`51`；chat/command 为 50 个 legacy registry 工具，recall=`[memory_read,memory_search]`，task=`[]`，code=`[file_read,file_write,workspace_recall]`，web_search=`[deepresearch,scrapling_fetch,gold_price_lookup,web_fetch,web_crawl,web_search]`，plan=`[]`，emotion=`[]`。空 task/plan 与缺失的 `file_search`/`todo_add`/`todo_list` 是当前 legacy registry/policy 交集的真实基线，不在 Context OS 回退测试里改写。`*` 的展开列表必须在 golden 中保存为显式数组和 registry revision，不得测试时从 ON direct/discoverable 推导。旧 assembly/preflight 可用，新 reactive path 不运行。
3. 再开启并重启，重复一个 task。
   - 预期：ON 路径恢复；OFF 期间没有反向污染新 snapshot/tool selections。

### E2E-CTX-12 中转站 Realtime 语音、打断与恢复（AC-CTX-12/14，VR-0..2）

**硬前置**：relay 已用真实 key 通过 VR-0 capability gate，公开 voice model alias 和 WebRTC/Realtime
契约；若 `/models` 无语音模型或 Realtime endpoint 为 404，本 case 记为 BLOCKED，不得回退本地模型、
Edge TTS，也不得用预录 transcript/WS 直注冒充真人语音。

1. 冷启动后打开语音入口，记录启动日志与 ContextTrace，再授权麦克风。
   - 预期：日志没有 import/load/download Faster-Whisper、Silero、CosyVoice，没有 Edge TTS 请求；UI 依次显示
     connecting→listening；长期 relay key 未出现在 WebView、截图或日志。
2. 真人连续说 5 轮中文，其中包含同一 session 的早期 marker、当前 task decision 与一个只读工具请求。
   - 预期：每轮恰好一个 user final 和一个 assistant final；partial 只展示不落库；回答承接早期 marker；
     工具调用走 PreparedToolSet/Permission/Registry/Receipt，ContextTrace 的 request/scope/schema hash 可对账。
3. assistant 正在说话时自然插话 3 次，每次说出新的唯一 marker。
   - 预期：服务端 speech_started 触发 cancel；旧音频立即停止，未播放尾部被 truncate；旧 response 的晚到
     audio/delta 被 generation fence 丢弃；新回答基于插话继续，三次各只有一个 cancel terminal。
4. 说话中断网，恢复网络后等待自动重连；再正常关闭并重启应用，继续同一 session。
   - 预期：UI 显式 reconnecting/error/recovered；指数退避无忙循环；只从最后一个已提交 final 边界恢复，
     不重放 partial、不重复 turn；重启后 transcript/task/snapshot 连续。
5. 切换到新 session 说唯一 marker，再切回旧 session 追问旧 marker。
   - 预期：voice binding 随 active session 重建；新 marker 不污染旧 session；旧 session 精确恢复且无 `default`
     固定写入造成的串线。
6. 连续运行 20 轮稳定性 soak，至少包含一次 429/503 fixture 和一次 key rotation。
   - 预期：无重复 turn、无跨 session 污染、无旧音频复活、无静默 fallback；错误可见且可重试，key 更新后
     新连接使用新凭据，旧连接有界退出。

## 4. 幂等、并发与清理审查

1. 重复进入设置、ContextTrace、同一会话各 3 次。
   - 预期：不新增 snapshot revision、attempt、receipt、compact cycle；纯读取无副作用。
2. 同一 activation nonce 连续提交两次，并发两个 stale-CAS writer。
   - 预期：至多一次激活/一次 DB commit；另一请求得到明确 stale/conflict，handler 不重复。
3. compact 在 segment N 成功后中断并重试。
   - 预期：已覆盖 range 不重复提交，coverage 仍 gap=0/overlap=0。
4. 每 case 结束查询 daemon/provider seam：allow-list 回到空、所有 barriers/faults cleared、child 数≤1；保存 counters 后执行 stop。测试后先正常关闭 Tauri，再确认并清理仅本轮创建的 daemon/MCP child/provider seam 进程和隔离 userdata；不得删除用户原目录。
   - 预期：无 orphan deskpet/Vite/control daemon/MCP child/provider seam；原始 evidence 保留，清理动作不截取或输出凭据。

## 5. AC → testcase 追踪矩阵

| AC / assertion id | 自动化 oracle | 真 E2E oracle | 必须存在的证据文件 |
|---|---|---|---|
| 1 / A01-LIFECYCLE | fragment 六字段、固定顺序、无跨层重复 | E2E-01 trace 层级/原始因果顺序 | `automation/AT-CTX-01.json`; `logs/requests/E2E-CTX-01-*.jsonl`; `screenshots/E2E-CTX-01-*-after.png` |
| 2 / A02-CACHE | 两轮 stable bytes/fingerprint 相等，动态字段排除 | E2E-01/10 相邻轮 cache fingerprint 相同且 breakpoint 正确 | `automation/AT-CTX-01.json`; `logs/requests/E2E-CTX-10-*.jsonl` |
| 3 / A03-L123 | L1/L2/L3 独立；完整 tool group；L3 fault 不擦 L2 | E2E-01 marker 承接仍在 | `automation/AT-CTX-01.json`; `screenshots/E2E-CTX-01-04-after.png` |
| 4 / A04-TASK-PROJECTION | 权威优先、闲聊无空 snapshot | E2E-07 四 marker 与卡片一致 | `automation/AT-CTX-02.json`; `logs/requests/E2E-CTX-07-*.jsonl` |
| 5 / A05-PREFLUSH | flush-before-loss、失败 pruning/BLOCK、无 MEMORY append | E2E-02/07/08 snapshot commit 先于 compact | `automation/AT-CTX-02.json`; `logs/requests/E2E-CTX-02-*.jsonl` |
| 6 / A06-ONE-OWNER | 每 cycle 单 owner/model/id、range-once，OFF 互斥 | E2E-02 恰一个 cycle；E2E-06 actual model | `automation/AT-CTX-03.json`; `logs/requests/E2E-CTX-02-*.jsonl` |
| 7 / A07-TOOL-GROUP | call/results 不可拆；receipt 不伪完成 | E2E-02 21 对 tool_call_id/receipt | `automation/AT-CTX-03.json`; `logs/fixture.jsonl`; `logs/requests/E2E-CTX-02-*.jsonl` |
| 8 / A08-BUDGET | whole-request 全字段预算、chain 重算、estimate→prune→re-estimate→BLOCK | E2E-02/10 window/reserve/token 字段对账 | `automation/AT-CTX-04.json`; `logs/requests/E2E-CTX-10-*.jsonl` |
| 9 / A09-DISCLOSURE | skill/L3/path rules 按需且普通 L2 不退化 | E2E-01/09 普通聊天与 code path 对照 | `automation/AT-CTX-05.json`; `logs/requests/E2E-CTX-09-*.jsonl` |
| 10 / A10-REMOUNT | compact 后 task/rules/skill/tail remount identity 相同 | E2E-02/07/08 marker 与 artifact identity 相同 | `automation/AT-CTX-02.json`; `screenshots/E2E-CTX-08-03-after.png` |
| 11 / A11-ATTEMPT-REPORT | 每 attempt 全字段、usage 同 id、敏感 redacted | E2E-10 UI/hash/token 与 request slice 相等 | `automation/AT-CTX-09.json`; `logs/requests/E2E-CTX-10-*.jsonl`; `screenshots/E2E-CTX-10-*-after.png` |
| 12 / A12-ENTRY-MATRIX | text/code/web/research/voice 合同一致、fault safe-fail | E2E-09 四入口和 voice 均保留原文/tail | `automation/AT-CTX-06.json`; `logs/requests/E2E-CTX-09-*.jsonl` |
| 13 / A13-DEFAULT-ROLLBACK | default ON；OFF golden 八类完全相等且互斥 | E2E-11 启动日志和八类 slices | `automation/AT-CTX-06.json`; `backend/tests/fixtures/context_os_off_golden.json`; `logs/requests/E2E-CTX-11-*.jsonl` |
| 14 / A14-LONG-SESSION | 全量回归/性能达标 | E2E-01～11 全 PASS，无跳项 | `automation/AT-CTX-10.json`; `REPORT.md` |
| 15 / A15-COMPACTION-MODEL | IPC/model resolver/failure 不偷换 | E2E-06 requested=resolved=actual；失败无新 summary | `automation/AT-CTX-06.json`; `logs/requests/E2E-CTX-06-*.jsonl`; `screenshots/E2E-CTX-06-*-after.png` |
| 16 / A16-COVERAGE | fit→全 raw；超窗 gap=0/overlap=0；stale BLOCK；page-in strict | E2E-01 全 raw、E2E-02 coverage/page-in | `automation/AT-CTX-05.json`; `logs/requests/E2E-CTX-01-*.jsonl`; `logs/requests/E2E-CTX-02-*.jsonl` |
| 17 / A17-ONE-TOOL-TRUTH | resolver once、immutable revision、prepared/wire hash parity | E2E-03/05/10 schema hash/revision 对账 | `automation/AT-CTX-07.json`; `logs/requests/E2E-CTX-03-*.jsonl` |
| 18 / A18-ACTIVATION | scope-filtered search/describe/activate，nonce/budget/policy fail closed | E2E-03 真四段调用；E2E-04 断连 stale | `automation/AT-CTX-07.json`; `logs/fixture.jsonl`; `logs/requests/E2E-CTX-04-*.jsonl` |
| 19 / A19-EXEC-GUARD | dispatch/policy/stale fail closed、CAS/cancel、durable 闭环不退化 | E2E-03/04/05/07/08/10 scope-schema-handler 三方对账 | `automation/AT-CTX-08.json`; `logs/fixture.jsonl`; `logs/requests/E2E-CTX-05-*.jsonl` |
| VR / A20-VOICE-RELAY | capability gate、relay-only cold start、turn/final 幂等、cancel/fence/reconnect/session binding | E2E-12 五轮中文、3 次真人打断、断网/重启/切 session、20 轮 soak | `automation/AT-VOICE-01.json`; `logs/requests/E2E-CTX-12-*.jsonl`; `screenshots/E2E-CTX-12-*-after.png` |

## 6. 最终通过门槛

- AT-CTX-01～10 全部 PASS；全量回归无新红线；性能阈值全部满足。
- E2E-CTX-01～12 全部按真实 UI 动作执行并有 before/after 截图与同 request 日志；不得 SKIP 或以脚本替代。
- MCP 工具 case 必须额外完成 scope revision、provider schema hash、handler receipt 三方对账。
- 8K case 确认一次单 owner compact；默认大窗口 case 确认全 raw；20+ tool calls、task switch、restart、stale、disconnect、设置专用模型与失败不偷换均有证据。
- AC-CTX-1～19 与 A20-VOICE-RELAY 追踪矩阵每格至少一份实际 PASS 证据后，增量后的 Context OS V1 才可判定验收完成。
