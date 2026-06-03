# 工具层 极其严格 手工测试文档（Tool Layer — Rigorous Manual Test）

> **状态**: v3（已纳入 opus 第 1、2 轮挑战修正 + **测试设计期发现并修复 1 个真 bug**：
>   agent_parallel/computer_use 非法 permission_category；待第 3 轮）
> **被测**: DeskPet 工具层全部生产功能 —— 全部工具 + 中间件横切（权限门 / 熔断 /
>   last-mile artifact+receipt / verify gate / 错误信封）+ 配置开关 + 已修 bug 回归 + 健壮性边角。
> **测试人立场**: 严谨测试员，**默认怀疑"它能用"**。每个 case 必须满足"判定证据"列出的**全部**
>   硬证据才 PASS；一环断 = FAIL。
>
> ## ⚠️ 测试方式铁律（违反即证据无效）
> 1. **只允许真人工操作**：windows-mcp 真 SendInput 点击 / 真键盘或剪贴板输入 / 肉眼看 UI；
>    或 CDP **驱动真实 DOM 的真点击/真键入**（走完整 React→WS→backend 栈）+ 截图取证。
>    **绝不** `ws.send`/HTTP 直连 backend 注入当 UI 证据。
> 2. **❌ 不允许**：pytest / `last_mile_smoke.py` / 脚本回放 / Python import 查 registry/store
>    内部状态 当"功能可用"证据。**例外（允许）**：grep 后端**行为日志**（工具 dispatch / receipt /
>    breaker / verify 事件，带 timestamp）—— 这是"真实运行栈的实际行为"，非 import 内省（见 §证据等级）。
> 3. **每动作前 declare**：`坐标=(x,y)[物理像素] | 动作 | 期望`。
> 4. **DPI/WebView2 坑**：多屏 DPI 缩放（webview dpr 异常）；物理像素；`SetProcessDpiAwareness(2)`+
>    **SendInput**（WebView2 不响应老式 mouse_event）；中文走 `[Clipboard]::SetText`+Ctrl+V。
> 5. **失败 retry ≥3 种 workaround** 才标"环境受限"；跳过 case 须显式声明 + 等用户确认。
>
> ## 证据等级（每个 case 满足其"判定证据"列出的全部）
> - **L1 产物落盘**：文件真存在 + 大小>0 + **真打开内容正确**（非空壳）。
> - **L2 UI 渲染**：ArtifactCard/ToolResultCard/权限弹窗/错误卡 肉眼可见且内容对。
> - **L3 行为日志**：工具 dispatch / receipt / breaker / verify 事件（真实运行栈行为日志，**非** import 内省）。
> - **L4 反向证据**：该发生的发生 **且** 不该发生的没发生（disabled 工具 0 dispatch、cancel 后 0 落盘）。

---

## 0. 前置准备（每轮开始执行一次）

0.1 **干净启动 + 跑源码**：杀残留 → 既定 env（`DESKPET_BACKEND_DIR=backend`+`DESKPET_PYTHON=<venv>`+CDP 9222）
   启动 → 日志确认 `[backend_launch] Dev python=...backend_dir=<repo>/backend`（否则跑旧 frozen exe = 白测）。
0.2 **登录**：onboarding 登录（`LOCAL-DEV-CREDENTIALS.md`，gitignored）→ key 入 keychain →
   日志 `llm_config_updated base_url=https://chinzy.com/v1`。**截图前关 onboarding 窗**（防泄密）。
0.3 **进 Code 模式**：开测试项目（如 `G:\projects\test-research-helper`）→ tile idle。code 模式才注入 `_project_root`。
0.4 **日志通道**：tail tauri dev 重定向 log（backend structlog → stderr → Stdio::inherit）。

### 0.5 ★★★ 配置前置矩阵（关键！多个 TC 依赖；不设对会**集体假 FAIL**）
工具层多项功能默认 **OFF**（出厂字节级一致）。**测前按下表把 dev `config.toml` 设好并重启**，每组测完恢复基线：

| 要测的能力 | 必需 config（`backend/userdata/config.toml`，dev gitignored） | 默认 | 不设的后果 |
|---|---|---|---|
| ArtifactCard 渲染（TC-10~13,23,30） | `[tools.last_mile]` `artifact_envelope=true` **且** `frontend_artifact_card=true` | 均 false | **无 ArtifactCard**，只回落纯文本/路径 → 误判 FAIL |
| receipt 落盘（TC-3/7/10 的 receipt 子项, TC-B-receipt） | `[tools.verifier]` `emit_receipts=true` | false | **0 receipt** → 误判 FAIL |
| verify gate（TC-25） | `[tools.verifier]` `verify_gate_mode=shadow\|strict` **且** `emit_receipts=true` | off/false | mode≠off 但 emit=false → **启动抛 VG-INVARIANT-1 ConfigError**（`config.py:481`）→ 起不来 |
| 熔断（TC-25） | `[supervisor] enabled=true`（默认 true）即接电；threshold/cooldown 默认 3/60，可经 `[supervisor] circuit_breaker_threshold`/`circuit_breaker_cooldown_seconds` 调（main.py:1729-1739）→ 日志 `p5s2_circuit_breaker_wired threshold=3 cooldown=60s` | true | supervisor 关 → breaker 不接电 → 测不到 |
| agent_parallel（TC-17） | `[features] agent_parallel=true` | false | flag off → 工具不注册 → LLM 串行绕道 → 误判 FAIL |
| 自然语言 memory_forget（TC-18③） | `[memory.v2.forget] enable_natural_language=true` | false | 自然语言 forget 默认拒绝 |

> 注：本批主测建议基线设 `artifact_envelope=true`+`frontend_artifact_card=true`+`emit_receipts=true`+
> `agent_parallel=true`+`supervisor.enabled=true`（verify_gate_mode 单独在 TC-25 切），覆盖最广。**测完恢复出厂默认**。

---

# 第 1 部分 — 工具触发矩阵（每个工具逐一真触发）

> ★ **重要前提修正（R1-W5）**：生产代码 `_agent.run()` **未传 tools_filter** → 所有 toolset 的工具
> **在 companion 和 code 模式都暴露给 LLM**。两模式真正差异 = **`_project_root` 注入**（仅 code 模式）：
> file/shell/glob/grep 的 handler 需 `_project_root` 才能正确解析相对路径；companion 模式触发会因缺 root
> 报路径错/降级（**不是"工具不可用 0 dispatch"**）。因此下表"模式"列指"能正常工作的模式"，非"是否暴露"。
> 触发方式：在 chat 输入框真输入自然语言，**禁止手敲工具名/JSON**。工具真名以
> `backend/deskpet/tools/registry.py` + 各 `*_tools.py` 注册为准（测前 grep 核对防漂移）。

## TC-1 read_file（toolset=os）— code 模式
- 操作：code tile 输入 `读一下项目根目录的 README.md 前 20 行`（无则先 TC-3 建）。
- 证据：L2 聊天出现**真实**文件内容片段（与磁盘核对）+ L3 `read_file` dispatch（args.path = 真实 project_root 下路径）。
- FAIL：报"不存在"但文件在 / 内容是 LLM 编的（与真实不符）。

## TC-2 list_directory（os）— code
- 操作：`列出项目根目录有哪些文件`。
- 证据：L2 列表**抽查 2-3 个真实文件名**存在 + L3 `list_directory` dispatch（注意 max_entries=100 截断，大目录不逐条对）。

## TC-3 write_file（os，**非 dangerous → 橙色 warning 弹窗**，R1-W2）
- 操作：`在项目根目录创建 RIGOR_T3.txt，写入一行：hello tool layer`。
- 期望：permission_category=write_file → 弹**橙色 warning 权限弹窗**（非红色）→ 允许 → 写入。
- 证据：**L1** `RIGOR_T3.txt` 真存在 + 内容精确 = `hello tool layer` + **L2** 橙色权限弹窗显示真实 path
   + **L3** `write_file` dispatch（+ receipt 若 emit_receipts=true）。**测后删**。

## TC-4 edit_file（os，非 dangerous，write_file category）— code
- 操作：续 TC-3，`把 RIGOR_T3.txt 里的 hello 改成 HELLO`。
- 证据：L1 内容变 `HELLO tool layer`（**仅该处改**）+ L3 `edit_file` dispatch。FAIL：整文件重写/改错处。**测后删**。

## TC-5 glob（code）— code
- 操作：`找出项目里所有 .py 文件`。
- 证据：L2 抽查 2 个路径真实存在 + L3 `glob` dispatch（注意按 mtime 排序）。

## TC-6 grep（code）— code
- 操作：`在项目里搜索包含 "def " 的行`。
- 证据：L2 抽查一条命中去文件核对行号 + L3 `grep` dispatch。

## TC-7 run_shell（os，**dangerous=True → 红色 error 弹窗**，R1-W2）
- 操作：`运行 echo hello-shell 命令`。
- 期望：permission_category=shell → 弹**红色 error 权限弹窗** → 允许 → 回显 `hello-shell`。
- 证据：L2 红色弹窗 + 输出含 `hello-shell` + L3 `run_shell` dispatch + 权限事件。
- ★ 危险命令（R1-NM5，重要）：deskpet **不加沙箱**。`rm -rf`/`删除项目目录` 默认**只弹一次 shell 权限窗，
   点允许就真删**。要测拦截须先配 `[permissions] shell_deny_patterns=["rm -rf", ...]`。
   **本 case 验**：配 deny pattern 后触发 `rm -rf` → 被 deny 拦（不弹窗/直接拒）；不配则记录"真执行（设计如此，非 bug）"。

## TC-8 web_fetch（web，network category）— both
- 操作：`抓取 https://example.com 的内容`。
- 证据：L2 含 example.com 真实文案（"Example Domain"）+ L3 `web_fetch` dispatch。FAIL：返回臆造内容。

## TC-8b web_extract_article / web_crawl / web_read_sitemap（web，R1-M1 补）— both
- 操作：分别 `提取这篇文章正文 <某新闻URL>` / `爬取 <站点> 的页面` / `读取 <站点>/sitemap.xml`。
- 证据：各自 L3 dispatch（`web_extract_article`/`web_crawl`/`web_read_sitemap`）+ L2 结果合理。

## TC-9 web_search（code，network）— both
- 操作：`搜索 2026 年最新的 Python 版本`。
- 证据：L2 真实可点链接 + L3 `web_search` dispatch。（R2-B5：DuckDuckGo，**无需 env key**，失败只会是网络问题）

## TC-10 ppt_create（toolset=ppt，write_file category，artifact）— both
- **前置**：`artifact_envelope=true`+`frontend_artifact_card=true`（否则无卡，R1-WK3）。
- 操作：`生成一个介绍人工智能的 3 页 PPT`。
- 证据：**L1** .pptx 真落盘（路径 = `default_artifact_dir/<日期>/<tool>/...` 或 tempdir）+ **PowerPoint/WPS 真打开有 3 页**
   + **L2** ArtifactCard（含文件名 + 打开/定位）+ **L3** `ppt_create` dispatch + artifacts 键（+ receipt 若 emit）。
- FAIL：卡显示但文件空壳/0 字节/打不开。

## TC-11 doc_create（toolset=office，artifact，★回归点）— both
- 前置：同 TC-10 flag。
- 操作：`生成一份团队周报 Word，含标题和三段正文`。
- 证据：L1 .docx 真打开 **有标题+三段正文（非空）** + L2 ArtifactCard + L3 `doc_create` dispatch。
- ★★**回归（曾 bug：element 格式 {heading,level} vs {type,text} 不匹配 → 空文档**，doc_tools.py 已加归一）：
   **必须确认文档非空**，这是本 case 核心。另测 `doc_read`/`doc_edit`（office）。

## TC-12 excel_create（office，artifact）— both
- 前置：同上。操作：`生成含 3 行销售数据的 Excel`。
- 证据：L1 .xlsx 真打开有数据 + L2 ArtifactCard + L3 `excel_create` dispatch。

## TC-13 pdf_export（office，artifact）/ TC-14 generate_image（image，network，能力依赖）— both
- 前置：同上。pdf：`导出一个 PDF 说明文档`；image：`画一只戴帽子的猫`。
- 证据：L1 文件落盘 + L2 卡渲染。R2-B5：**generate_image 用与 chat 同一套 LLM creds（登录即可用，无需额外 env）**
   → dev 已登录应**真出图**；"graceful refuse"仅在 key 失效时（返带 hint 的金黄卡，不崩）。

## TC-15 todo_write（**toolset=code**，被 code_tools 覆盖，R1-W7）— code
- 操作：`做一个三步计划：先看代码、再改、最后测`。
- 证据：L2 tile todos 区 3 条 + 状态随执行更新 + L3 `todo_write` dispatch。（禁它需 `disabled_toolsets=["code"]`）

## TC-16 agent（code）/ TC-17 agent_parallel（**control**，需 `[features] agent_parallel=true`，R1-M7）— code
- TC-16：`用一个子代理分析项目目录结构并总结` → L3 `agent` dispatch + 子 session。
- TC-17 前置：`agent_parallel=true` 重启 + 日志确认工具注册。操作：`同时用多个子代理分别统计 py 和 md 文件数`
   → L3 见 **2 个并行 subagent**（hub-and-spoke）+ L2 两结果汇总。

## TC-18 memory_write/search/read/forget（memory）— both
- 操作：① `记住我最喜欢的颜色是钴蓝色`(write) ② 新轮 `我最喜欢什么颜色?`(search/read) ③ `忘掉我喜欢的颜色`(forget)
   ④ 再问。
- 证据：① L3 memory_write + L1 记忆落盘 ② L2 答"钴蓝色"+L3 命中 ③ **前置 `enable_natural_language=true`**
   （R1-WK6，否则自然语言 forget 默认拒）→ L3 forget ④ **L4 反向**：forget 后召不回。

## TC-19 tool_search / fetch_tool_result（control，R1-M5）— code
- 操作：`搜索可用工具里有没有处理图片的`。
- 证据：L3 `tool_search` dispatch + L2 候选列出（含 env-gated 工具如需 key 的，提示配 env）；长结果走 `fetch_tool_result`。

## TC-20 desktop_create_file（os，desktop_write category，R1-M1 补）— both
- 操作：`在桌面创建一个 hello-desktop.txt`。
- 期望：弹 desktop_write 橙色弹窗 → 允许 → 桌面真出现该文件。
- 证据：L1 桌面文件存在 + L2 橙窗 + L3 `desktop_create_file` dispatch。**测后删**。

## TC-21 image_ocr（office，能力门控）/ office_pick_file（office，原生对话框）/ file_organize / run_browser_task / skill_invoke / research_run（R1-M1 补）— both
- image_ocr：`识别这张图片里的文字 <图片>` → 有 OCR 引擎则返文字；无则 graceful refuse
   （`{ok:false,error:"ocr_engine_missing",message:"OCR 组件(RapidOCR)不可用..."}` —— R2-B5 注意：用 **message 字段非 hint**
   → **不触发金黄描边卡**，别期望金黄边）。
- office_pick_file：`让我选一个文件` → **弹原生 Windows 文件对话框**（纯前端可见特性）。
- file_organize / run_browser_task（toolset=e2e）/ skill_invoke（control）/ research_run（web）：各触发一次，L3 dispatch + 合理结果。
- 证据：各 L3 dispatch；office_pick_file 须**肉眼见原生对话框**。

## TC-22 file_* 双套工具说明（R2-B4 定稿）
- `file_read/file_write/file_glob/file_grep/workspace_recall`（toolset=`file`）与 os 的 read_file/write_file 等**并存**，
   两套都暴露给 LLM（生产未传 tools_filter）。**R2 结论：实际多 dispatch os 套**（read_file/write_file）——
   `os_tools/read_file.py:14` 注释明示"code agent 实调 os 套的 read_file"；file_* 套主要服务 workspace_recall。
   选择由 **LLM + schema description 驱动**，非注册顺序。
- 操作：companion 模式触发文件读 → 看 L3 dispatch 实际工具名。
- 证据：L3 记录实际工具名（**预期 read_file/write_file**）；若 dispatch file_* 也记录，注明两套关系。

---

# 第 2 部分 — 中间件横切特性

## TC-23 权限门 — 按 category 上色（R1-W2 修正）— code
- 操作：分别触发 run_shell（shell=红）、write_file（write_file=橙）、desktop_create_file（desktop_write=橙）。
- 期望：弹窗颜色由 **permission_category** 决定（`PermissionPopup.tsx CATEGORY_META`）：
   shell/read_file_sensitive/skill_install = **红 error**；write_file/desktop_write/network = **橙 warning**。
   弹窗显示真实参数 + 顶部 `4px solid accent` 边框色。
- 证据：L2 颜色与 category 对应正确 + 显示真实参数 + **L4** 点[拒绝]→工具**不执行**（文件不创建）；[允许]→执行。

## TC-24 权限门 — auto-mode 自动放行（R1-NM4）— code
- 操作：开 permission auto-mode → 触发 write_file/run_shell。
- 期望：auto-mode ON → 不弹窗直接执行。
- 证据：L2 **无弹窗** + L1 操作完成（L3 auto 决策日志若有则记，gate.py auto 路径不一定打 log）。**测后关 auto-mode**。

## TC-25 熔断 ToolCircuitBreaker（R1-W6 修正：3 次连续/60s/需 supervisor）— code
- **前置**：`[supervisor] enabled=true` + 日志见 `p5s2_circuit_breaker_wired threshold=3 cooldown=60s`（否则 SKIP+声明）。
- 操作：对**同一工具连续 3 次失败**（中间无该工具成功），如连读不存在文件 `读取 /no/such/path_xyz`×3。
- 期望：第 3 次后熔断 OPEN → 后续该工具调用返 `{ok:false,error:"circuit_open"}` envelope，
   hint 精确含 **"连续失败 3 次已熔断"** + `available_alternatives`（同 toolset 兄弟工具名）。
- 证据：**L2** 错误卡含"连续失败 3 次已熔断" + **列出 ≥1 个兄弟工具名**（LLM 编不出）+ L3 circuit_open envelope。
- 注：half-open 60s 冷却恢复**由单测覆盖**（手测难稳定复现，不强求）。

## TC-26 last-mile ArtifactCard 多产物（R1-WK3 前置）— both
- 前置：`artifact_envelope=true`+`frontend_artifact_card=true`。操作：`生成 PPT 和对应的 Excel 数据表`。
- 证据：L2 **多张 ArtifactCard**（"N 个产物"）+ 每卡可打开/定位 + L1 各产物文件真存在。

## TC-27 错误信封 + hint 金黄高亮（R2-B2 定稿：用 read_file ENOENT 触发）— code
- **操作（首选稳定触发）**：`读取一个明显不存在的绝对路径，比如 G:\no\such\file_xyz.txt`
   → `read_file` 返 `{ok:false, hint:"... 不存在。请先用 list_directory ..."}`（read_file ENOENT，os_tools 全系填 hint）。
   其它带 hint 例：write_file 缺 content / 已存在不加 overwrite / content>4096。
- 期望：ToolResultCard **金黄描边 + 💡有修复建议**（`MessageBubble.tsx:305`：仅当返回 JSON 含**非空 hint string** 才触发）。
- 证据：L2 金黄描边 + 显示 hint 文案 + L3 工具返 ok=false（带 hint 字段）。
- ★ FAIL 陷阱：**纯 dispatch 异常**走 `registry.py:537` 的 `{"error":...,"retriable":...}` **无 hint 字段 → 无金黄边**。
   别用这类错误测本 case（会误判）。务必用 read_file ENOENT 这种 handler 主动填 hint 的错误。

## TC-28 verify gate（off/shadow/strict，R1-WK2 前置）— code
- **前置铁律**：mode≠off **必须**同时 `emit_receipts=true`，否则启动抛 VG-INVARIANT-1 ConfigError（起不来）。
- off：触发"声称做完"任务 → 直接完成（基线）。
- shadow：同任务 → 完成（不拦），日志精确出现 `verify_gate shadow: N unmatched claims (would block in strict)`（verify_gate.py:311）。
- strict：验**不误杀真任务** — 真建文件任务 → 完成、**无** `verify_gate_nudge_injected`；日志 `verify_gate_init mode=strict`。
- 证据：shadow L3 上述原文 + 任务完成；strict L3 mode=strict + 真任务不被拦。★真"拦 fake"由单测覆盖。**测后恢复基线**。

---

# 第 3 部分 — 配置开关行为（改 config=prep；验证看 UI/产物/日志行为）

> 每开关：改 config.toml → 重启 → UI 真触发 → 验证差异 → **恢复基线**。L4 用 dispatch 计数（行为日志，非 import 内省，合规）。

## TC-29 disabled_toolsets 双层门控（R1-W1 修正 + ★回归）— code
- 操作：`[tools] disabled_toolsets = ["office"]`（挡 ppt/doc/excel/pdf/file_organize/ocr/picker）→ 重启 → `生成一个 Excel`。
- 期望：office toolset 工具**既不在 LLM schema、调用也被后端拦**（双层）→ LLM 绕道/说不能。
- 证据：**L4** `excel_create` **0 次 dispatch** + L2 无 .xlsx 产物。
- ★ 回归（曾 `_load_tools` 漏读 disabled_toolsets → 配了不生效）：确认禁用**真生效**。**测后移除**。

## TC-30 dangerous_tools_allowlist（R1：dangerous 工具仅 run_shell + computer_use 系）— code
- 操作：`dangerous_tools_allowlist = ["run_shell"]`（非空）→ 重启 → 触发 run_shell（白名单内，可用）+
   一个不在白名单的 dangerous 工具（如 computer_use 的 screen_click，若可触发）。
- 期望：仅白名单内 dangerous 工具可用；其余 dangerous 被挡。
- 证据：L4 run_shell 可用、白名单外 dangerous 被挡。**测后清空**。

## TC-31 default_timeout_seconds — ★实测对内置工具无效（R1-W3）— code
- 操作：`default_timeout_seconds = 2` → 重启 → `运行 sleep 10`。
- 期望（修正）：**run_shell 仍按自身 timeout（300s）**，`default_timeout_seconds` **只在工具未配 timeout（=0）时兜底**，
   对所有内置工具（都有非零 timeout_seconds）**无效**。
- 证据：记录"sleep 10 未被 2s 截断 = 设计如此（潜在设计陷阱，建议记 issue）"。要测真超时须触发超过工具自身 timeout 的操作。**测后恢复**。

## TC-32 strict_unknown_toolset — ★已确认无消费点 = 潜在 bug（R2-B1 坐实）— 配置
- **R2 结论**：全 backend grep，`strict_unknown_toolset` 仅在 `config.py:281/291/457` 声明+读取，
   **无任何启动期 fail-fast 校验消费它** → disabled_toolsets 含未知 toolset 名时既不报错也不 warn，静默忽略。
- 操作：`disabled_toolsets = ["typoset"]` + `strict_unknown_toolset = true` → 启动。
- 期望：**静默忽略**（启动正常、无报错、无 warn）—— 这是潜在 bug（flag 声明了但未接电）。
- 证据：L3 启动日志**无**未知 toolset 报错/warn。**记 issue：strict_unknown_toolset 未实现校验**。**测后恢复**。

## TC-33 artifact_envelope ON/OFF 信封控制（R1-WK3）— both
- 操作：`[tools.last_mile] artifact_envelope = false` → 重启 → 触发 ppt_create。
- 期望：OFF → tool_result **不含 artifacts 键** → 前端**回落纯文本/路径**（无 ArtifactCard，这也是出厂默认行为）。
- 证据：L2 OFF 时无 ArtifactCard、ON 时有。**测后恢复 ON**（本批基线）。

---

# 第 4 部分 — 健壮性 / 边角 / 安全 / 疑似 bug

## TC-34 ★ 回归：非法 permission_category bug 已修（R2-B3 坐实真 bug + 本次已修）— code
- **背景（测试设计期发现的真 bug，已修）**：agent_parallel 曾用 `execute_command`、computer_use(screen_*) 曾用
   `shell_exec`，均**不在 gate 8 类合法集** → `gate.check`(gate.py:194) 在 auto-mode 短路**之前** raise ValueError
   → 被 execute_tool except 吞 → 工具**100% 不执行**（会话不崩但功能哑火）。同 `skill_tools.py:107` P0 bug fix #8 漏网者。
- **修复**：agent_parallel `execute_command`→`read_file`（对齐单 agent；子代理内部工具各自 gate）；
   computer_use 5×`shell_exec`→`shell`。回归 223 passed。
- **回归验证**：gate 接电 + `features.agent_parallel=true` + code 模式 → 触发 agent_parallel(TC-17) →
   **不再出现** `ValueError: unknown permission category` + 真执行(2 子代理并行出结果)；screen_click 触发→红弹窗(shell)→允许→真执行。
- 证据：L3 **无** ValueError/permission category 异常 + L4 工具真执行。FAIL(=回归)：又见 ValueError / 被吞不执行。

## TC-35 工具失败优雅处理（不崩会话）— code
- 操作：连续触发多种失败（不存在文件 / 无权限路径 / 畸形参数）。
- 证据：L2 每次错误卡 + 之后能正常发下一条 + 桌宠不崩/无"启动失败"弹窗 + 状态回 idle。

## TC-36 写入域 / 路径越界（write_scope，非沙箱，R1-NM5）— code/companion
- 操作：① code 模式 `在 C:\Windows\System32 写个文件` ② companion 模式触发写盘。
- 期望：写盘**倾向**限制在 workspace/project_root；越界**可能**被 write_scope 拦或重定向（**但 deskpet 不加沙箱**，
   只防手滑级）。记录实际行为。
- 证据：L4 System32 下是否真有新文件 + L2 说明。**不预设"必拦"**（按 write_scope_enforced 实际配置）。

## TC-37 大输出截断 — code
- 操作：`读取一个很大的文件` 或 grep 海量命中。
- 证据：L2 ToolResultCard 显示截断提示（"N 行—点击展开"）+ 可展开 + 不卡死/不 OOM。

## TC-38 并发工具（多 tile session 隔离）— code
- 操作：仪表盘开 2 个项目 tile，同时各发工具任务。
- 证据：L4 各 tile 产物落各自项目（不串台）+ L2 两 tile 状态独立 + 并发上限提示。

## TC-39 receipt 落盘 + HMAC + duration（R1-M3 补，需 emit_receipts=true）— code
- 前置：`emit_receipts=true`。操作：触发任一工具（如 write_file）。
- 证据：L3/L1 receipt 文件存在 + **duration_ms 非 0**（曾 bug：两次 now() → ~0，registry.py:812 已修，回归点）+ HMAC 签名字段存在。

## TC-40 工具注册健全 / name 冲突（R1-M6）— 启动
- 操作：看启动日志。
- 期望：无 `ToolNameConflictError`；`web_fetch re-registered ... replace_allowed` warning 出现且不致命；
   工具总数合理（disabled/allowlist 过滤后）。
- 证据：L3 启动无注册 ERROR/冲突；`skill_loader_ready count=N`。

---

## 测试结果汇总表（执行时填）

| TC | 工具/特性 | 前置flag | L1 | L2 | L3 | L4 | 判定 | 截图 |
|----|----------|---------|----|----|----|----|------|------|
| TC-1 read_file | — | | | — | | | |
| ...（TC-1~TC-40 全列）... | | | | | | | | |

（每行填证据要点 + PASS/FAIL/RETRY-N/SKIP；FAIL 必附现象+日志；SKIP 必附环境受限理由+等用户确认）

## 附录 A — 真实工具名核对表（测前 grep 核对，R1-W4 修正）
| 类别 | 真实注册名 | toolset |
|---|---|---|
| 文件 | read_file / write_file / edit_file / list_directory / desktop_create_file | os |
| 文件(另套) | file_read / file_write / file_glob / file_grep / workspace_recall | file |
| 代码 | glob / grep / web_search / fetch_tool_result / todo_write / agent | code |
| shell | run_shell（dangerous=True） | os |
| web | web_fetch / web_extract_article / web_crawl / web_read_sitemap / research_run | web |
| 产物 | ppt_create(ppt) / doc_create,doc_read,doc_edit,excel_create,pdf_export,file_organize,image_ocr,office_pick_file(office) / generate_image(image) | — |
| 记忆 | memory_write / memory_read / memory_search / memory_forget | memory |
| 控制 | agent_parallel / skill_invoke / tool_search | control |
| 屏幕 | screen_capture / screen_click / screen_move / screen_type / screen_key / screen_scroll（部分 dangerous） | computer_use |
| 浏览器 | run_browser_task | e2e |
| todo | todo_complete | todo |
> **以代码实际为准**：测前 `grep -rn "register" backend/deskpet/tools/` 核对，防工具名漂移。

## 附录 B — 模式与可用性（R1-W5 修正）
- **所有工具在 companion + code 都暴露给 LLM**（生产未传 tools_filter）。
- 真差异 = **`_project_root` 注入**（仅 code 模式）：file/shell/glob/grep 需它才能解析相对路径；
  companion 触发这些工具会因缺 root **报路径错/降级**（非"0 dispatch"）。
- 产物类/web/memory/tool_search 在两模式都能正常用。

## 附录 C — dangerous / permission_category 速查（R2 修正）
- **dangerous=True**（红色 error 弹窗候选）：run_shell（shell）、computer_use 的 **screen_click/screen_type/screen_key**
   （R2-NEW3：screen_move/screen_scroll **非** dangerous）。
- permission_category → 弹窗色（PermissionPopup.tsx CATEGORY_META）：shell/read_file_sensitive/skill_install=**红 error**；
   write_file/desktop_write/network=**橙 warning**。
- ★ 非法 category bug **已修**（TC-34）：agent_parallel `execute_command`→`read_file`；computer_use `shell_exec`→`shell`。
