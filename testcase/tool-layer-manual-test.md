# 手工测试用例 — DeskPet 工具层（Tool Layer）

> **被测功能**: 整个 deskpet 工具层 —— 工具注册/发现、用户级工具触发（PPT/Excel/Word/PDF/OCR/图片/文件整理/网页抓取）、
> artifact 信封、receipt 落盘、verify gate（fake-completion 防护）、circuit breaker 熔断、permission gate、
> toolset 门控（disabled/dangerous/timeout）、tool_search 懒加载、agent_parallel 多 agent、workspace 沙箱。
> **被测分支**: `master`（`G:\projects\deskpet`）
> **对应迭代**: tool-last-mile-upgrade（D1~D12）+ tool-layer-optimization-v3（WI-T2.1 / WI-T5.1）+ companion-code v1（agent_parallel）
> **测试类型**: **混合** —— A 类用户级 UI 真测（必须 windows-mcp / CDP 真模拟点击 + 截图，**不可用单测/协议层替代**，见 CLAUDE.md「🔒 手工测试纪律」）；B 类配置/契约核对（改 config + 跑脚本 + 看落盘文件）。
> **最后更新**: 2026-06-01

---

## 0. 测试前置准备

> ⚠️ **启动纪律**（CLAUDE.md 踩坑 #7/#8/#9）：**不要**手动起 backend / 手动起 vite。
> 只用脚本启动 Tauri，让它自己 spawn backend + vite。dev 模式必须跑**源码**（不是 stale 打包 exe）。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| P-1 | 确认在 master：`git branch --show-current` | 输出 `master` |
| P-2 | 主树启动：`powershell -File scripts/dev-start.ps1`（或 worktree 用 `dev-worktree.ps1`） | 桌宠窗口出现；终端 log 出现 `[backend_launch] Dev python=... backend_dir=<...>backend`（**确认是源码不是 `Bundled exe=...`**） |
| P-3 | 定位 user data 目录 | 主树默认 `%APPDATA%\deskpet`；dev 注入了 `DESKPET_USER_DATA_DIR` 则为该目录。记下，后面找 receipts/artifacts/metrics 用 |
| P-4 | 定位配置文件 | 仓库根 `config.toml`（改 flag 后需重启桌宠生效） |
| P-5 | 定位后端日志 | backend structlog 全走 stderr → 落进 tauri dev 终端输出。保持该终端可见 |
| P-6 | 进入 code 模式 | 桌宠对话切到 code 面板（code-panel）；准备好用自然语言对话触发工具 |

**判定标准约定**：每个 TC 末尾 `PASS / FAIL / RETRY-N / SKIP（带理由）`。
A 类（UI）失败必须按纪律 retry ≥3 次不同 workaround 才能标 SKIP。

---

# A 类 — 用户级工具触发（桌宠 UI 真测，必须 windows-mcp / CDP）

> 这一类证明「**用户真的用得了**」。每个动作前先 declare：`坐标=(x,y) | 动作=click/type | 期望=...`。
> 证据：截图存 `plans/manual-results-<date>/screenshots/`，并 grep backend log 关键事件。

## TC-A1 — 生成 PPT（ppt_create → ArtifactCard + .pptx 落盘）

**目的**: 验证最核心的产物类工具端到端：自然语言 → LLM 调 `ppt_create` → 真生成 .pptx → 前端 ArtifactCard 渲染 → 文件真落盘。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | code 模式输入框真输入「帮我生成一份关于"猫咪护理"的3页PPT」→ 回车 | 消息真发出；backend log 出现一次 LLM turn |
| 2 | 等待 LLM 调用工具 | backend log 出现 `execute_tool ppt_create` / tool dispatch 日志；对话区出现"正在生成"类反馈 |
| 3 | 等生成完成 | 对话区出现 **ArtifactCard**（不是纯 JSON 文本），卡片含文件名 `*.pptx` + 可打开/定位按钮 |
| 4 | 去落盘目录核对 | `<artifact_dir 或 temp>/<YYYY-MM-DD>/ppt_create/<slug>-<8hex>.pptx` 真实存在，大小 > 0，能用 PowerPoint 打开且有 3 页 |
| 5 | 截图 ArtifactCard + 文件资源管理器 | 截图存档 |

**判定**: ArtifactCard 渲染 + .pptx 真落盘可打开 → **PASS**；只出 JSON 文本无卡片，或文件不存在 → **FAIL**

---

## TC-A2 — 生成 Excel（excel_create）

**目的**: 验证 `excel_create` 端到端。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 真输入「做一个含'姓名/年龄/城市'三列、3行示例数据的Excel」→ 回车 | 消息发出 |
| 2 | 等工具调用 | backend log 出现 `excel_create` dispatch |
| 3 | 等完成 | ArtifactCard 出现，文件名 `*.xlsx` |
| 4 | 核对落盘 | `.../excel_create/<slug>-<hex>.xlsx` 存在，Excel 打开有 3 列表头 + 3 行数据 |

**判定**: 卡片 + .xlsx 真落盘且内容正确 → **PASS**

---

## TC-A3 — 生成 Word 文档（doc_create）

**目的**: 验证 `doc_create` 端到端。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 真输入「写一篇 200 字左右介绍桌宠的 Word 文档」→ 回车 | 消息发出 |
| 2 | 等完成 | ArtifactCard，文件名 `*.docx`；`.../doc_create/<slug>-<hex>.docx` 落盘，Word 打开有正文 |

**判定**: 卡片 + .docx 真落盘 → **PASS**

---

## TC-A4 — 网页抓取（web_fetch）

**目的**: 验证零成本 web 工具链可用（不依赖任何商业搜索 API）。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 真输入「帮我抓取 https://example.com 的正文内容」→ 回车 | 消息发出 |
| 2 | 等工具调用 | backend log 出现 `web_fetch` dispatch（首次会读 robots.txt + 限速） |
| 3 | 等完成 | 对话区返回 example.com 的正文文本（含 "Example Domain" 字样），不是报错 |

**判定**: 返回真实网页正文 → **PASS**；返回 `error` / 空 → **FAIL**（区分是网络问题还是工具问题，网络问题记 SKIP+理由）

---

## TC-A5 — 能力门控 graceful refuse（capability_gate）

**目的**: 验证 `[companion].capability_gate_enabled=true` 时，请求一个**没有对应工具**的能力会被优雅拒绝（不是假装做了 = fake completion 的另一面）。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 真输入「帮我生成一段 10 秒的视频」（工具层无视频生成工具）→ 回车 | 消息发出 |
| 2 | 看回复 | 桌宠**明确说做不到 / 没有该能力**，而不是谎称"已生成"或给假链接 |

**判定**: 优雅拒绝、不撒谎 → **PASS**；谎称完成 / 给假产物 → **FAIL**（这是 fake-completion，严重）

---

# B 类 — 配置与契约核对（改 config + 跑脚本 + 看落盘）

> 这一类验证工具层的**不变量与开关**。可在终端/编辑器完成，不需要 UI 真点，但**不替代 A 类**——
> A 类才是"用户用得了"的证据，B 类是"内部契约对"的证据。

## TC-B1 — 一键验收 smoke（last_mile_smoke）

**目的**: 跑 last-mile 准入硬条件 + 4 个一票否决，确认工具层整体可发布。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 终端跑 `python scripts/acceptance/last_mile_smoke.py` | 脚本运行，输出彩色 PASS/FAIL 表 |
| 2 | 看末行决策 | 出现 `DECISION: SHIP`（退出码 0）。若 `NO-SHIP`/退出码非 0 → 记录哪条一票否决（MR-0/8/13/19）失败 |

**判定**: `DECISION: SHIP` → **PASS**；否则 **FAIL**（附失败项）

---

## TC-B2 — artifact_envelope 开关字节级一致（D1）

**目的**: 验证 `artifact_envelope` flag OFF 时，工具 result **缺** `artifacts` 键（不是空数组，是缺键 —— 字节级一致硬保证）。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 当前 `config.toml [tools.last_mile] artifact_envelope = true` | 确认 ON |
| 2 | 走一次 TC-A1（生成 PPT），观察工具 result envelope（前端 ArtifactCard 来源 / 或 receipt） | envelope dict **含** `artifacts` 键（数组，里面有 path） |
| 3 | 改 `artifact_envelope = false` → 重启桌宠 → 再生成一次 | envelope dict **不含** `artifacts` 键（缺键，非空数组）；前端退回旧显示 |
| 4 | 改回 `true` 复原 | — |

**判定**: ON 有键 / OFF 缺键 → **PASS**；OFF 时出现 `artifacts: []` 空数组 → **FAIL**（违反字节级一致）

---

## TC-B3 — receipt 落盘 + HMAC 签名（D5）

**目的**: 验证 `emit_receipts=true` 时每次工具调用写一条带 HMAC 签名的 receipt。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 确认 `[tools.verifier] emit_receipts = true` | ON |
| 2 | 走一次任意工具调用（如 TC-A2） | — |
| 3 | 打开 `<user_data>/receipts/<session_id>.jsonl` | 文件存在，最后一行是 JSON：含 `tool_name` / `args` / `ok` / `started_at` / `ended_at` / `duration_ms` / `iteration` / `sig` 字段 |
| 4 | 检查 `duration_ms` | **> 0**（不是 ~0；v3 P0 修：用真实 started_at 对账，曾经 bug 是恒为 0） |
| 5 | 改 `emit_receipts = false` → 重启 → 再调一次工具 | 不再追加新 receipt 行（BC 路径） |
| 6 | 改回 `true` 复原 | — |

**判定**: receipt 行字段齐全 + duration_ms>0 + OFF 时不产 → **PASS**

---

## TC-B4 — verify gate shadow 模式 + metrics 真出现 verify 事件（WI-T2.1 / D6）

**目的**: 验证 fake-completion 防护已**真接电**——`verify_gate_mode="shadow"` 时遇到"声称完成但无产物"会 warn 并向 metrics 写 `verify_*` 事件（v3 硬要求：**必须 metrics.jsonl 真出现 verify_* event，不能 grep 源码当证据**）。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 确认 `[tools.verifier] verify_gate_mode = "shadow"` | ON（shadow） |
| 2 | 启动桌宠后，找到 metrics 落盘文件（`<user_data>/metrics.jsonl` 或日志配置位置） | 文件存在 |
| 3 | grep metrics 文件 `verify_gate_init` | boot 时出现一条 `verify_gate_init` 事件（证明 gate 真初始化接电，不是死代码） |
| 4 | 诱发一次"声称完成无产物"场景：让桌宠回复含完成短语但工具未真产出（可借 claim_patterns 命中短语的对话，如让它"说已经保存好了"但不实际产文件） | shadow 模式：backend log 出现 verify warn；metrics 出现 `verify_gate_nudge_injected`（或同类 verify_* 事件） |
| 5 | （可选）改 `verify_gate_mode = "strict"` → 重启 → 重复步骤 4 | strict 模式：不仅 warn，还真注入 nudge / 阻断假完成 |

**判定**: metrics.jsonl 真出现 `verify_gate_init` + shadow 诱发出 `verify_*` 事件 → **PASS**；
metrics 无任何 verify 事件 → **FAIL**（说明 gate 没接电，是死代码）

---

## TC-B5 — circuit breaker 连续失败熔断（P5-S2）

**目的**: 验证同一工具连续失败 3 次后熔断，返回 `circuit_open` + 替代工具列表。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 确认 `[supervisor] circuit_breaker_threshold = 3` | =3 |
| 2 | 在 code 模式诱发同一工具连续失败 3 次（如反复让它读一个**不存在的文件路径** → file_read 返 `ok:false`） | 前 3 次返各自的失败 envelope（`ok:false`） |
| 3 | 第 4 次再调同一工具 | 返回 `{"ok": false, "error": "circuit_open", ...}`，含中文 hint「连续失败3次已熔断」+ `available_alternatives`（同 toolset 的兄弟工具名列表） |
| 4 | 等 `circuit_breaker_cooldown_seconds=60` 秒后再调一次 | HALF_OPEN：允许一次探针；成功则恢复，失败则继续熔断 |

**判定**: 第 4 次出现 circuit_open + 替代列表 → **PASS**；一直重试不熔断 → **FAIL**

---

## TC-B6 — toolset 门控：disabled_toolsets 双层挡（WI-T5.1）

**目的**: 验证禁用整个 toolset 时，schema 层（LLM 看不到）+ execute_tool 层（直接调也拒）双重生效。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 在配置里把 `office` 加入 `[tools] disabled_toolsets` → 重启 | — |
| 2 | code 模式让它「做个Excel/写个Word」 | LLM 工具清单里**看不到** excel_create/doc_create/pdf_export/image_ocr/file_organize（均属 office toolset，schema 层挡）。注意 `ppt_create` 属 `ppt` toolset、`generate_image` 属 `image`，不受 `disabled=["office"]` 影响 |
| 3 | 强行让它尝试调 excel_create | 返回 `{"ok": false, "error": "...disabled by [tools] disabled_toolsets (toolset=office)..."}`（execute_tool 层挡） |
| 4 | 对比 `disabled_toolsets_schema_only`：把 office 改放进 schema_only → 重启 | schema 仍看不到，但 execute_tool **可调**（仅 schema 层挡） |
| 5 | 复原配置 | — |

**判定**: disabled_toolsets 双层都挡 / schema_only 仅 schema 挡 → **PASS**

---

## TC-B7 — dangerous 工具白名单（WI-T5.1）

**目的**: 验证 `dangerous_tools_allowlist` 非空时，只有白名单内的 `dangerous=True` 工具可见。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 确认 dangerous 工具（如 computer use 的 `screen_click` / `run_shell`）默认可见 | 在工具清单中 |
| 2 | 设 `[tools] dangerous_tools_allowlist = ["run_shell"]` → 重启 | 仅 run_shell 这一个 dangerous 工具可见，screen_* 等其它 dangerous 工具被过滤 |
| 3 | 复原 | — |

**判定**: 白名单生效、非白名单 dangerous 工具被过滤 → **PASS**

---

## TC-B8 — 工具超时（default_timeout_seconds / per-tool）

**目的**: 验证工具执行超时返回统一 `tool_timeout` 信封（而不是卡死或崩溃）。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 临时设一个很短的 `[tools] default_timeout_seconds = 1`（或针对某慢工具）→ 重启 | — |
| 2 | 触发一个耗时 > 1s 的工具（如抓取一个慢响应网址 / 大文件处理） | 返回 `{"ok": false, "error": "tool_timeout: <name> exceeded 1s"}`，agent loop 继续而非崩溃 |
| 3 | 复原 default_timeout_seconds | — |

**判定**: 超时返统一 tool_timeout 信封、循环不死 → **PASS**

---

## TC-B9 — tool_search 懒加载元工具（control）

**目的**: 验证 `tool_search` 能按关键词返回匹配工具的 schema 子集（CCB 懒加载模式）。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | code 模式问「有哪些和'网页/web'相关的工具？」诱发 tool_search，或观察 dispatcher 自动调用 | backend log 出现 `tool_search` dispatch |
| 2 | 看返回 | JSON 含 `matches`（web_fetch/web_crawl/... 的 schema）+ `count` + `query`；大小写不敏感、多 token 全匹配 |

**判定**: 返回相关工具子集 + count 正确 → **PASS**

---

## TC-B10 — workspace 沙箱与写入域（file 工具 + write_scope_enforced D3）

**目的**: 验证 file_write/file_read 等被强制限制在 workspace 根，`..` 穿越被挡；companion session 写入域受限而 code session 绑 project_root 不受影响。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | code 模式让它「在工作区写一个 test.txt 内容随便」 | file_write 成功，文件落在 `%APPDATA%\deskpet\workspace\test.txt` |
| 2 | 让它「写到 `../../Windows/system32/x.txt`」（穿越攻击） | 被拒绝 / 路径被强制归一到 workspace 内，**不**写到系统目录 |
| 3 | 确认 `[companion] write_scope_enforced=true` 下 companion(default) session 写盘限 workspace | companion 模式无法写任意仓库路径；code 模式（绑 project_root）正常 |

**判定**: 沙箱生效 + `..` 穿越被挡 → **PASS**；能写出 workspace 之外 → **FAIL**（安全问题）

---

## TC-B11 — agent_parallel 多 agent 派发（companion v1，master 存在）

**目的**: 验证 code 模式下 `agent_parallel`（hub-and-spoke 多 subagent）可用。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | code 模式给一个可拆 2-3 个独立子任务的请求（如「同时：①查一个网址 ②整理一个文件夹 ③写个小文档」） | backend log 出现 `agent_parallel` dispatch（若 ContextAssembler 判定为多任务） |
| 2 | 等完成 | 多个子任务各自有结果汇总返回，不是串行卡死 |

**判定**: agent_parallel 真派发并汇总 → **PASS**；
若 master 上 ContextAssembler 未触发该工具，记 **SKIP（带理由：默认 session 未路由到 agent_parallel）** 并在 code-mode 显式请求下复测

---

## TC-B12 — 工具注册健全性 + name 冲突保护（WI-T4.1，基础设施）

**目的**: 验证自动发现把所有工具注册成功，且重复注册同名工具会抛 `ToolNameConflictError`（防 stub 无声覆盖真实现）。

> ⚠️ 此为**基础设施健全性检查**，**不替代** A 类的"用户用得了"证据；仅作为 smoke 前置。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 桌宠正常启动后看 boot log | 无 `tool auto-discovery: failed to import ...` 报错；工具数量符合预期（40+） |
| 2 | （开发者）确认 stubs.py 用守卫模式 `if not registry.has(name)` | 真实现注册不被 late-loaded stub 覆盖 |

**判定**: 全部工具发现成功、无冲突崩溃 → **PASS**

---

## 测试结果汇总表（执行时填写）

### A 类 — 用户级 UI 真测（需 windows-mcp / CDP + 截图）

| 用例 | 被测工具 | 判定 | 截图 / log 证据 |
|---|---|---|---|
| TC-A1 生成 PPT | ppt_create | ☐ PASS / ☐ FAIL / ☐ RETRY-N | |
| TC-A2 生成 Excel | excel_create | ☐ PASS / ☐ FAIL | |
| TC-A3 生成 Word | doc_create | ☐ PASS / ☐ FAIL | |
| TC-A4 网页抓取 | web_fetch | ☐ PASS / ☐ FAIL / ☐ SKIP | |
| TC-A5 能力门控拒绝 | capability_gate | ☐ PASS / ☐ FAIL | |

### B 类 — 配置与契约核对

| 用例 | 被测点 | 判定 | 备注 |
|---|---|---|---|
| TC-B1 一键验收 smoke | last_mile_smoke | ☐ PASS / ☐ FAIL | |
| TC-B2 artifact 信封开关 | D1 字节级一致 | ☐ PASS / ☐ FAIL | |
| TC-B3 receipt 落盘+HMAC | D5 | ☐ PASS / ☐ FAIL | |
| TC-B4 verify gate + metrics | WI-T2.1 接电 | ☐ PASS / ☐ FAIL | metrics 真出现 verify_* |
| TC-B5 circuit breaker 熔断 | P5-S2 | ☐ PASS / ☐ FAIL | |
| TC-B6 disabled_toolsets 双层 | WI-T5.1 | ☐ PASS / ☐ FAIL | |
| TC-B7 dangerous 白名单 | WI-T5.1 | ☐ PASS / ☐ FAIL | |
| TC-B8 工具超时信封 | timeout | ☐ PASS / ☐ FAIL | |
| TC-B9 tool_search 懒加载 | control | ☐ PASS / ☐ FAIL | |
| TC-B10 workspace 沙箱 | file + D3 | ☐ PASS / ☐ FAIL | 安全项 |
| TC-B11 agent_parallel | companion v1 | ☐ PASS / ☐ FAIL / ☐ SKIP | |
| TC-B12 注册健全+冲突保护 | WI-T4.1 | ☐ PASS / ☐ FAIL | 基础设施 |

**整体结论**: ☐ A 类全 PASS（用户用得了）+ B 类全 PASS（契约对） / ☐ 有 FAIL（需修复后复测）

---

## 附录：工具名 ↔ 自然语言触发速查（master 实际注册名）

| 工具名 | toolset | 典型触发语 | dangerous |
|---|---|---|---|
| `ppt_create` | ppt | "生成PPT/演示文稿" | 否 |
| `excel_create` | office | "做个Excel/表格" | 否 |
| `doc_create` / `doc_read` / `doc_edit` | office | "写/读/改 Word" | 否 |
| `pdf_export` | office | "导出PDF" | 否 |
| `image_ocr` | office | "识别图里的文字" | 否 |
| `generate_image` | image | "生成一张图片" | 否 |
| `file_organize` | office | "整理文件夹" | 否 |
| `web_fetch` / `web_crawl` / `web_extract_article` / `web_read_sitemap` | web | "抓取网址/爬网页" | 否 |
| `file_read` / `file_write` / `file_glob` / `file_grep` / `workspace_recall` | file | code 模式文件操作 | 否 |
| `todo_write` / `todo_complete` | todo | "记个待办/标记完成" | 否 |
| `memory_search` / `memory_add` / `memory_forget` | memory | "记住/你还记得吗/忘掉" | 否 |
| `tool_search` | control | （多被 dispatcher 自动调用） | 否 |
| `agent` / `agent_parallel` | code | code 模式递归/多 agent | 否 |
| `run_shell` | os | "运行命令" | **是** |
| `screen_capture/click/move/type/key/scroll` | computer | "帮我操作屏幕" | **是** |
| `run_browser_task` | browser | "用浏览器帮我做X" | 视实现 |
| `research_run` | research | "深入调研X" | 否 |

> 注：工具实际可见性还受 `enabled_toolsets`（按 task_type 动态选）+ `requires_env`（如缺 key 隐藏）+ `disabled_toolsets` 影响。
> master 上 **slash 命令（/命令）/ /goal skill 不存在**（仅 worktree），故本文档不含其用例。
