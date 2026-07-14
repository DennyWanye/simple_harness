# Context OS V1 实施与验证结果

> 日期：2026-07-13
> 结论：**Context OS 自动化、性能与 Windows Computer Use E2E-CTX-01..11 已通过；整体仍不得标记 complete，因为 relay 尚未提供 Realtime/ASR/TTS 契约，Voice Relay Only 的 E2E-CTX-12 被 VR-0 硬阻断。没有用本地语音、脚本、旧实例或协议注入伪造 E2E PASS。**

## 1. 已落地范围

Context OS V1 已完成默认 ON 接线：统一 typed fragments、TaskContextSnapshot、单 Session coverage tree、PreparedToolSet/capability scope、全请求预算、单一 ContextCompressor owner、provider-attempt report、ContextTrace coverage、text/code/voice/subagent prepared contract，以及单一 `context_os_v1=false` legacy rollback。

本轮最后发现并关闭 7 个由更宽回归暴露的实现回归：

| 类别 | 数量 | 处理结果 |
|---|---:|---|
| compaction observability event | 1 | 修复非 metadata reactive compaction 的 requested-model 初始化；单 owner 下 event/metric 恰一次。 |
| ephemeral/auxiliary attempt | 2 | 修复辅助 provider attempt 的边界与终态。 |
| migration | 3 | 修复 V18/兼容迁移相关回归。 |
| provider signature compatibility | 1 | 修复 fake/第三方 provider 兼容签名回归。 |

这些修复均进入后续相关分组复测；没有通过删除断言、放宽预期或跳过用例获得绿色结果。

## 2. 自动化结果

| 层级 | 结果 | 判定边界 |
|---|---|---|
| Default-ON focused 综合 | **185 passed in 15.02s** | 覆盖 planner、snapshot、coverage、capability、attempt、voice/default/rollback 等 Context OS 聚焦合同。 |
| 后续相关修复组 | **73 passed** | 覆盖最后一轮回归修复及相邻合同。 |
| Automated runner | **AT-CTX-01..10: selected=10, failed=0** | 十个自动验收场景全部通过；这是自动化，不替代 Windows UI E2E。 |
| Frontend Vitest 全量 | **75 files / 782 tests passed** | ContextTrace 与既有前端回归通过。 |
| TypeScript | **`tsc -b` exit 0** | 类型构建通过。 |
| Backend 字母分组 | **累计约 4279 passed，剩余 21 项红线** | 这是分组执行的累计结果，不是一次无重叠的全量 pytest 数字；21 项均单列如下，未伪装成全绿。 |

本轮 DoD 证据已收敛到可审计的逐子命令 JSON：

- [`AT-CTX-09.json`](./test-results/automated/AT-CTX-09.json) 记录 ContextTrace/ModelContextCard focused Vitest（2 files / 16 tests）及 `tsc -b`，两个子命令均 exit 0。
- [`AT-CTX-10.json`](./test-results/automated/AT-CTX-10.json) 记录七个完整 backend 字母 shard、全量 Vitest（75 files / 782 tests）、`tsc -b` 与 benchmark compare。Backend 精确命中预先冻结的 21 条红线，`observed=21`、`unexpected=0`；这些红线仍是失败测试，不被伪装成全绿。
- [`summary.json`](./test-results/automated/summary.json) 为 `selected=10, failed=0`；这里的 PASS 表示没有新增/意外红线，不表示 21 条既有红线已经修复，也不替代 Windows UI E2E。

基线的 89 个 Context/Agent focused tests 没有被当作本轮最终数字重复计算。最终证据采用上表本轮执行结果。

## 3. 性能与工具披露 benchmark

ON 模式与 `context-perf-baseline.json` 比较通过：

| 指标 | 结果 | 门限/结论 |
|---|---:|---|
| short-chat assembly P95 | **0.1422 ms** | 相对基线增量 **0.1416 ms**，低于 **20 ms** 门限。 |
| 500 deferred capabilities 初始 schema reduction | **99.398416%** | 高于计划要求的 80%。 |
| 无 deferred 命中时辅助 LLM 调用 | **0** | 未新增辅助 provider round-trip。 |
| compare verdict | **passed** | ON benchmark 通过。 |

该 benchmark 是 provider-free assembly/payload 性能证据，不证明真实 relay 延迟、tokenizer 精度或 GUI 体验。

## 4. 21 个既有/环境红线

下列 21 项在 backend 字母分组结束后仍红。它们没有被归为 Context OS 自动化 PASS，也没有被本任务擅自修复：

| 组 | 数量 | 当前表现/归因 |
|---|---:|---|
| `agent_parallel` timing | 1 | 期望 `<0.18s`，当前约 `0.205s`；时间敏感红线。 |
| `e2e_integration` | 1 | fixture/runtime 中 `memory_store is None`。 |
| `hybrid_router` | 1 | local strict-key 场景转入 cloud，与测试预期不符。 |
| `image_config` | 1 | endpoint 的 global config 状态污染相邻测试。 |
| `model_catalog` | 2 | 全局 1M context-window override 与用例期望 400K 冲突。 |
| Ollama live | 1 | 环境端点返回 HTTP 404。 |
| `outcome_verifier` | 1 | 当前工作目录/fixture 不是可用 git 环境。 |
| `p4s21` memory stub | 3 | stub 缺少 `time_remaining_ms`。 |
| `p4s22` web search | 2 | 外部 mock/解析假设与当前实现不一致。 |
| `workflow_eval_cli` | 8 | observer attributes/fixture baseline 漂移。 |
| **合计** | **21** | 作为既有/环境红线保留。 |

“既有/环境”只表示它们不是本次 Context OS focused gate 新引入的失败，不表示这些问题已经解决或可以从总体验收中忽略。

## 5. Windows E2E 初始阻断（已解除，保留历史记录）

计划要求的 Tauri dev + Windows Computer Use 真点击 E2E 未能启动。`tauri-dev.log` 的阻断原文为：

```text
failed to run 'cargo metadata' ... program not found
```

已使用合法 launcher 完成残留进程清理，但环境中没有可执行的 Cargo 工具链。桌面上现有的 Desktop Pet 是昨晚遗留旧实例，不能代表本轮源码、默认 ON 配置或当前 backend，因此没有复用它截图，也没有把它计为 E2E 证据。

本轮明确未采用以下替代方式冒充真人 E2E：

- WebSocket/HTTP 直接注入；
- pytest 或 automated runner 回放；
- import backend 查询 registry/store；
- 旧 frozen backend 或旧桌面实例；
- 仅凭启动日志、ContextTrace 单测或截图静态页面判 PASS。

以上是 2026-07-13 的初始阻断快照。2026-07-14 已恢复 Cargo/Tauri 并按 `testcase.md` 完成动作前坐标声明、真实点击/输入、截图和 Tauri/backend/provider 日志对账；当前结论以 §7 的续报为准。

## 6. 最终判定

| 交付面 | 状态 |
|---|---|
| Context OS V1 代码与默认 ON | PASS（自动化边界） |
| Focused/backend/frontend/typecheck | PASS，但保留并明确列出 21 项更宽红线 |
| 性能与渐进工具披露 | PASS |
| Windows Computer Use 真 E2E | **E2E-CTX-01..11 PASS；E2E-CTX-12 被 relay VR-0 阻断** |
| Voice Relay Only | **BLOCKED：relay 无 Realtime/ASR/TTS endpoint 与 voice model alias** |
| 整体计划完成度 | **NOT COMPLETE** |

当前剩余完成条件是 relay 通过 VR-0 并完成 E2E-CTX-12；`STATUS/status.md` 只记录当前黄色部分完成与上游阻塞，不得把整体 plan 晋升为 complete。

## 7. 执行续报（2026-07-14）

上一节的 Cargo blocker 已解除：本机 Cargo 位于 `C:\Users\Administrator\.cargo\bin\cargo.exe`，
Rust/Tauri 测试 71/71 通过，Context OS 真 UI E2E 已继续推进。最终结果仍未收口，STATUS 仅记录 E2E-01～11 已通过与 E2E-12/VR-0 blocked。

### 7.1 Voice Relay Only 增量能力探测

用户锁定正式语音路径不得再使用本地模型。探测使用本机现有 relay key，但输出只保留状态码、模型数量和
脱敏 capability 结果，没有输出凭据。

| Probe | 结果 |
|---|---|
| `GET https://chinzy.com/v1/models` | 200；146 aliases；voice/audio/realtime/whisper/tts/transcription 命中 0 |
| `POST /v1/realtime/client_secrets` | 404 `NOT_FOUND` |
| `POST /v1/audio/speech` | 404 `NOT_FOUND` |
| `POST /v1/audio/transcriptions` | 404 `NOT_FOUND` |
| `gpt-audio` via `/v1/chat/completions` | 未形成可用音频响应 |
| 中转站公开文档 | 只承诺 OpenAI Chat Completions 与 Anthropic Messages；模型表未发布语音 alias |

当前结论：VR-0 是真实上游 blocker。DeskPet 侧不能在 relay 尚未发布语音模型/Realtime endpoint 时实现并
真测“中转站聊天 + 打断”。计划已新增 VR-0..VR-2 与 E2E-CTX-12；在上游契约通过前，不写假 provider、
不把旧本地链路冒充 relay，也不静默降级。

### 7.2 E2E-CTX-10 ContextTrace / fallback / force-finish / privacy

本 case 使用新隔离 userdata、8312 backend、5385 Vite 和 launcher 自管的唯一 Tauri/backend/Vite 栈；所有用户
请求均通过 Windows Computer Use 在真实 DeskPet 输入框点击、输入和发送，没有用 WebSocket/HTTP 注入。

初次 force-finish 真机运行发现边界缺陷：`max_iterations=3` 被三轮 tool call 全部耗尽，provider 没有得到
`tools=None` 的最终收尾 attempt，控制状态停在 F3。实现已修正为：Context OS 请求在至少执行过一次工具后，
最后一个 iteration 固定保留给 `tools=None + tool_choice=none`；E2E 配置改为 `max_iterations=4`，即前三轮真实
工具、第四轮收尾。非 Context OS legacy 行为不变。

| 核验项 | 结果 |
|---|---|
| 自动化回归 | **45 passed in 13.53s**；另有相邻 AgentLoop 回归 **24 passed** |
| direct fixture | 首轮 provider 暴露 canonical `mcp_context-os-e2e_mcp_ctx_fixture_tool_001`；daemon 真实 handler `ctx-1-1` |
| provider fallback | 同一 request `34a9a89e…`：`provider:0`/`ctx-primary` 返回 503，`provider:1`/`ctx-fallback` stop，状态 DONE 后清 arm |
| force-finish | 同一 request `13e804cf…`：iteration 1～3 为 tool_calls，daemon 新增 `ctx-1-2..4`；iteration 4 purpose=`force_finish`、tools_count=0、`observed_tools_none=true`、finish_reason=stop，状态 DONE 后清 arm |
| ContextTrace | 真 UI 显示 `force_finish · succeeded`、planned/window/budget/reserve、7 direct + 546 deferred、wire/schema/policy/cache hash、loaded fragments 和 coverage valid |
| privacy | 7 个 E2E 文本日志中假 secret 与假 tool body 明文命中文件数均为 0；ContextTrace 截图无明文 |

Computer Use 的 frame channel 在 onboarding 后持续超时；按手测重试纪律完成多种恢复后，UI 点击仍由 Computer Use
执行，截图单独改用 Windows 屏幕捕获 API 获取当前已激活窗口，没有用它替代任何交互或 provider/handler 判定。
证据位于：

- [`context-trace-context.jpg`](./test-results/manual-final-20260714-e2e10-fixed/cases/E2E-CTX-10/context-trace-context.jpg)
- [`context-trace-attempts.jpg`](./test-results/manual-final-20260714-e2e10-fixed/cases/E2E-CTX-10/context-trace-attempts.jpg)
- [`provider-seam.jsonl`](./test-results/manual-final-20260714-e2e10-fixed/cases/E2E-CTX-10/logs/provider-seam.jsonl)
- [`fixture.jsonl`](./test-results/manual-final-20260714-e2e10-fixed/cases/E2E-CTX-10/logs/fixture.jsonl)
- [`tauri-dev.log`](./test-results/manual-final-20260714-e2e10-fixed/cases/E2E-CTX-10/logs/tauri-dev.log)

E2E-CTX-10 判定 **PASS**。后续 E2E-CTX-11 已在 §7.3 完成；整体仍不标 complete，因为 E2E-CTX-12 被 VR-0 relay 能力缺失阻断。

### 7.3 E2E-CTX-11 Default → OFF → ON 唯一回退

本 case 使用第三份隔离 userdata，并在每次切换后完整关闭、重新启动唯一的 Tauri/backend/Vite 栈。所有测试消息均通过 Windows Computer Use 在真实 DeskPet 输入框点击、输入和发送；没有通过 WebSocket/HTTP 注入用户消息。

| 阶段 | 启动证据 | 实际 provider payload | 判定 |
|---|---|---|---|
| Default | `ACTIVE / follow_session / revision 56` | `CTX-ON-DEFAULT-EVIDENCE` 携带 `memory_read,memory_search,tool_search,tool_describe,tool_activate,context_page_in` | PASS |
| OFF | `ROLLBACK / follow_session / revision 51` | 8 个固定 task_type 与 checked-in legacy golden 的 names/order 完全相等 | PASS |
| ON restore | `ACTIVE / follow_session / revision 56` | 同一 userdata 的 `CTX-ON-RESTORE-EVIDENCE` 恢复上述 6 个 Context OS 工具，未混入 OFF 的 50 工具 | PASS |

第一次 OFF 运行暴露夹具缺口：辅助 classifier 一律返回 `AUX-ACK`，导致固定 prompt 被错误分类。修复后，夹具只对 `CTX-OFF-*` 输出 parser-valid 的确定性分类结果；其他 E2E 场景仍保持原有 `AUX-ACK` 行为。夹具增加了显式 stop 控制，完整 14 项测试后无孤儿 Python 进程；最终 launcher/fixture/rollback/causality 精确回归 `30 passed`。

旧 golden 还暴露出它并非从真实启动栈校准：记录 revision 66，并把当前 registry 中不存在的 `file_search/todo_add/todo_list/todo_update` 写进 code/plan。保留旧 golden 运行 oracle 得到预期失败后，依据真实 OFF 启动栈的 provider payload 重新校准为 revision 51；task/plan 的空列表和 code 的三个有效工具是当前 legacy registry/policy 交集的真实基线，不从 ON policy 推导。随后在 `manual-final-20260714-e2e11-audit-off` 通过 UI 完整重放八类 prompt，oracle 与定向测试均通过。

关键证据：

- [`Computer Use 动作记录`](./test-results/manual-final-20260714-e2e11-audit-off/cases/E2E-CTX-11/manual-actions.md)
- Default：[`before`](./test-results/manual-final-20260714-e2e11-audit-default/cases/E2E-CTX-11/default-before.png) / [`after`](./test-results/manual-final-20260714-e2e11-audit-default/cases/E2E-CTX-11/default-after.png) / [`provider seam`](./test-results/manual-final-20260714-e2e11-audit-default/cases/E2E-CTX-11/logs/provider-seam.jsonl)
- OFF：[`before`](./test-results/manual-final-20260714-e2e11-audit-off/cases/E2E-CTX-11/off-before.png) / [`after`](./test-results/manual-final-20260714-e2e11-audit-off/cases/E2E-CTX-11/off-after.png) / [`evidence.json`](./test-results/manual-final-20260714-e2e11-audit-off/cases/E2E-CTX-11/evidence.json) / [`observation`](./test-results/manual-final-20260714-e2e11-audit-off/cases/E2E-CTX-11/off-observation.json) / [`provider seam`](./test-results/manual-final-20260714-e2e11-audit-off/cases/E2E-CTX-11/logs/provider-seam.jsonl)
- ON restore：[`before`](./test-results/manual-final-20260714-e2e11-audit-on/cases/E2E-CTX-11/on-before.png) / [`after`](./test-results/manual-final-20260714-e2e11-audit-on/cases/E2E-CTX-11/on-after.png) / [`provider seam`](./test-results/manual-final-20260714-e2e11-audit-on/cases/E2E-CTX-11/logs/provider-seam.jsonl)
- [`停机后 UI/日志 SHA-256 清单`](./test-results/manual-final-20260714-e2e11-audit-off/cases/E2E-CTX-11/ui-evidence-hashes.json)

Default 阶段的 Computer Use frame 通道连续三次超时后，只对截图保存改用 DPI-aware `CopyFromScreen`；所有点击、键盘输入和发送动作仍由 Windows Computer Use 执行。三个运行栈全部停止后生成哈希清单并再次校验 `evidence.json`，避免运行中日志继续增长造成哈希失效。

E2E-CTX-11 判定 **PASS**。E2E-CTX-12 仍被 VR-0 阻断：当前 relay 没有 Realtime/ASR/TTS endpoint 或语音模型 alias，而且 fresh app log 仍显示本地 Silero VAD 与 faster-whisper 加载，因此不满足“正式语音不再使用本地模型”的产品要求。

### 7.4 最终回归快照

- E2E-11 修复后的 launcher/fixture/default-rollback/causality：`30 passed`；provider fixture 全文件：`14 passed`，测试后无 DeskPet/Context OS E2E 孤儿进程。
- Context OS focused/架构交叉组：`61 passed` / `48 passed`；AT-CTX-01..08：`8/8 PASS`。
- Frontend：`75 files / 782 tests passed`；TypeScript build exit 0；Rust/Tauri：`71/71`。
- Backend d–f shard：`930 passed, 10 skipped, 4 deselected, 2 failed`；两项均为既有 vector worker 时间敏感 flaky，独立复跑 `2 passed`。m–r / s–z 先前 7 个异常 nodeid 复跑 `7 passed`。
- 本次交付文件语法、三个 JSON evidence artifact 与定向 whitespace 检查通过。仓库级 `git diff --check` 仅命中无关历史文件 `plans/2026-06-21-deepresearch-subagent-fanout/exec/relaunch_p2.out` 的 5 行既有尾空格，未改动该用户文件。
