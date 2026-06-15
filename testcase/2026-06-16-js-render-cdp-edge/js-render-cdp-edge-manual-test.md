# JS 渲染兜底（Option C：cdp-edge 引擎）手工测试用例

> **被测功能**: deep-research 抓取链路新增的 **JS 渲染兜底**（Option C / `cdp-edge` 引擎）。
> trafilatura 抓到的正文过短（`< _JINA_MIN_CHARS=300` 字）**且**原始 HTML `> _JS_RENDER_MIN_SHELL_HTML=20000`
> 字节（疑被 JS 藏内容的富页空壳）时，连**系统已装 Edge 无头（CDP）**渲染页面拿 JS 跑完后的
> `outerHTML`，再 trafilatura 抽正文。本地渲染排在 **jina（国外、需代理）之前**，中国可直连。
> **Windows 落地，纯后端**（无跨层、无 GUI 依赖）。
>
> **对应 commit**:
> - `a520ef7` feat(deep-research): JS 渲染抓取兜底 — CDP-系统Edge 引擎(Option C, Windows 落地)
> - `cd15844` fix(deep-research): 补 Option C 评估 3 缺口 — 渲染失败保活常驻浏览器 + ai_generated 渲染后扫描 + eval 超时单测
>
> **被测代码**:
> - `backend/deskpet/tools/research_cdp_edge.py`（新，适配器）— `cdp_edge_render` / `cdp_edge_available` / `shutdown_cdp_edge`；
>   单例常驻无头 Edge + `websockets` 连 CDP `Runtime.evaluate` 取 `outerHTML`；`asyncio.Semaphore(2)` 页级限流。
> - `backend/deskpet/tools/research_tools.py` — `default_extract` 接入双闸（L797~812）、`_js_render_dispatch`（L213）路由、
>   `_js_render_enabled/_js_render_engine/_js_render_timeout`（L174~198）、`_reset_js_render_budget`（L207，`research_run` 开头归零，L995）。
> - 配置（`[research]` 段）: `js_render`（默认 **false**，opt-in）/ `js_render_engine` / `js_render_timeout`（默认 20s）。
>   dev 运行配置在 `backend/userdata/config.toml`；出厂文档样例在 `config.toml`。
>
> **★ 关键事实（影响多条 TC 判定，先读）**:
> 1. **真实默认开关**来自代码 `_research_raw().get("js_render", False)`，不是 toml 文件。toml 写值是给用户看的文档/或 dev 覆盖。
> 2. **`webview` 引擎本期未实现**：`_js_render_dispatch` 命中 `webview` 时 `log.debug("js_render engine=webview 本期未实现,跳过")` 并返 None → 自动降级。
>    所以「engine 设非 Windows 能用的引擎」这条优雅降级用例，直接用 `js_render_engine="webview"` 即可稳定复现，**无需破坏 Edge 路径**。
> 3. **未配置 engine 时的默认**：`_js_render_engine()` 在 **Windows 返回 `cdp-edge`**（已落地），非 Win 返回 `webview`（本期降级）。
>    —— 注意这与 toml 文档里写的 `js_render_engine="webview"` 不一致；toml 文档是给三端主线写的，本期 Windows 真实可用引擎是 cdp-edge。
> 4. **报告里命中源的 `extractor` 字段** = `_js_render_engine()` 的返回值（即 `cdp-edge`），由 `default_extract` 在替换正文时写入（L811）。
> 5. **日志锚点**（structlog → stderr → tauri dev log）:
>    - 成功: `event="cdp_edge_render" url=<URL> chars=<int> ms=<int> ok=True`
>    - 失败/超时: `event="cdp_edge_render" url=<URL> ok=False error=<str>`
>    （emit 处 `research_cdp_edge.py:406 / 418`）
>
> **最后更新**: 2026-06-16

---

## 0. 测试前置

| 项 | 要求 |
|---|---|
| **跑的是 worktree 代码** ★ | 按项目 CLAUDE.md「坑 #8」，给 **Tauri 进程**注入 `DESKPET_BACKEND_DIR=<worktree>/backend` + `DESKPET_PYTHON=<worktree .venv python.exe>`；启动日志必须出现 `[backend_launch] Dev python=... backend_dir=<worktree>`；若看到 `[backend_launch] Bundled exe=...` 说明跑的是旧冻结 exe（**无本期改动 → 测了等于白测**，假性 FAIL，先修环境再测）。 |
| **不要手动起 backend / 双起 vite** ★ | 坑 #7/#9：Tauri 自己 spawn backend（占 `DESKPET_BACKEND_PORT`）+ 自跑 `beforeDevCommand` 起唯一 vite。**只跑 `npx tauri dev`**（带上面 env），别另手动 `python main.py` / `npm run dev:relay`。 |
| 系统 Edge | Windows 10/11 自带 Microsoft Edge（`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`）。`cdp_edge_available()` 靠它返回 True。若缺 Edge → 所有渲染降级（属环境，记录）。 |
| 桌宠运行 | onboarding 已登录、LLM 链路可用（`research_run` 要 LLM 拆子问题 + synth）。 |
| 网络 | 能直连目标 JS 站。Clash/系统代理在场时注意：`_render_once` 内 `Page.navigate` 经代理可能慢（代码已注释，超时也继续轮询兜底）。 |
| 后端日志 | 能抓 tauri dev 重定向日志（backend structlog 走 stderr → `Stdio::inherit()`）。grep 锚点见上「关键事实 5」。 |
| 报告落盘 | `OutPut/Research/<最新>.md`（research_run 自动落盘 .md，末尾含引用附录）。 |
| 触发路由 | 用「**深度调研 / 出一份调研报告**」这类词触发 `research_run`；「查一下」只触发 `web_search`，**不**走本特性。 |
| 截图存档 | `plans/manual-results-2026-06-16-js-render/screenshots/<case-id>.png`；日志证据 grep 片段贴报告。 |

> ⚠️ **windows-mcp 真测纪律**（见 `CLAUDE.md`）：每个 UI 用例必须**真模拟点击 / 粘贴 + 截图 + 抓后端日志**，
> **不能**用 pytest / `import research_cdp_edge` 查内部状态 / WebSocket 注入 / `python research_cdp_edge.py` 直跑当 UI 证据。
> 每个动作前先 declare：`坐标=(x,y) | 动作=click/type | 期望=...`。

### 中文输入 / 点击 workaround（按优先级 retry，每例失败 ≥3 次不同 workaround 才标「环境受限」）

- **点击**：① PowerShell `[Win]::SetCursorPos(x,y)` + `SendInput`（LEFTDOWN/UP，WebView2 圣杯，老式 `mouse_event` 对 Chromium 不响应）② windows-mcp `Click(label=...)` 用 Snapshot 出的 label ③ `App switch` 先聚焦窗口再 click。
- **中文输入**：STA Runspace + `[System.Windows.Forms.Clipboard]::SetText("中文")` + 先 Click 输入框聚焦 → Ctrl+V → 验后端 log 真收到了消息（不收到就说明粘贴落错窗口，retry）。

### 触发渲染的「稳定命中」主题选择（关键）

双闸苛刻（trafilatura 短 + 原始 HTML > 20KB），普通静态站/小页不触发。**用例须选会命中 JS/SPA 富页空壳的调研主题**。
POC 已验证有效的真实 JS 站（`plans/2026-06-16-crawl4ai-fetch-tier/00-PLAN.md` §WI-(-1)）：
- `quotes.toscrape.com/js/`（JS 金标准，httpx 29 字空壳 → 渲染后 1071 字）
- `book.douban.com/latest`（**真实中文 JS 站**，httpx 44 字空壳 → 渲染后 1244 字）

实战调研主题里能命中此类站的措辞示例（让 plan 拆出的子问题去搜到 SPA 富页）：
- 「**豆瓣 2024 年最新出版的新书书单**」（命中 book.douban.com 类 JS 站）
- 某「**前端框架/JS 重渲染产品官网的功能介绍**」（SPA 着陆页常是空壳）

> 📌 **触发不了时怎么排查**（贯穿所有 happy-path 用例）：
> 1. grep 日志有无 `cdp_edge_render` 任何行。**完全没有** = 双闸没过 → 进一步判：
>    - 是 trafilatura 没短（命中的站正文够长）→ 换更「空壳」的 SPA 主题；
>    - 还是原始 HTML < 20KB（小页）→ 双闸①未过，换富页大站；
>    - 还是 `js_render` 没真开（查启动日志 + `backend/userdata/config.toml`）。
> 2. 有 `cdp_edge_render ok=False` = 渲染触发了但失败 → 看 `error=` 字段（Edge 没装 / navigate 超时 / CDP 断）。
> 3. 触发计数已达 `_JS_RENDER_MAX_PER_RUN=4` → 后续空壳源不再触发（TC-05 专测此项，非 bug）。

---

## TC-01 — Happy path：开 cdp-edge，JS 空壳站被渲染救回（招牌真机链）★

**目的**：开 `js_render`（cdp-edge）后，深度调研一个会命中 JS 渲染站的主题 → 后端日志出现
`cdp_edge_render ok=True chars>0`，报告某源 `extractor=cdp-edge`，且报告正文含该 JS 站内容（原抓空壳本会丢源）。

**前置配置**：`backend/userdata/config.toml` 的 `[research]` 段设 `js_render = true`（无段则新增），
`js_render_engine` 不设（Windows 默认即 cdp-edge）或显式 `js_render_engine = "cdp-edge"`。改后**重启桌宠**使生效。

| 步骤 | 动作（declare 坐标/动作/期望） | 期望结果 |
|---|---|---|
| 1 | Screenshot 桌宠主界面 | 桌宠在线、聊天输入框可见 |
| 2 | declare `坐标=(输入框) \| 动作=Click+粘贴 \| 期望=消息发出`：Click 输入框 → Clipboard 设 `深度调研一下"豆瓣 2024 年最新出版的新书有哪些值得读"，出一份带来源的报告` → Ctrl+V → Enter | 消息发出；桌宠进入「调研中」 |
| 3 | Wait 调研完成（30s~数分钟，deep 档可能久） | 桌宠贴出报告，顶部有「调研覆盖 X 个来源」 |
| 4 | grep 后端日志 `cdp_edge_render` | 至少一行 `event="cdp_edge_render" ... ok=True chars=<N>`，`N` 明显 > 原 httpx 空壳长度（如 >300） |
| 5 | 打开 `OutPut/Research/<最新>.md`，看引用附录 / 正文 | 报告引用含该 JS 站域名（如 `book.douban.com`），且**正文引用了该站渲染后才有的内容**（不是空壳标题） |

**可观测证据**：
- 日志锚点：`event="cdp_edge_render" url=https://book.douban.com/... chars=1200+ ms=<int> ok=True`
- 报告 `extractor` 字段：内部 dict 写 `extractor="cdp-edge"`（报告 md 不一定直显 extractor；以**该源正文从空壳变实质内容**为肉眼判据 + 日志 chars 增长为硬据）。

**PASS 判据**：步骤 4 grep 到 ≥1 条 `cdp_edge_render ok=True chars>300` **且** 步骤 5 报告含该 JS 站的实质正文（非空壳）。
**FAIL 判据**：日志无任何 `cdp_edge_render`（双闸没过，按上「触发不了排查」换主题 retry ≥3 次）；或全是 `ok=False`（渲染触发但失败，记 `error=`）；或报告里该站仍是空壳/被丢。

---

## TC-02 — 开关关闭（flag-off）：同主题不触发渲染，回落原行为 ★

**目的**：`js_render=false`（出厂默认）时，同一主题日志**无** `cdp_edge_render`，抓取回落原 trafilatura/jina 行为，
`research_run` 正常出报告（字节级行为不变）。

**前置配置**：`backend/userdata/config.toml` `[research]` 设 `js_render = false`（或删除该行恢复默认关）。重启桌宠。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 同 TC-01 步骤 2，粘贴**完全相同**的豆瓣新书主题 → Enter | 消息发出 |
| 2 | Wait 调研完成 | 桌宠正常贴出报告（不崩、不挂死） |
| 3 | grep 后端日志 `cdp_edge_render` | **零行**（开关关 → 双闸前的 `_js_render_enabled()` 即 False，根本不进 dispatch） |
| 4 | 打开最新报告 | 报告正常生成、有引用；该 JS 站可能因空壳被丢或正文很短（这是 flag-off 的预期退化，非 bug） |

**PASS 判据**：步骤 3 日志**无任何** `cdp_edge_render` 行，**且** 报告仍正常生成（research_run 不因开关关而异常）。
**FAIL 判据**：关了开关却仍 grep 到 `cdp_edge_render`（开关失效，对照 commit `2b7baa1` 同类 bug —— 检查走的是 `_research_raw()` 而非 `_cfg.config.raw`）；或报告生成失败。

---

## TC-03 — 优雅降级·引擎缺失（engine=webview 本期未实现）★

**目的**：`js_render=true` 但 `js_render_engine="webview"`（本期未实现）→ `_js_render_dispatch` 返 None，
降级到 jina/原结果，`research_run` 不挂死、不超 300s。**这是「引擎不可用」最稳定的复现方式**（无需破坏 Edge 路径）。

**前置配置**：`[research]` 设 `js_render = true` **且** `js_render_engine = "webview"`。重启桌宠。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 同 TC-01 主题粘贴 → Enter | 消息发出 |
| 2 | Wait 调研完成 | 报告正常出（不挂死） |
| 3 | grep 后端日志 `cdp_edge_render` | **零行**（引擎是 webview，根本不进 cdp-edge 路径） |
| 4 | grep 后端日志 `engine=webview 本期未实现` | 出现该 debug 行（证明双闸过了、进了 dispatch 但 webview 路径降级返 None）— 注意 debug 级别可能被日志级别过滤，**非 PASS 必需，作辅证** |
| 5 | 抓 research_run wall-clock | 全程未触发 `research_run` 300s tool_timeout（不挂死） |

**PASS 判据**：报告正常生成 + 无 `cdp_edge_render` 行 + research_run 未超时。
**FAIL 判据**：webview 引擎下竟出现 `cdp_edge_render`（路由错）；或 research_run 挂死/超时崩。

> 📌 **备选复现（engine 路径搞坏）workaround**：若想测「cdp-edge 引擎下 Edge 找不到」的降级，可临时把 `js_render_engine="cdp-edge"`
> 但在 dev 机重命名/移走 `msedge.exe`（**测后务必还原**）→ `cdp_edge_available()=False` → `_ensure_browser` 抛
> `edge executable not found` → `cdp_edge_render` 捕获返 None，日志 `cdp_edge_render ok=False error=edge executable not found`。
> 此法侵入系统，**仅在 webview 路径不足以说服时用，且必须还原 Edge**。优先用 engine=webview 的无损法。

---

## TC-04 — 双闸不误触发（普通静态站为主的主题）★

**目的**：`js_render=true` 时，调研一个以**纯文字静态新闻/小页**为主的主题 → 双闸（trafilatura 短 + HTML>20KB）
不同时满足 → 不触发渲染，不拖慢。验证「正常站不被误渲染」。

**前置配置**：`[research]` `js_render = true`、engine = cdp-edge。重启桌宠。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | declare 后粘贴一个**静态文字**主题：`深度调研"宋朝的茶文化历史"，出一份报告`（文化类，命中的多为静态长文页） | 消息发出 |
| 2 | Wait 完成 | 报告正常出 |
| 3 | grep `cdp_edge_render` | **零行**或**极少**（命中的静态站 trafilatura 抽得到长正文 → 双闸①`len(text)<300` 不满足 → 不触发）；即便偶有触发也应 `ok=True` 且正文未被替换（渲染后正文不更长） |
| 4 | 对比 TC-01 wall-clock | 整轮耗时不显著高于无渲染（没被误触发拖慢） |

**PASS 判据**：步骤 3 无 `cdp_edge_render`（或触发数远少于 TC-01 的空壳主题，且未拖慢整轮）。
**FAIL 判据**：静态文字主题大量触发 `cdp_edge_render`（双闸①失守 / 阈值判断有 bug）→ 检查 `len(text) < _JINA_MIN_CHARS` 与 `len(html) > _JS_RENDER_MIN_SHELL_HTML` 逻辑。

> 📌 **排查**：静态站若 HTML 也 >20KB（带大量广告/脚本但正文长），双闸①靠 `len(text)<300` 把关 → 正文长就不触发。
> 若发现误触发，重点看是不是某站 trafilatura 抽空（正文<300）但实际是静态站（该走 jina 而非渲染）—— 这是边界，记录站点 URL。

### TC-04.1（边界·观察性子项）— 渲染 `ok=True` 但抽出正文**不比原正文长** → **不替换**正文（预期行为，**别误判成 bug**）

**背景/被测逻辑**：`default_extract` 替换正文有**长度闸**——代码 `if len(r_text) > len(text):` 才替换（`research_tools.py` L809~811）。
即：本地渲染**成功**（`cdp_edge_render ok=True`、`r_text` 非空），但渲染抽出的正文 `r_text` 长度**≤** trafilatura 原正文 `text` 长度时，**不替换**：
该源 `extractor` 仍为 `trafilatura`，报告正文**保持原 trafilatura 结果**（不会被更短的渲染结果覆盖）。
**这是设计预期**——只在「渲染拿到的正文更长（更可能是被 JS 藏住的真内容）」时才替换，避免渲染回来个更短/更差的版本反而把好正文盖掉。

> ⚠️ **真机难精确制造**：是否命中此分支取决于具体站点（要恰好 trafilatura 已抽到不短的正文、且渲染结果反而不更长）。
> 故本子项标为 **观察性子项 / 边界说明**，**不强制主动复现**；重点是让测试者**知道这是预期行为**，
> 一旦在 TC-01/04/10 的日志里看到「`cdp_edge_render ok=True` 但该源报告 `extractor` 仍是 `trafilatura`、正文没换成渲染结果」，
> **应判 PASS（符合长度闸预期），切勿误报为「渲染成功却没替换正文」的 bug**。

| 观察点 | 期望（PASS） | 误判警示（不是 bug） |
|---|---|---|
| 某源日志 `cdp_edge_render ok=True chars=<N>` 但 `N ≤` 该源 trafilatura 原正文长度 | 该源 `extractor` 仍 `trafilatura`、报告正文是原 trafilatura 文本（未被渲染结果覆盖） | 「渲染都 ok=True 了正文却没换」← 这是 L809 长度闸的预期，**不是** bug |
| 某源日志 `cdp_edge_render ok=True chars=<N>` 且 `N >` 原正文长度 | 才替换：`extractor=cdp-edge`、正文换成渲染结果（即 TC-01 happy path） | — |

**判据（若真观察到此分支）**：`ok=True` 但 `chars ≤` 原正文 → `extractor` 仍 `trafilatura` + 正文未被更短渲染结果覆盖 → **PASS（预期）**。
**FAIL（真 bug）**：`ok=True` 且渲染正文**确实更长**（`> 原正文`）却**没**替换（仍 trafilatura）→ 长度闸/替换逻辑反了，查 L809~811。

---

## TC-05 — 触发计数上限（≤4 次/research，护超时预算）

**目的**：一次 deep 调研命中**多个**空壳源时，渲染触发次数 ≤ `_JS_RENDER_MAX_PER_RUN=4`，超出的空壳源走 jina/原结果（护 300s）。

**前置配置**：`[research]` `js_render = true`、engine = cdp-edge。重启桌宠。
选一个能拉出**≥5 个 JS 空壳候选源**的主题（多个 SPA 站，如「多个豆瓣/SPA 榜单类」综合主题）。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 粘贴能命中多个 JS 空壳源的综合主题 → Enter | 消息发出 |
| 2 | Wait 完成 | 报告出 |
| 3 | grep 后端日志 `cdp_edge_render` 行数（统计 ok=True+ok=False 总触发） | **总触发次数 ≤ 4**（即便候选空壳源 >4，第 5 个起 `_js_render_run_count < 4` 不成立 → 不进 dispatch） |
| 4 | 确认 `_reset_js_render_budget` 跨轮归零：再发**第二次**同主题调研 → grep 第二轮 `cdp_edge_render` | 第二轮计数**重新**从 0 起（每次 research_run 开头归零，L995），不是累加（不会因为第一轮跑满 4 次就第二轮一次都不渲染） |

**PASS 判据**：单轮 `cdp_edge_render` 行数 ≤ 4；第二轮独立重新计数（跨轮归零生效）。
**FAIL 判据**：单轮 >4 次（计数上限失守）；或第二轮一次都不触发（`_reset_js_render_budget` 没在 research_run 开头调，全局计数没归零）。

> 📌 **统计 workaround**：deep 档候选源数受 plan/网络影响，难精确控「正好 >4 个空壳」。若一轮凑不够 5 个空壳源，
> 此例降级为「观察到的触发次数从不超过 4」即可（上限的负向不易主动撑满，重点验**不越界** + **跨轮归零**）。

---

## TC-06 — 单页渲染超时降级（js_render_timeout）

**目的**：单页渲染超过 `js_render_timeout` → 该源 `cdp_edge_render` 返 None（`ok=False`），降级到 jina/原结果，不影响整轮、不挂死 research_run。

**前置配置**：`[research]` `js_render = true`、engine = cdp-edge、**`js_render_timeout = 2`**（故意设极短，逼超时）。重启桌宠。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 粘贴 TC-01 同 JS 站主题（豆瓣新书）→ Enter | 消息发出 |
| 2 | Wait 完成 | 报告正常出（不崩） |
| 3 | grep `cdp_edge_render` | 出现 `ok=False`（`error` 多为超时/`empty html`，因 2s 不够渲完 SPA）；`ms` 应 ≈ 2000 上下 |
| 4 | 看报告该源 | 该 JS 站正文**未被渲染结果替换**（extractor 仍 trafilatura 或回落 jina）；整体报告仍生成 |
| 5 | research_run wall-clock | 未触发 300s 超时 |

**PASS 判据**：步骤 3 出现 `cdp_edge_render ok=False`（超时），步骤 4 该源优雅回落，整轮不崩不超时。
**FAIL 判据**：超时把整轮抓取/`research_run` 拖崩或挂死；或 timeout 配置不生效（`ms` 远超设定值且不降级）。
**测后**：把 `js_render_timeout` 改回默认 20（或删行）+ 重启。

---

## TC-07 — 去重：本地渲染命中后不再走 jina ★

**目的**：本地渲染（cdp-edge）命中并替换正文后，该源**不再**触发 jina 二级兜底（plan R6 去重，避免一个空壳站连跑两个重型兜底使超时翻倍）。

**前置配置**：`[research]` `js_render = true`、engine = cdp-edge、**`jina_reader = true`**（把 jina 也打开，才能验证「渲染命中后 jina 没被调」）。重启桌宠。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 粘贴 TC-01 JS 站主题 → Enter | 消息发出 |
| 2 | Wait 完成 | 报告出 |
| 3 | grep `cdp_edge_render` | 该 JS 源 `ok=True chars>300`（渲染命中） |
| 4 | 看该源的 extractor / 报告引用 | 命中源 `extractor=cdp-edge`（**不是** jina）；该 URL 没有 jina 抓取的痕迹 |
| 5 | grep 后端日志 jina 相关（`r.jina.ai` / jina extract） | **同一个被渲染命中的 URL 没有走 jina**（代码 L815：`extractor == "trafilatura"` 才进 jina 分支；渲染命中后 extractor 已变 cdp-edge → 跳过 jina） |

**可观测证据**：代码逻辑 L813~820 —— 仅当 `extractor == "trafilatura"`（即本地渲染**未**命中）才试 jina。渲染命中后 `extractor=cdp-edge` → jina 分支被跳过。
**PASS 判据**：渲染命中的源 `extractor=cdp-edge` 且该 URL 无 jina 调用记录。
**FAIL 判据**：同一 URL 既出现 `cdp_edge_render ok=True` 又走了 jina（去重失守，双重兜底叠加）。

---

## TC-08 — 边界·关桌宠重开后开关生效（配置持久）

**目的**：改 `[research].js_render` 后必须重启才生效（无热重载）；验证关→重启→开→重启两态都对，证明开关随重启稳定生效。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 关桌宠（`taskkill /F /IM deskpet.exe` + 清 Vite，按坑 #1）| 进程清干净（无 orphan） |
| 2 | `config.toml` 设 `js_render = false` → 重启（带 worktree env）| 上线；日志确认 `[backend_launch] Dev python=...` |
| 3 | 跑 TC-01 主题 → grep `cdp_edge_render` | **零行**（关态生效） |
| 4 | 再关桌宠 → 设 `js_render = true` → 重启 | 上线 |
| 5 | 跑同主题 → grep `cdp_edge_render` | 出现 `ok=True`（开态生效） |

**PASS 判据**：关态零渲染、开态有渲染，两次重启各自生效。
**FAIL 判据**：重启后开关态没切换（配置没读到 / 缓存了旧值）。

---

## TC-09 — 边界·深度档（300s）叠加渲染不超时 ★

**目的**：deep 档（research_run 工具上限 300s，commit `ee38fc4`）+ js_render on 的一次完整真机跑，
**不**触发 300s tool_timeout（验证渲染没顶穿超时预算 —— Semaphore(2) 限流 + 计数 ≤4 + 单页 timeout 共同护住）。

**前置配置**：`[research]` `js_render = true`、engine = cdp-edge、`js_render_timeout = 20`（默认）。重启。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 粘贴一个**深度**（多子问题、含多个 JS 空壳源）的主题 → Enter | 消息发出 |
| 2 | Wait 完成（计时 wall-clock）| 报告完整出 |
| 3 | grep 后端日志 research_run 超时 / `tool_timeout` / 300s | **无** research_run 300s 超时记录 |
| 4 | 统计渲染：grep `cdp_edge_render` 行数 + 各 `ms` | 触发 ≤4 次；首页冷启可能 10~17s（含起 Edge），后续热复用应明显更短（单例常驻复用生效，非每页冷启） |

**可观测证据**：单例常驻 Edge —— `cd15844` 修复「渲染失败保活常驻浏览器」，所以第 2+ 个 URL 不应每次冷启 10s+。
**PASS 判据**：整轮 wall-clock 内 research_run 未超 300s，报告完整。
**FAIL 判据**：deep 档叠加渲染顶穿 300s（丢源 / 报告残缺）→ 查是否每页冷启（单例没复用）/ 计数没限住 / Semaphore 失效。

---

## TC-10 — 边界·中文 JS 站能渲染（豆瓣类，中国直连）

**目的**：验证 cdp-edge 对**中文 JS 站**（POC 已证 `book.douban.com/latest`：httpx 44 字 → 渲染后 1244 字）能拿到中文正文，
中国直连有效（不依赖国外 jina），且无中文乱码（`outerHTML` 经 trafilatura 抽出中文正常）。

**前置配置**：`[research]` `js_render = true`、engine = cdp-edge。重启。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 粘贴明确命中中文 JS 站的主题：`深度调研"豆瓣读书 2024 最新书单推荐"，出报告` | 消息发出 |
| 2 | Wait 完成 | 报告出 |
| 3 | grep `cdp_edge_render` 命中 `book.douban.com` 类域名 | `ok=True chars>1000`（中文正文长度合理） |
| 4 | 打开报告该源正文 | 中文正文**可读、无乱码、含真实书单内容**（不是「登录/加载中」空壳，也不是 mojibake） |

**PASS 判据**：中文 JS 站 `cdp_edge_render ok=True chars>1000` 且报告该源中文正文可读无乱码。
**FAIL 判据**：中文站渲染返空 / 乱码 / 仍是空壳。

> 📌 **排查**：豆瓣可能有反爬/登录墙 → 若 `ok=False` 看 `error`；若渲染到「登录提示页」也算空壳（chars 偏小）。
> 可换其它中文 SPA 富页主题 retry ≥3 次再判环境受限。中国直连：本特性走本地 Edge，**不**经国外服务，代理掐连不影响（除非代理拦截 navigate）。

---

## TC-11 — 渲染失败「保活常驻浏览器」：失败一次后下个源秒复用同一 Edge（★招牌·`cd15844` 唯一真机手段）

**目的**：验证本期**最关键修复**（commit `cd15844`，`research_cdp_edge.py:408-419`）—— `cdp_edge_render` **单次渲染失败**（页面级 `ok=False`，如超时/empty html）时**不再**杀常驻 Edge，
只在**连接级致命**（进程死 / ws 断，`_browser_running()` 返 False）才 `_reset_browser_state()`。
单测里 `_browser_running` 被 mock 成 True，**真实保活行为只能靠真机验证**（评估子代理明确标注此为 live 兜底盲区）。
**判据核心**：同一轮调研里，一次渲染**失败后**，**紧接着**的下一个 JS 源 `cdp_edge_render ok=True` 的 `ms` **明显小**（热复用、无冷启 10~17s）→ 证明那次失败**没把常驻 Edge 杀掉**。

**前置配置**：`[research]` `js_render = true`、engine = cdp-edge、**`js_render_timeout = 2`**（沿用 TC-06：极短超时逼第一个 SPA 源渲染超时 `ok=False`）。
选一个能**先后命中 ≥2 个 JS 空壳源**的主题（让第一个超时失败、后面还有 JS 源继续渲染），如「**豆瓣 2024 最新书单 + 某 SPA 榜单**」综合主题，或同主题但拆出多个 JS 子源。重启桌宠。

> ⚠️ **关键观测窗**：本例不靠单次 PASS，靠**同一轮内多次 `cdp_edge_render` 的 `ms` 序列对比**。务必把整轮所有 `cdp_edge_render` 行（含 `ok` / `ms` / `url`）按时间顺序抓全。

| 步骤 | 动作（declare 坐标/动作/期望） | 期望结果 |
|---|---|---|
| 1 | 真测前先 `tasklist | findstr msedge` 记下基线 msedge 进程数 | 记录基线（常驻 Edge 起来后应是稳定的少量 msedge 进程，非每渲染一个） |
| 2 | declare `坐标=(输入框) \| 动作=Click+粘贴 \| 期望=消息发出`：Click 输入框 → Clipboard 设能命中**多个**JS 空壳源的综合主题（如 `深度调研"豆瓣 2024 最新书单 + 其它 SPA 榜单的新书推荐"，出一份带来源的报告`）→ Ctrl+V → Enter | 消息发出；进入「调研中」 |
| 3 | Wait 调研完成（deep 档可能数分钟） | 报告完整出（不崩、不挂死） |
| 4 | grep 后端日志**全部** `cdp_edge_render` 行，按时间序排：抓 `ok` / `ms` / `url` | 至少出现**一次** `ok=False`（被 2s 超时逼出，多为 `error=`超时/`empty html`），**且其后**至少出现**一次** `ok=True` |
| 5 | 比对该 `ok=False` **之后**的 `ok=True` 行的 `ms` | 失败之后那次（们）渲染 `ms` **明显小**（热复用，应 < 一次冷启的 10~17s，典型几百 ms ~ 数秒级）→ 证明常驻 Edge 在失败后仍存活被复用 |
| 6 | 整轮中再 `tasklist | findstr msedge` | msedge 进程数**稳定**、**不随每次渲染暴增**（常驻单例，不是失败一次就杀光重起一批） |
| 7 | research_run wall-clock | 未触发 300s 超时；报告完整不丢源 |

**可观测证据（失败→复用证据链）**：
- 日志锚点序列示例（同一轮、时间递增）：
  ```
  event="cdp_edge_render" url=https://<SPA-A> ms≈2000 ok=False error=<超时/empty html>   ← 第一个源被 2s 超时逼失败
  event="cdp_edge_render" url=https://book.douban.com/... ms=<远小于10000，如 600~3000> ok=True chars>300   ← 紧接着的源秒复用同一常驻 Edge
  ```
- 链条逻辑：若 `cd15844` 修复在真机生效 → 失败那次走的是**页面级**返 None（`_browser_running()` 仍 True，不 reset）→ 浏览器留着 → 下个源**热复用**（无冷启）→ `ms` 小。

**PASS 判据**：同一轮内观测到 `ok=False` **之后**紧跟的 `ok=True` 渲染 `ms` 明显小于一次冷启（无 10~17s 冷启代价）**且** msedge 进程数稳定不暴增 → 失败保活生效。
**FAIL 判据（缺口①修复在真机失效）**：失败之后的**每次**渲染 `ms` 都 >10s（每次都在冷启）→ 说明那次失败把常驻 Edge 杀了 / 没复用；或 msedge 进程随每次渲染被杀光重起（reset 误触发）；或失败直接把整轮抓取拖崩。

> 📌 **排查**：
> 1. **凑不出「失败后还有 JS 源」** → 把综合主题里 JS 空壳源排在多个位置，或调 timeout 让中间某个源失败而非最后一个；最少要 1 次 `ok=False` + 其后 1 次 `ok=True`。
> 2. **没有任何 `ok=True`（全失败）** → 2s 对所有源都太短，无法对比冷热；适当放宽 timeout 到刚好够热渲染但仍逼第一个慢源超时，或换更快的热复用站。
> 3. **「下次渲染快」是 `_browser_running` 仍 True 的间接证据**：本特性不暴露 `_browser_running` 给 UI/日志，**热复用的 `ms` 小就是它没被杀的可观测代理**；勿用 `import` 直接查 `_browser_running` 当证据（违反真测纪律，那只证代码加载、不证运行栈行为）。
> 4. **冷启基线参照**：TC-09 已记录「首页冷启 10~17s（含起 Edge）」；TC-11 的失败后复用 `ms` 应显著低于该冷启基线。
> 5. **务必做**：这是验证 `cd15844`「渲染失败保活常驻浏览器」修复的**唯一真机手段**，单测 mock 了 `_browser_running` 覆盖不到，不可跳过。

---

## 结果汇总表

| 用例 | 被测点 | 关键配置 | windows-mcp | 日志锚点 | 判定 |
|---|---|---|---|---|---|
| TC-01 | Happy path：JS 空壳被渲染救回 | `js_render=true`, cdp-edge | 是（UI + 报告 + 日志） | `cdp_edge_render ok=True chars>300` | ✅ PASS（4 站 90K-254K 字进报告） |
| TC-02 | flag-off：同主题不触发、回落原行为 | `js_render=false` | 是 | **无** `cdp_edge_render` | ✅ PASS（0 渲染，报告正常） |
| TC-03 | 优雅降级·引擎缺失（webview 未实现） | `js_render=true`, engine=webview | 是 | 无 cdp_edge_render | ✅ PASS（0 渲染+报告生成+无超时） |
| TC-04 | 双闸不误触发（静态文字主题） | `js_render=true`, cdp-edge | 是 | 无/极少 `cdp_edge_render` | ✅ PASS（只渲真空壳 SPA，静态长文未误触发） |
| TC-04.1 | 边界·渲染 ok=True 但正文不更长 → 不替换（观察性） | `js_render=true`, cdp-edge | 观察性 | extractor 仍 trafilatura | ✅（单测覆盖） |
| TC-05 | 触发计数上限 ≤4 + 跨轮归零 | `js_render=true`, cdp-edge | 是 | `cdp_edge_render` 行数 ≤4 | ✅ PASS（TC-01 正好 4 次） |
| TC-06 | 单页渲染超时降级 | `js_render_timeout=2` | 是 | `cdp_edge_render ok=False`（超时） | ✅ PASS（ok=False+报告正常+无挂死） |
| TC-07 | 去重：渲染命中后不走 jina | `js_render=true`+`jina_reader=true` | 是 | 命中源无 jina | ✅ PASS（3 渲染命中，r.jina.ai 调用 0） |
| TC-08 | 关桌宠重开后开关生效 | 关/开两态 + 重启 | 是 | 关态零渲染 / 开态有渲染 | ✅ PASS（开 4/关 0） |
| TC-09 | deep 档（300s）叠加渲染不超时 | deep 主题, `js_render=true` | 是（wall-clock） | research_run 无 300s 超时 | ✅ PASS（deep 完整出报告，0 超时） |
| TC-10 | 中文 JS 站能渲染（豆瓣类） | `js_render=true`, cdp-edge | 是 | `cdp_edge_render ok=True chars>1000` | ✅ PASS（douban 90232 字无乱码） |
| TC-11 | ★招牌·渲染失败保活（`cd15844` 唯一真机手段） | `js_render=true`, cdp-edge, `timeout=2` | 是（进程数+ms） | 失败后 msedge 稳定不被杀 | ✅ PASS（ok=False 后 msedge 稳定 10 未归零） |

> **用例计数**：共 **11 条主用例**（TC-01 ~ TC-11）+ **1 条观察性边界子项**（TC-04.1）= 12 条。
>
> **执行结果**: **2026-06-16 windows-mcp 真机 11/11 PASS，0 bug**（TC-04.1 观察项由单测覆盖）。
> 完整证据见 [plans/manual-results-2026-06-16-js-render/RESULTS.md](../../plans/manual-results-2026-06-16-js-render/RESULTS.md)
> （日志锚点 `cdp_edge_render` + 报告引用核对 + msedge 进程数保活证据；`logs/` 存各 TC 日志）。
> ⚠️ 真测确认启动日志 `[backend_launch] Dev python=... backend_dir=<worktree>`（跑 worktree 代码，非冻结 exe）。
