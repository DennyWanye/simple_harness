# JS 渲染抓取层 — 实施 PLAN（首选 Tauri WebView，Crawl4AI 降 dev 高级档）

> **状态**: 📋 规划中（三轮评审 + WebView POC PASS + 三端最佳实践拍板；第 3 轮已暴露 Tauri-WebView 主线的
> **反向跨层链路**风险并把全部 WI 对齐到新主线，详见 §3.0 / R7 / 文末第 3 轮记录）
> **目标**: 给 deep-research 抓取链路加一级**真浏览器渲染**兜底，治 trafilatura/Jina 都拿不下的
> JS/SPA 站。**主线实现 = 复用 Tauri 自带 WebView（三端零体积零安装，见 §2.5）**；Crawl4AI（本
> plan 原始诉求）降为 dev/高级档，CDP-系统Edge 为 Windows 可选加速档。
> **历史**: 本 plan 始于"接 Crawl4AI"，经 POC + 三端兼容评估后**主线转向 Tauri WebView**；文件夹名
> 保留 `crawl4ai-fetch-tier` 仅为历史连续性，**真正主线见 §2.5**。
> **关键来源**: Crawl4AI https://github.com/unclecode/crawl4ai ；Tauri WebView `eval_with_callback`。
> **最后更新**: 2026-06-16

> ⚠️ **评审关键前置（务必先读）**：本仓库**已存在** `backend/deskpet/tools/browser_use_tool.py`
> —— 一套 browser-use + Playwright + Chromium 的浏览器自动化工具，已经把 Crawl4AI 要面对的
> 几乎所有难题（**懒 import / flag-off 零开销 / 后台线程 + `asyncio.run` 避开 backend 事件循环 /
> 首次使用才下 Chromium / 长任务 job 化**）解过一遍。**本 plan 不应另起炉灶，必须复用它的成熟模式**。
> 同时本仓库已有 `model_provisioner.py`（首启下载基建），但它**走自建 COS 直下、明确放弃了
> hf-mirror 等第三方镜像**（实测不稳）—— 这直接推翻本 plan 早期"PLAYWRIGHT_DOWNLOAD_HOST 指
> 国内镜像"的乐观假设。详见文末评审记录与下方各章已就地修订处。

> 🛑 **第 3 轮评审揪出的最大未填洞 —— Tauri-WebView 主线的「反向跨层链路」（必须先读 §3.0）**：
> §2.5 把主线定为「用 Tauri 自带 WebView 渲染 JS 页」，但**渲染发生在 Rust/前端侧的 WebView，
> 而要用渲染结果的 `default_extract` 在 Python 后端**。正常数据流是「前端→后端」（前端是 HTTP/WS
> client，后端是 server）。本方案需要的是**反方向**：「后端 Python 主动叫前端/Rust 去渲染一个 URL
> 并把渲染后 HTML 拿回来」。**读完真实代码确认：当前进程拓扑里这条反向链路完全不存在，且不能
> 一跳直达**——
> - **Rust↔backend 是单向的**：`process_manager.rs` 只 spawn backend + 读它 stdout 的 `SHARED_SECRET`
>   然后静默 drain，**Rust 侧没有任何"接收 backend 指令"的入口**（`lib.rs::invoke_handler` 全是
>   前端→Rust 的 command）。所以后端**不能**直接命令 Rust 去开 webview。
> - **能渲染的只有前端**：开/控离屏 webview 要走 Tauri JS API（`WebviewWindow`，`withGlobalTauri:true`
>   已开），这是**前端 JS** 的能力，不是后端 Python 的。
> - 因此真实链路是**四跳环路**：`后端(WS server) →push 渲染请求→ 前端(WS client) →Tauri JS→ 开离屏
>   webview 导航+eval 取 HTML →前端→ 经 HTTP/WS 回传后端 →喂 default_extract`。
>   control-WS（`/ws/control`，后端 server / 前端 client，已能 server→client push，见 `ControlChannel.ts`）
>   是**唯一可复用**的反向投递基础设施，但它现在**没有"渲染请求/HTML 回传"这对消息**，要新建一整套
>   请求-应答协议（含 requestId 关联、超时、并发、桌宠未开窗/control-WS 未连时的降级）。
> - **复杂度诚实评级：高**。这是把一个"纯后端的抓取兜底"改造成"跨 后端↔前端↔WebView 三层、依赖
>   GUI 在线 的异步请求-应答系统"。**这是本 plan 当前最大的、被 §2.5 一句"写一小块 Rust"严重低估
>   的风险**。WI-0 必须把这条链路当**头号可行性 GATE** 实测，不能闷头进 WI-1。详见 §3.0 + R7 + §10 F3。

---

## 1. 背景与目标

deep-research 现有抓取链路（`research_tools.default_extract`）是**两级降级**：

```
httpx 直抓 HTML → trafilatura 抽正文(SOTA,但只读静态 HTML)
   ↓ 正文 < 300 字(疑似 JS 空壳) 且 [research].jina_reader=on
r.jina.ai 二级兜底(JS 渲染,但国外服务,中国大陆需代理)
```

**缺口**：现代 SPA / JS 重渲染站，原始 HTML 是空壳，trafilatura 抽不到；Jina 是国外站，
裸中国用户连不上。→ 这类站在调研里**直接丢源**，报告退化（真机查特斯拉时 9 源含多个空壳/垃圾页）。

**本 plan 目标**：引入 **Crawl4AI** 作为**本地真浏览器渲染**抓取层 —— 用 Playwright 跑完页面 JS
再抽正文，中国可用（本地渲染不依赖国外服务），输出干净 Markdown 直接喂 synth。

---

## 2. 核心约束与关键决策（先定，否则方案跑偏）

| 约束 | 决策 |
|---|---|
| **体积**：Crawl4AI 底层 Playwright + Chromium ≈ **100~160MB**，与 NSIS 瘦包定位冲突 | **绝不进默认安装包**，且**不进 PyInstaller spec 的 `hiddenimports`/`collect_all`**（见下 ★frozen 约束）。复用 [`nsis-model-externalization`](../2026-06-05-nsis-model-externalization/PLAN.md) + `model_provisioner.py` 的「首启外置下载」模式：基础包不含 Chromium，用户**显式开启**后首次使用时下载 |
| **★ frozen backend 不可 import（一轮新增 — 硬约束）**：生产 backend 是 PyInstaller 冻结 exe（`backend/deskpet-backend.spec`），**没有 site-packages**，未列入 `hiddenimports` 的包**根本 import 不到**。spec 现在**没有** `crawl4ai`/`playwright`/`browser_use`。 | **接受并写明**：crawl4ai 在**冻结生产包里直接不可用**（`crawl4ai_available()` 永远 False → 自动降级）。它**只在 dev / 源码运行模式**（`DESKPET_BACKEND_DIR` 指向 worktree、跑 `.venv` python）下可用。**若要让正式用户用上**，需另起一项工作：把 crawl4ai 作为**外置 wheel + 运行时 `pip install` 到 user 目录并 `sys.path` 注入**（类似 model 外置，但对象是 Python 包不是数据文件），**本期不做，列为 follow-up（见 §10）**。WI-0 必须实测确认冻结包行为。 |
| **中国可达**：Playwright 默认从 `playwright.azureedge.net` 下 Chromium，中国慢/不稳 | ⚠️ **不要轻信第三方镜像**：本仓 `model_provisioner.py` 明确记录"hf-mirror 等第三方镜像实测不稳，已改自建 COS 直下"。**首选方案 = 复用系统已装 Chrome/Edge（CDP / channel），免下载**；下载 Chromium 仅作兜底，且 `PLAYWRIGHT_DOWNLOAD_HOST` 国内镜像可用性**列为 WI-0 必须验证项**，不可用则**走自建 COS 托管 Chromium 包**（对齐既有模型基建），不靠不可控第三方。 |
| **opt-in**：默认关（主线 webview 虽零体积也默认关，避免误触发重渲染；crawl4ai 档另避免几百 MB 吓人） | `[research].js_render = false` + `js_render_engine="webview"`（出厂默认）。注意：出厂 `config.toml` 当前**没有 `[research]` 段**，真实默认来自代码 `_research_raw().get("js_render", False)` / `.get("js_render_engine","webview")`；config.toml 里写注释是给用户看的文档，**不是**默认值来源。 |
| **降级**：浏览器没装好 / 渲染失败 / 超时 | best-effort：任何失败 → 回落现有 trafilatura/Jina 结果，绝不让抓取整体崩。对齐 `research_sources.py`「失败返空、绝不抛」先例。 |
| **不阻塞 + 超时预算（一轮强化）**：浏览器渲染慢（秒级） | 仅在 trafilatura 抽空/过短时**才触发**（和 Jina 同条件）；独立超时。**★ 关键风险**：`research_run` 工具上限已是 **300s 且 deep 档慢网区实测逼近**（commit `ee38fc4`，TC-P2-03 曾 180s 超时丢源）；`default_extract` 对所有候选 URL **并发**执行 → crawl4ai 会**同时起多个浏览器页**。必须设 **全局并发上限（信号量）** + **单次 research 内 crawl4ai 触发次数上限**，否则 deep 档叠加 crawl4ai 会直接顶穿 300s（见 R4 已升级为必决项）。 |
| **★ 浏览器实例模型（一轮：R4 从"待定"升为必决）** | crawl4ai `AsyncWebCrawler` 起浏览器开销大。决策：**进程内单例 crawler + 复用 browser context，页级并发用 `asyncio.Semaphore(≤2)` 限流**；不每 URL 新建（太慢），也不无上限并发（OOM/顶穿超时）。生命周期挂在适配器模块级，懒启动、随进程退出。 |

> **本质**：把 Crawl4AI 做成"**可选的、外置的、best-effort 的、且当前仅 dev 可用**的第三级抓取兜底"，
> 而不是默认链路。这样既拿到它的 JS 渲染能力，又不违背"单机桌宠 + 瘦包 + 中国用户"定位。
> **不要假装它在正式冻结包里能跑** —— 这是本 plan 第一轮评审揪出的最大认知偏差。

---

## 2.5 三端最佳实践决策（2026-06-16，用户拍板 — 本 plan 的真正主线）

> WI-(-1) POC 证明"系统引擎渲染 JS"能力成立后，以**「三端(Win/Mac/Linux)兼容 + 打包后用户零负担」**
> 为最高准则重排方案。**结论：JS 渲染兜底的首选实现 = 复用 Tauri 自带 WebView 开隐藏窗渲染。**

**为什么是它（决定性事实）**：DeskPet 是 Tauri 应用 —— **它能在某平台跑起来，就说明该平台的 WebView
引擎一定在**（Win=WebView2 / Mac=WKWebView / Linux=WebKitGTK，Tauri 无引擎启动不了）。所以 Tauri
WebView 是**三端唯一"保证在场 + 用户零安装 + 零额外体积"**的浏览器引擎。

| 方案 | Windows | macOS | Linux | 用户负担 | 正式包可用 | 跨平台一套代码 |
|---|---|---|---|---|---|---|
| **Tauri 自带 WebView（首选）** | ✅WebView2 | ✅WKWebView | ✅WebKitGTK | **零** | ✅ | ✅ |
| CDP-系统Edge（Win 可选加速档） | ✅ | ❌默认无 | ❌不一定 | Mac/Linux 需装浏览器 | ✅(websockets) | ❌偏科 |
| Crawl4AI/Playwright（dev 高级档） | ✅ | ✅ | ✅ | 下 ~100MB | ❌冻结包不可 | ✅但重 |

**代价全在开发侧（用户侧永远零负担）—— ⚠️ 第 3 轮修正：开发侧代价被原文严重低估，并非"一小块 Rust"**：
① **反向跨层链路（真正的大头，见 §3.0）**：不是简单"写一小块 Rust"——后端 Python 拿不到 webview，
   也命令不动 Rust（Rust↔backend 单向）。必须经 **control-WS 新建一对"渲染请求/HTML 回传"消息**，
   由**前端 JS**（不是后端）用 Tauri API 开离屏 webview、导航、eval、回传。这是**跨三层的异步请求-应答系统**，
   是本方案复杂度与风险的真正集中点。
② 三端 `eval` 小差异收进**注入的 JS**（自己 try/catch 包字符串，规避 Win 吞异常）；并且**离屏 webview
   能否对跨域外部页 eval 取 DOM**（同源策略 / WebView2 vs WKWebView vs WebKitGTK 行为差异）尚未验证，列 WI-0；
③ **安全（硬约束）**：用**隔离 webview**（不暴露 app IPC 给被抓的外部页）只跑固定抠正文脚本，**绝不复用
   桌宠主窗口**。当前 `tauri.conf.json` 的 `security.csp=null` 且 `capabilities/` 只有 `default.json`，
   **没有为"任意外部 URL webview"做任何隔离**——把不可信外部网页加载进一个默认能访问 app IPC 的 webview
   是真实安全风险，必须新增一个**不挂任何 capability**（或专门收窄 capability）的 webview，见 §3.3 + R8；
④ 常驻单例隐藏 webview 复用，顺序导航控资源；
⑤ **GUI 依赖**：渲染依赖桌宠窗口与 control-WS 在线——若用户隐藏/最小化桌宠、或 control-WS 未连，
   后端必须能优雅降级（这格在纯后端的 CDP/crawl4ai 方案里不存在）。

**三档定位（最终）**：
- 🥇 **Tauri WebView 渲染** = 跨平台首选兜底（本 plan 主线，下文 WI 以它为准）
- 🥈 **CDP-系统Edge** = Windows 可选加速档（系统 Edge 比内嵌 webview 略好控；POC 已验证；非主路）
- 🥉 **Crawl4AI** = dev / 高级用户档，不进默认包（原始诉求保留，但不是默认实现）

---

## 3. 架构与接入点

### 3.0 ★ 反向跨层链路（Tauri-WebView 主线的命门 — 第 3 轮新增，必读）

> 读了 `process_manager.rs` / `lib.rs` / `commands.rs` / `ControlChannel.ts` / `tauri.conf.json` /
> `webview_permissions.rs` 的真实代码后得出，**不是凭空推断**。

**进程拓扑实况**：

```
              spawn + 读 stdout(SHARED_SECRET) 后静默 drain stderr
   Rust ───────────────────────────────────────────────────▶ Python backend
   (deskpet.exe)        ← 单向，Rust 无 "接收 backend 指令" 入口 →   (FastAPI, WS server)
        │                                                              ▲  │
        │ 管理窗口 / WebviewWindow                                      │  │ control-WS
        ▼                                                   client连│  │push(server→client)
   离屏 WebView ◀──── Tauri JS API ──── 前端(webview 内 JS) ──────────┘  │ 已具备
   (WebView2/WKWebView/WebKitGTK)        (WS client, withGlobalTauri)  ◀──┘
```

**关键结论**：
1. **后端命令不动 Rust**。`lib.rs::invoke_handler` 注册的全是「前端→Rust」command；Rust 读 backend
   stdout 仅为拿一次性 `SHARED_SECRET`，之后只 drain，**没有回向通道**。→ "后端直接叫 Rust 开 webview" 不可行。
2. **能开/控 webview 的只有前端 JS**（Tauri `WebviewWindow` API；`message-panel`/`code-panel` 就是隐藏
   窗 + 动态 rebuild 的现成先例，见 `commands.rs::open_code_panel`、`tauri.conf.json` `"visible": false`）。
3. **后端→前端的现成投递通道 = control-WS**（`/ws/control`）：后端是 WS **server**，前端是 client，
   后端**已能主动 push**（`server_hello`/`viseme`/`milestone`）。这是**唯一可复用**的反向投递基础设施，
   **但它没有"渲染请求 / HTML 回传"这对消息**。

**因此真实数据流是一条「四跳异步请求-应答环路」**（plan 之前完全没写）：

```
default_extract 命中空壳 (Python)
  ① 后端经 control-WS push  { type:"js_render_request", payload:{ reqId, url, timeout } }
  ② 前端收到 → Tauri JS 开/复用隐藏离屏 webview → 导航 url → 等渲染 → eval 注入脚本取 outerHTML
  ③ 前端把结果回传后端：  control-WS send { type:"js_render_result", payload:{ reqId, html|error } }
     （或走一个新 HTTP 端点 POST /research/js_render_result，带 SHARED_SECRET）
  ④ 后端按 reqId 关联 future → resolve → 把 html 交给 trafilatura 抽正文
```

**这条链路必须解决的工程问题（每条都是 WI-0 的实测/设计项，不是"一小块 Rust"）**：
- **请求-应答关联**：reqId ↔ asyncio.Future 映射表；单次 research 多个 URL 并发请求的 future 管理。
- **超时与降级**：control-WS 未连接 / 桌宠窗口隐藏或最小化 / 前端无响应 → 后端等不到 result →
  必须超时回落 jina/原结果，**绝不挂死 `research_run`（已逼近 300s 上限）**。
- **并发**：前端单例离屏 webview **顺序导航**（一个 webview 同时只能在一个 URL），后端的页级
  `Semaphore` 要和前端的串行能力对齐——否则后端并发派 N 个请求，前端排队，时间线性叠加顶穿 300s。
- **后端不在 GUI 线程 / 无 webview 句柄**：所有 webview 操作必须 marshal 到前端；后端只发消息收结果。
- **生命周期**：隐藏 webview 何时建（首次请求懒建）、何时回收、桌宠退出时清理。

> **务实评估（不推翻三端方向，但把账摊清给用户）**：这条反向链路把"纯后端抓取兜底"升级成了
> "跨 后端↔前端↔WebView 三层 + 依赖 GUI 在线 的分布式请求-应答系统"。对比 **CDP-系统Edge**（POC 已
> PASS，**纯 Python 后端进程内**用 `websockets` 连本地无头 Edge，**无任何跨层、无 GUI 依赖、`websockets`
> 是纯 Python 可进 spec hiddenimports → 连冻结正式包都能用**）：在**实现复杂度、可测性、不挂死风险**三项上，
> CDP-系统Edge 明显更低。Tauri-WebView 的唯一且决定性优势是**三端引擎保证在场 + 用户零安装零体积**。
>
> **给用户的务实备选（不替你拍板，摊利弊）**：
> - **方案 A（用户已拍板，三端统一）**：全平台走 Tauri-WebView 反向链路。优点：一套代码、三端零负担；
>   代价：上述反向链路全部要建 + GUI 依赖 + 冻结包内 webview 路径仍要验证。
> - **方案 B（混合，务实落地最快）**：**Windows 先用 CDP-系统Edge**（纯后端、POC 已验证、Win10/11 必装 Edge、
>   能进冻结包），**Mac/Linux 用 Tauri-WebView**（这两端默认无 Edge，正好补位）。优点：Windows（用户主盘）
>   当期就能纯后端落地、风险最低；缺点：两套渲染路径、两套测试。
> - **方案 C（分期）**：本期先把 **CDP-系统Edge 在 Windows 落地**（小、稳、可进正式包、立刻让用户受益），
>   Tauri-WebView 三端统一 升级**作为独立下一期**（§10 F3），先做一个反向链路 spike-GATE 验证可行性再投入。
>
> 三个方案都**尊重"三端统一"为最终目标**，差别只在"是否本期一步到位 vs 先 Windows 纯后端落地再统一"。
> **建议把本节连同 R7 一起回写给用户做取舍**，再决定 WI-1 起点。下文 WI 默认按方案 A（用户拍板方向）
> 展开，并标出哪些 WI 可在方案 B/C 下省略。

### 3.1 抓取链路（升级后，三级降级 — 以 Tauri WebView 为 JS 兜底主路）

```
httpx 直抓 → trafilatura(主, 三端, 治静态站)
   ↓ 正文 < _JINA_MIN_CHARS(300) 且原始 HTML > 20KB(疑 JS 空壳, 见 WI-3 双闸)
[research].js_render=on (engine=webview) → Tauri 自带 WebView 隐藏窗渲染 → 取 outerHTML 再 trafilatura   ← 主线(三端零体积)
   ↓ (Windows 可选) js_render_engine=cdp-edge → CDP 连系统 Edge(POC 验证, 纯后端加速档)
   ↓ (dev/高级) js_render_engine=crawl4ai → Crawl4AI 渲染(不进默认包, 仅 dev)
   ↓ 仍不行 / 渲染关
[research].jina_reader=on → Jina Reader(国外, 代理)
   ↓
返回现有最好结果(哪怕短)
```

> **次序决策**：本地渲染（WebView / CDP-Edge / crawl4ai，皆中国可直连）排在 Jina（国外、需代理）**之前**；
> 三档本地渲染按"命中即停"，不叠跑（见 R6 去重）。配置开关统一为 `[research].js_render`(总开关) +
> `js_render_engine`(webview[默认] / cdp-edge / crawl4ai)。

### 3.2 代码接入点（主线 = Tauri-WebView 反向链路；crawl4ai 收编为 dev 档）

> **主线侵入面比旧 crawl4ai 方案大得多**：它横跨 Python 后端 + 前端 TS + Rust 三层（见 §3.0）。
> 旧 crawl4ai 方案的"强制复用范本 `browser_use_tool.py`"只适用于**dev 档 crawl4ai 适配器**那一格，
> 不覆盖反向链路本身。

**主线（Tauri-WebView，方案 A）改动**：

| 层 | 文件 | 改动 |
|---|---|---|
| **后端·发起+回收** | `backend/deskpet/tools/research_webview.py`（新） | `async webview_render(url, *, timeout) -> Optional[str]`：① 生成 `reqId`，建 `asyncio.Future` 存入模块级 `_pending: dict[reqId, Future]`；② 经 control-WS 向**已连接的前端**广播 `{type:"js_render_request", payload:{reqId,url,timeout}}`；③ `await asyncio.wait_for(future, timeout)`；④ 超时/无前端连接/收到 error → 返 None（best-effort）。**模块级 `asyncio.Semaphore` 控后端侧并发，且要对齐前端 webview 串行能力**。返回渲染后 `outerHTML` 字符串，由调用方喂 trafilatura。 |
| **后端·control-WS** | control-WS handler（`backend/main.py` 的 `/ws/control` + 现有 hello 分发处） | 新增入站消息分支 `js_render_result`：按 `reqId` 取 `_pending` 里的 future 并 `set_result(html)`/`set_exception`。**注意**：control-WS 可能多前端连接（main + message-panel + code-panel 各一条），渲染请求**只需任一前端处理一次**——需指定"主窗口处理"或加 claim 去重，避免多窗重复渲染同一 URL。 |
| **前端·渲染器** | `tauri-app/src/ws/`（在 control-WS onMessage 里加分支）+ 新 `webviewRenderer.ts` | 收到 `js_render_request` → 调 Tauri JS 开/复用**隔离离屏 webview**（见下 Rust/conf 改动）→ 导航 `url` → 等加载完成 → eval 注入脚本取 `document.documentElement.outerHTML`（脚本内 try/catch 包字符串，规避 Win 吞异常）→ 经 control-WS `send {type:"js_render_result",...}` 回传。失败/超时 → 回传 error。**串行处理**（单例 webview 同时只渲一个 URL）。 |
| **Rust·离屏窗（如前端 JS API 不足）** | `tauri-app/src-tauri/src/commands.rs`（可能新增 command） | 若前端 `WebviewWindow` JS API 无法满足"隔离 + 跨域 eval 取 DOM"（WI-0 验证），则下沉到 Rust：用 `WebviewWindowBuilder`+`.visible(false)` 建隔离离屏窗，导航 + `eval`/`with_webview` 取 DOM，结果经 event/command 回前端或直接回后端。**三端 eval 取 DOM 的统一封装目前不存在**（`webview_permissions.rs` 的 `with_webview` 仅 Windows 且拿 raw WebView2 COM）→ 这是 WI-0 必须算清的工作量。 |
| **安全·隔离 capability** | `tauri-app/src-tauri/capabilities/`（新 `js-render-sandbox.json`）+ `tauri.conf.json` | 给离屏渲染 webview 一个**不挂任何 app command 的窄 capability**（或显式空 capability），**绝不让被抓的外部页拿到 `invoke`/IPC**。当前 `security.csp=null` + 仅 `default.json`，必须为外部 URL 窗收窄。见 §3.3 + R8。 |
| **后端·pipeline 接入** | `research_tools.py::default_extract` | trafilatura 短 → 若 `_js_render_enabled()` 且引擎=webview → `await webview_render(url)`；取更长正文，`extractor="webview"`。**次序**：本地渲染在 jina **之前**（见 §3.1）。 |
| **后端·配置** | `research_tools.py` | 加 `_js_render_enabled()` + `_js_render_engine()`（走 `_research_raw().get(...)`，**禁止** `_cfg.config.raw`，对齐 commit `2b7baa1`；真实默认在代码 = off / `"webview"`）。 |
| **配置** | `config.toml [research]` | 加 `js_render=false` + `js_render_engine="webview"`（文档作用，真实默认在代码，见 §5）。 |

**可选/加速档 + dev 档改动（不阻塞主线，方案 B/C 下 Windows 可优先做 CDP-Edge 那行）**：

| 层 | 文件 | 改动 |
|---|---|---|
| **后端·CDP-Edge（Win 加速档，纯后端，POC 已验证）** | `backend/deskpet/tools/research_cdp_edge.py`（新） | `async cdp_edge_render(url) -> Optional[str]`：单例常驻无头 Edge（`--remote-debugging-port`）+ `websockets` 连 CDP `Runtime.evaluate` 取渲染后 `outerHTML`（POC `.tmp/webview_poc_cdp.py` 蓝本）。**纯 Python、依赖 `websockets`（可进 spec hiddenimports → 冻结正式包可用）**。`js_render_engine="cdp-edge"` 时启用。无跨层、无 GUI 依赖。**方案 B/C 下这是 Windows 的当期落地路径。** |
| **后端·crawl4ai（dev 高级档）** | `backend/deskpet/tools/research_crawl4ai.py`（新） | `async crawl4ai_extract(url, *, timeout) -> Optional[dict]`：进程内单例 `AsyncWebCrawler.arun`，模块级 `asyncio.Semaphore(2)` 限并发；best-effort 返 None；**懒 import crawl4ai**（顶层不 import）；冻结包内 import 失败 → 静默不可用。**对照 `browser_use_tool.py` 抄成熟约定**（懒 import / flag-off 零开销 / 失败不抛）。`js_render_engine="crawl4ai"` 时启用，**仅 dev**。 |
| **后端·asyncio** | （crawl4ai 档） | crawl4ai `AsyncWebCrawler.arun` 是协程可直接 `await`；WI-0 实测确认它不内部 `asyncio.run()`/关宿主 loop，冲突则退回"后台线程+独立 loop"（抄 browser_use_tool）。 |
| **provisioning** | `model_provisioner.py`（**复用，不新建目录**） | 仅 crawl4ai/chromium 档需要：首次开启下载 Chromium，走自建 COS（非镜像）。主线 Tauri-WebView **零下载**，此项主线用不到。 |
| **依赖** | `pyproject`/spec | `crawl4ai` 列**可选 extra**，不进基础依赖、**不进 spec hiddenimports**（保冻结包字节不变）；`websockets`（CDP-Edge 用）若要让正式包可用则**应进 spec hiddenimports**。 |

### 3.3 安全：把不可信外部网页关进隔离 webview（硬约束 — 第 3 轮新增）

主线要把**外部任意 URL（不可信）** 加载进一个 webview 并渲染 JS。**绝不能**复用桌宠主窗口/任何挂了
app command 的 webview——否则被抓页面里的恶意 JS 能调 `window.__TAURI__.invoke(...)` 拿到 artifact 读写、
keychain、退出 app 等能力。

**当前现状（实查）**：`tauri.conf.json` `app.security.csp = null`（无 CSP）；`capabilities/` 只有
`default.json`（给所有窗口一套默认权限）；`withGlobalTauri: true`（`window.__TAURI__` 全局可用）。
→ **没有任何针对"外部 URL 窗"的隔离**，直接加载外部页是真实漏洞。

**必须做到（Tauri v2 capability 模型）**：
- 离屏渲染 webview 用**独立 label**（如 `js-render-sandbox`），在 `capabilities/` 里**不把它列入任何
  授予 app command 的 capability**（capability 通过 `windows`/`webviews` 字段按 label 匹配；不匹配 = 拿不到该 permission）。
- 注入的抠 DOM 脚本是**我们自己固定的字符串**，不执行外部页提供的回调；只读 `outerHTML`/`innerText`，不回传任何 token。
- 验证 **`withGlobalTauri` 是否会给该窗注入 `__TAURI__`**——若会，需对该 webview 关闭全局注入或确保其无 capability 时 `invoke` 全部被拒（WI-0 实测：在隔离窗里 `invoke('app_exit')` 必须失败）。
- 收紧 CSP/导航：限制该 webview 不能反向打开新窗、不能访问 `tauri://` / `asset://` 协议。

> **R8 风险**：Tauri v2 的 capability/隔离对"动态创建的外部 URL webview"是否完全隔离 IPC，需 WI-0 用
> "隔离窗内 `invoke` 被拒"实测证实，不能假设。这也是 §3.0 务实备选里 **CDP-Edge 更省心**的一个理由——
> CDP-Edge 渲染发生在**独立无头浏览器进程**，天然不接触 app IPC，无此隔离负担。

---

## 4. 分阶段任务（WI）

### WI-(-1) WebView 渲染 POC GATE（二轮新增）→ ✅ **已跑，PASS（2026-06-16）**

**实测结论（证据 `.tmp/webview_poc_cdp.py`，系统 Edge 149 = WebView2 引擎，经 CDP 渲染）**：

| 站点 | 原始 httpx 正文 | 系统引擎渲染后正文 | 结果 |
|---|---|---|---|
| quotes.toscrape.com/**js/**（JS 金标准） | 29 字（空壳） | **1071 字** | ✅ 救回 +1042 |
| quotes.toscrape.com/（静态对照） | 1161 字 | 1161 字 | ≈ 不变（不破坏静态站） |
| book.douban.com/latest（**真实中文 JS 站**） | 44 字（空壳） | **1244 字**（豆瓣书单真内容） | ✅ 救回 +1200 |

→ **"复用系统浏览器引擎渲染 JS、零下载"路线成立**，中文站直连有效，不伤静态站。

> **★ POC 暴露出一条比 Tauri-WebView-eval 和 Crawl4AI 都更优的实现路径 —— CDP-连系统-Edge**：
> POC 用的不是 Tauri 内嵌 webview，而是**直接启动系统已装 Edge 无头 + CDP（`websockets` 库，venv 已有）**
> `Runtime.evaluate` 取渲染后 `outerHTML`。这条路同时打赢三家：
> - **vs Crawl4AI**：不下 100MB Chromium（用系统 Edge）；**且只依赖 `websockets`(纯 Python，可进 spec hiddenimports) → 正式冻结包也能用**，没有"dev-only"硬伤
> - **vs Tauri-WebView-eval**：**不用动 Rust / 不用重编译**，纯 Python 后端适配器，好建好测
> - 中国友好：本地 Edge 直连，无国外服务
>
> **代价/待解**：① 依赖系统装了 Edge（Win10/11 必装 ✓；Mac/Linux 需另议）② 当前 POC 每页冷启 10-17s（含每次新起无头进程 + 固定 4s 等待）→ 实现时必须**单例常驻无头浏览器 + 复用、调优等待**，把单页压到 2-4s。

**判定回写用户**：GATE PASS —— "复用系统引擎渲染 JS、零下载"成立。

> **★★ 用户决策（2026-06-16）：以「三端兼容 + 打包后用户零负担」为最高准则，最终选 Tauri 自带 WebView 为首选实现，CDP-系统-Edge 降为 Windows 可选加速档。** 见下「§2.5 三端最佳实践决策」。
> （CDP-系统-Edge 虽 Windows 最优、且我 POC 证明能力成立，但它**跨平台偏科**：Mac 默认无 Edge/Chrome、Linux 不一定有 → 不能作三端主路。Tauri WebView 是三端唯一"保证在场 + 免安装"的引擎。）

### WI-0 反向链路可行性 GATE（**头号 GATE，未通过不得进 WI-1**）— 第 3 轮重写为 Tauri-WebView 主线
> 旧版 WI-0 全是 crawl4ai 的 pip/Chromium/asyncio 实测，**与新主线不符**，已收编到 WI-0b（dev 档）。
> 主线 GATE 的核心是 §3.0 的**反向跨层链路**——这是被低估的真风险，必须先用最小 spike 证伪/证实。
> 每条要有**实测证据**（命令输出/截图/日志）；任一不通过 → 暂停、回写结论、等用户在 §3.0 三方案里取舍。

**WI-0a 主线 GATE（Tauri-WebView 反向链路，必做）**：
1. **反向投递可行性**：在 dev 桌宠里做最小 spike —— 后端经 control-WS 主动 push 一条自定义消息给前端，
   前端收到后回传一条消息给后端（HTTP 端点或 control-WS 回发），后端按 `reqId` `await` 到结果。
   证明"后端→前端→后端"请求-应答环路成立 + 量一次往返延迟。
2. **离屏 webview 开 + 跨域 eval 取 DOM**：前端用 Tauri JS `WebviewWindow` API（或下沉 Rust `WebviewWindowBuilder`）
   建一个 `visible(false)` 窗，导航到 §WI-(-1) 验证过的 JS 站（`quotes.toscrape.com/js/` / `book.douban.com/latest`），
   eval `document.documentElement.outerHTML`，**确认能拿到渲染后 DOM（含 JS 注入内容）而非空壳**。
   验证三端差异：Win=WebView2 先测；Mac/WKWebView、Linux/WebKitGTK 至少各跑一次或明确标"未验证待补"。
3. **★ 安全隔离实测（硬 GATE，见 §3.3 R8）**：在隔离窗里执行 `window.__TAURI__?.invoke?.('app_exit')`
   及任一 app command，**必须全部被拒/不存在**。证明外部页拿不到 app IPC。不通过 → 安全不达标，主线阻断。
4. **降级路径**：模拟"桌宠窗口隐藏 / control-WS 未连 / 前端无响应"三种情况，确认后端 `webview_render`
   能在超时内返 None 并回落 jina/原结果，**不挂死 `research_run`**（300s 预算）。
5. **超时预算**：量单 URL 经反向链路的端到端 P50/P90（含 push 往返 + 导航 + eval + 回传），
   叠加 deep 档（最多 16 URL，前端串行）总耗时，验证能否压在 300s 内（串行可能是瓶颈，必要时收窄触发计数）。
- **产出**：反向链路可行性结论（成立/受限）+ 三端 eval 取 DOM 验证矩阵 + 隔离实测 + 降级实测 + 超时预算表
  + **回写用户在 §3.0 方案 A/B/C 中取舍的建议**。

**WI-0b dev 档 GATE（仅当要做 crawl4ai/CDP-Edge 旁路时；方案 B/C 下 CDP-Edge 部分可前置到主路）**：
1. **CDP-Edge（POC 已 PASS，确认工程化）**：把 `.tmp/webview_poc_cdp.py` 收成单例常驻无头 Edge + `websockets`
   复用，量冷启/热复用耗时（POC 冷启 10-17s → 目标热复用 2-4s）；实测在**当前冻结 exe** 里 `import websockets` 可用。
2. **crawl4ai 仅 dev**：dev `.venv` `pip install crawl4ai`+`crawl4ai-setup` 跑 ≥2 JS 站确认能力；
   在**冻结 `deskpet-backend.exe`** 里 `import crawl4ai` 预期 ImportError（证实"仅 dev"）；asyncio 共存实测
   （`await AsyncWebCrawler().arun` 不内部 `asyncio.run`/关宿主 loop，冲突则退回线程模式）。
3. crawl4ai 浏览器获取 + 下载源（system 优先 → 自建 COS → 镜像仅实测稳才用）。

### WI-1 后端发起器 `research_webview.py`（主线）
- `async webview_render(url, *, timeout)`：建 `reqId`+`asyncio.Future` 存 `_pending`；经 control-WS 向前端
  push `js_render_request`；`await asyncio.wait_for(future, timeout)`；超时/无前端/error → 返 None（best-effort，不抛，对齐 `research_sources.py`）。
- 模块级 `asyncio.Semaphore` 控后端并发，**且与前端 webview 串行能力对齐**（前端一次只渲一个，后端别狂发）。
- control-WS 未连接 / 无前端窗口 → 立即返 None（不等超时空耗）。
- `webview_render_available() -> bool`：当前有无活跃 control-WS 前端连接（进程内查，不缓存——连接会变）。

### WI-1b control-WS 回传分支（主线）
- 后端 `/ws/control` handler 加入站分支 `js_render_result`：按 `reqId` 取 future `set_result`/`set_exception`。
- **多前端去重**：main/message-panel/code-panel 可能各一条 control-WS；渲染请求只指定**主窗口**处理（或加 claim），避免多窗重复渲同一 URL。

### WI-1c 前端渲染器（主线）
- control-WS `onMessage` 加 `js_render_request` 分支 → `webviewRenderer.ts`：开/复用隔离离屏 webview →
  导航 → 等 load → eval 注入脚本取 `outerHTML`（脚本 try/catch 包字符串）→ 经 control-WS 回传 `js_render_result`。
- **串行**：单例 webview 排队渲染。隔离 webview 用独立 label + 无 capability（见 §3.3）。

### WI-2 安全隔离 capability（主线，硬约束 — 见 §3.3）
- 新增 `capabilities/js-render-sandbox.json`（或在 default.json 里按 label 收窄），让 `js-render-sandbox` 窗
  **不获得任何 app command**。验证隔离窗内 `invoke` 全被拒（对齐 WI-0a #3）。
- 收紧该 webview 的导航/协议访问（不能开新窗、不能访问 `tauri://`/`asset://`）。
- （旧 WI-2 的 crawl4ai/Chromium provisioning 收编为 dev 档，主线零下载，无此项。）

### WI-3 pipeline 接入 `default_extract`（主线）
- trafilatura 短（< `_JINA_MIN_CHARS`，现值 300）→ 若 `_js_render_enabled()`：按 `_js_render_engine()` 选
  `webview_render`（默认）/ `cdp_edge_render`（Win 加速档）/ `crawl4ai_extract`（dev）→ 取更长正文，
  标 `extractor`（`"webview"`/`"cdp-edge"`/`"crawl4ai"`）。**次序**：本地渲染在 jina **之前**（见 §3.1）。
- **取更长正文**（`len(new) > len(text)` 才替换）。
- **★ 触发去重（R5/R6）**：本地渲染命中并替换后**不再**触发 jina（避免一个空壳站连跑两个重型兜底，超时翻倍）。
- **★ 误触发收敛（R5）双闸**：① 仅当 trafilatura `< _JINA_MIN_CHARS` **且原始 HTML `>20KB`**（疑被 JS 藏的富页）才触发；
  ② **单次 research 触发计数上限**（如 ≤4 次/research），超了走 jina/原结果护 300s 预算。
- 保留 `ai_generated`/`mojibake` 过滤：渲染路径下 `is_ai_generated` 扫**渲染后 HTML**（trafilatura 路径扫原始 HTML，锚点不同，测试分别覆盖）。

### WI-4 config + 开关
- `_js_render_enabled()` + `_js_render_engine()` 走统一 `_research_raw()`（**禁止** `_cfg.config.raw`，对齐 commit `2b7baa1`；真实默认在代码：off / `"webview"`）。
- 出厂 `config.toml` 加 `js_render=false` + `js_render_engine="webview"` + 注释（文档作用，见 §5）。

### WI-5 测试（可验证 + 可证伪）
- **后端单测（mock，不联网）**：
  - `webview_render`：mock control-WS 广播器 + 直接 `set_result(html)` 模拟前端回传 → 验返渲染后 HTML；
    无前端连接 → 立即返 None；超时（future 永不 resolve）→ `wait_for` 超时返 None 不抛；error 回传 → 返 None。
  - `default_extract` 接入：mock httpx 让 trafilatura 抽到 `<300字` + 大 HTML，patch `webview_render` 返长文 →
    断言 `extractor=="webview"` 且 jina **未被调用**（去重）；patch 返 None → 回落 jina/原结果。
  - 误触发闸：短文 + **小** HTML（<20KB）→ 断言 `webview_render` **不**被调用。
  - 触发计数上限：一次 research 多个空壳 URL → 断言渲染调用 ≤ 上限。
  - 引擎选择：`js_render_engine` 切 `cdp-edge`/`crawl4ai` → 断言路由到对应适配器（适配器本身 mock）。
- **前端单测**：`webviewRenderer` 收到 `js_render_request` → mock Tauri webview API 返固定 outerHTML → 断言回传 `js_render_result` 含该 HTML；导航失败 → 回传 error。
- **live smoke**（`@pytest.mark.live`，需 dev 桌宠在线）：主线难纯脚本测（依赖 GUI），故 live 证据主要落在 WI-6 真机；dev 档 CDP-Edge/crawl4ai 可脚本 live smoke（选确定性公开 SPA `spa.smashing.work`，避开会改版/被墙/需登录的站）。
- **★ 字节级 flag-off 基线**：写 `tests/test_js_render_flag_off.py` —— flag-off（默认）下对同一 mock URL 跑 `default_extract`，断言：① 返回 dict 逐字段等于未引入前结果（`extractor=="trafilatura"`）；② `sys.modules` 无 `crawl4ai`；③ 不发任何 control-WS 渲染请求（mock 广播器调用次数 0）。把"字节级不变"变可证伪。
- 回归：`backend && python -m pytest tests/ -k "research or search or scoring or extract" -v` 全绿。

### WI-6 真机 E2E（windows-mcp，按项目纪律 — 主线唯一的真证据层）
> **★ 前置**：真机测 worktree 代码，按项目 CLAUDE.md「坑 #8」设 `DESKPET_BACKEND_DIR=<worktree>/backend`
> + `DESKPET_PYTHON=<worktree .venv>`，日志确认 `[backend_launch] Dev python=... backend_dir=<worktree>`
> （否则跑旧冻结 exe，无本期改动 → 测了等于白测，假性 FAIL）。
> **★ 主线特有**：这是真正端到端验证反向链路的唯一手段——纯脚本测不了"后端→前端→webview→后端"全环（依赖 GUI 在线）。
- 开 `[research].js_render=true`（engine=webview）重启桌宠 → 深度调研"内容靠 JS 渲染"的主题 → 报告引用里出现该 JS 站正文（原抓空壳丢源）。
- 隐藏桌宠主窗 / 断 control-WS → 同主题确认**优雅降级**到 jina/原结果，`research_run` 不挂死（验 §3.0 降级）。
- 关 `js_render` → 同主题回落原行为（证开关 + 降级）。
- **★ 可观测证据锚点**：渲染命中时 emit `event="webview_render" url=... chars=... ms=... reqId=...`（structlog → stderr → tauri dev log）；pipeline 替换时该源 `extractor="webview"`。E2E PASS = **grep 到锚点 + 报告引用确有该 JS 站**，而非"报告看起来更全"。
- 截图 + 抓日志归档 `plans/manual-results-2026-06-16-js-render/`，按项目报告格式（坐标/动作/截图/log 证据/判定）。

---

## 5. 配置项（`config.toml [research]`）

> 注意：出厂 `config.toml` 目前**没有 `[research]` 段**，真实默认值来自代码
> `_research_raw().get("js_render", False)` / `.get("js_render_engine", "webview")`。下面的 toml
> 是给用户/文档看的样例，写进出厂 config 是文档作用，不改变代码默认。

```toml
[research]
# JS 渲染抓取兜底(治 JS/SPA 空壳站)。默认关。中国可用(本地渲染,不依赖国外服务)。
js_render = false
# 渲染引擎:
#   "webview"  = 复用 Tauri 自带 WebView 隐藏窗渲染(三端零体积零安装,首选主线;
#                依赖反向跨层链路 + 桌宠 GUI 在线,见 §3.0)
#   "cdp-edge" = 连系统已装 Edge 无头 + CDP(Windows 加速档,纯后端,POC 已验证;Mac/Linux 默认无 Edge)
#   "crawl4ai" = Crawl4AI/Playwright+Chromium(~100-160MB,不进基础包;⚠️ 仅 dev/源码模式可用,冻结包不可用)
js_render_engine = "webview"
# 单页渲染超时(秒),超时降级
js_render_timeout = 20
```

---

## 6. 降级矩阵（best-effort，任何一格失败都不崩）

| 情况 | 行为 |
|---|---|
| `js_render=false`（默认） | 完全跳过，走现有 trafilatura/Jina，**零开销**（不发 control-WS 请求、不 import 任何渲染库） |
| **engine=webview·桌宠主窗隐藏/最小化（主线特有，第 3 轮新增）** | `webview_render` 仍能跑（隐藏窗渲染），但若整个桌宠未启动则无 control-WS → 立即返 None 降级 |
| **engine=webview·control-WS 未连接 / 前端无响应（主线特有，第 3 轮新增）** | `webview_render_available()=False` 或 `wait_for` 超时 → 返 None → 回落 jina/原结果，**不挂死 research_run** |
| **engine=webview·隔离 webview 对外部页 eval 失败（同源/三端差异）** | 前端回传 error → 后端返 None → 降级（WI-0a 须先验证此路成立率） |
| engine=cdp-edge·系统无 Edge（Mac/Linux 常见） | 连不上 CDP → 返 None → 降级（故 cdp-edge 定位 Windows 加速档） |
| engine=crawl4ai·**冻结安装包内** | `import crawl4ai` 必失败 → 静默跳过（正式用户走此格，符合"crawl4ai 仅 dev"范围） |
| engine=crawl4ai·库没装/Chromium 没下好 | 不可用 → 跳过 + 一次性日志；下载失败（system→COS）→ 降级 |
| 渲染超时/异常（任一引擎） | 返 None → 回落 jina/trafilatura 结果 |
| **单次 research 触发已达上限** | 不再调渲染，剩余空壳走 jina/原结果（护 300s 预算） |
| 本地渲染命中并替换 | **跳过 jina**（触发去重，避免双重型兜底叠加超时） |

---

## 7. 验收标准（DoD）

1. **WI-0a 反向链路 GATE 通过**（主线前置）：① 后端→前端→后端请求-应答环路成立；② 隔离离屏 webview
   能对 JS 站 eval 取到渲染后 DOM；③ 隔离窗内 `invoke` 全被拒（安全）；④ 三种缺 GUI 情况下后端优雅降级
   不挂死。**未过此 GATE 不得进 WI-1**，结论回写用户在 §3.0 方案 A/B/C 取舍。
2. `js_render=false`（出厂）时：**抓取链路字节级不变** —— 由 `test_js_render_flag_off.py` 自动断言（返回 dict 逐字段相等 + `sys.modules` 无 `crawl4ai` + 不发任何 control-WS 渲染请求，见 WI-5），不靠肉眼。
3. `js_render=true`（engine=webview）+ 桌宠在线：JS 空壳站能抓到正文 —— 真机 E2E **grep 到 `event="webview_render"` 日志锚点** + 报告引用里确有该 JS 站（原来丢的源）。
4. **降级不崩**（含主线特有的 control-WS 未连/桌宠隐藏/前端无响应/隔离 eval 失败 + 各引擎库缺/超时/触发超限）—— 单测 + 真机覆盖每条降级矩阵格子，`research_run` 永不挂死。
5. **安全**：隔离 webview 加载外部页时无法访问 app IPC（WI-0a #3 + WI-2 capability 实测断言）。
6. `[research].js_render` + `js_render_engine` 开关真生效（走 `_research_raw()`；复测开/关 + 切引擎 + on 但不可用时跳过）。
7. 单测 + 回归全绿；真机 windows-mcp E2E 截图 + 日志归档（确认跑的是 worktree backend，非冻结 exe）。
8. 基础 NSIS 安装包体积**不因本功能增大**：主线 webview 零体积；crawl4ai 未进 spec hiddenimports（diff `du` 截图）。
9. **超时不退化**：deep 档 + js_render on 的真机一次完整跑 **不触发 `research_run` 300s tool_timeout**（抓 wall-clock；注意主线前端串行渲染是潜在瓶颈，对照触发计数 + 并发对齐生效）。
10. **范围诚实**：plan 与代码注释明确标注各引擎可用范围 —— webview=三端但依赖 GUI 在线；cdp-edge=Windows 加速档（Mac/Linux 默认无 Edge）；crawl4ai=仅 dev/源码模式，冻结正式包不可用。不误导用户。

---

## 8. 风险与待定问题（评审后状态）

- **R1 体积/价值比 → ★ 升级为 WI-(-1) 前置 GATE（二轮）**：Crawl4AI 背 Chromium（100-160MB）+ 在冻结包里**当前根本用不了**（只 dev 可用）→ 对"让正式用户受益"这个目标，**性价比存疑**。本仓已有 `browser_use_tool.py` 证明 Tauri 路线之外还有 browser-use 路线，且 Tauri 自带 WebView2 是**零体积**的同能力候选。
  - **决策（二轮）**：在 WI-0 之前先做一个 **0.5 天 WebView 渲染 POC** —— 用 Tauri 现成 WebView2 加载一个 JS 站、`eval` 抓 `document.body.innerText`/`outerHTML` 回传 backend，看能否拿到渲染后正文。
  - **GATE 判据**：若 WebView POC 能稳定拿到正文 → **强烈建议把它做成首选档**（零体积、正式包可用、中国天然可用），Crawl4AI 降为 dev-only 高级档；若 WebView POC 受限（跨进程 eval 回传链路复杂/拿不全 SPA 内容）→ 按本 plan 推进 Crawl4AI。**用户已指定方向是 Crawl4AI，故此 GATE 不阻断，但结论要回写给用户做最终取舍**（不是闷头实现 dev-only 的重方案）。
- **R2 中国下载 Chromium**：第三方镜像（`PLAYWRIGHT_DOWNLOAD_HOST`）**默认不信任**（`model_provisioner` 已因镜像不稳改自建 COS）。WI-0 实测，不稳即走 system 浏览器 + COS 托管 Chromium。
- **R3 system 浏览器 CDP**：中国 Windows 很多人只有 360/QQ 浏览器或纯 WebView2，`crawl4ai_browser=system` 命中 Chrome/Edge channel 的比例需 WI-0 量；命中率低则更印证 R1 的 WebView2 路线（WebView2 是 Win10+ 几乎必装的系统组件，命中率远高于独立 Chrome/Edge）。**这条本身就是 WebView 方案更优的论据。**
- **R4 异步/资源 → 已定（一轮）**：单例 crawler + 复用 context + `Semaphore(≤2)` 页级限流（见 §2 决策表）；不每 URL 新建，不无上限并发。WI-0 量耗时验证预算。
- **R5 触发频率 → 已定（二轮）**：`<300字` 阈值加"原始 HTML 须 >20KB"+"单次 research 触发计数上限"双闸，避免正常短页误触发 + 护超时预算（见 WI-3）。
- **R6 与 Jina 次序/共存 → 已定（一轮）**：本地渲染在 jina 之前；命中即**跳过** jina（触发去重，不双跑）。
- **R7 反向跨层链路（★ 第 3 轮新增 — 主线最大风险，复杂度=高）**：Tauri-WebView 主线需要"后端 Python →
  叫前端/webview 渲染 → 取回 HTML"的**反方向**链路，而真实进程拓扑里 Rust↔backend 单向、能开 webview 的
  只有前端 JS（见 §3.0）。这是一条跨三层、依赖 GUI 在线的异步请求-应答系统，被 §2.5 一句"写一小块 Rust"
  严重低估。**应对**：升为 WI-0a 头号 GATE 先 spike 验证；提供 §3.0 务实备选（方案 B/C：Windows 先用纯后端
  CDP-Edge 落地，三端统一作下一期 §10 F3）；DoD#1 把 GATE 通过列为主线前置。**不阻断用户拍板的三端方向，
  但要求先验证再投入，并把 CDP-Edge 务实备选摊给用户。**
- **R8 隔离 webview 安全（★ 第 3 轮新增 — 硬约束）**：主线把不可信外部页加载进 webview；当前
  `tauri.conf.json` `csp=null` + 仅 `default.json` capability + `withGlobalTauri:true`，**无隔离**。
  外部页恶意 JS 可能 `window.__TAURI__.invoke` 拿 app 能力。**应对**：见 §3.3 —— 独立 label + 无 capability +
  WI-0a #3 实测"隔离窗内 invoke 全被拒"。**注**：CDP-Edge/crawl4ai 在独立浏览器进程渲染，天然不接触 app IPC，
  无此负担（务实备选的又一加分项）。
- **R9 三端 eval 差异 + 前端串行瓶颈（★ 第 3 轮新增）**：① 三端 WebView 引擎（WebView2/WKWebView/WebKitGTK）
  对"跨域外部页 eval 取 DOM"行为是否一致、是否受同源策略限制，未验证（WI-0a #2 矩阵）。② 单例离屏 webview
  **串行渲染**，deep 档 16 URL 排队可能线性叠加顶穿 300s（WI-0a #5 量 + 触发计数收窄）。

---

## 9. 关键来源
- Crawl4AI: https://github.com/unclecode/crawl4ai （AsyncWebCrawler / BrowserConfig / CDP 复用系统浏览器）
- 现有抓取链路: `backend/deskpet/tools/research_tools.py::default_extract`（trafilatura + Jina 两级；`_JINA_MIN_CHARS=300`）
- **★ 必读范本（一轮发现）**: `backend/deskpet/tools/browser_use_tool.py` —— 已有的 browser-use+Playwright+Chromium 工具，懒 import / flag-off 零开销 / 后台线程+`asyncio.run` / 首用下 Chromium / job 化 全都示范过
- 外置下载基建（**真实模块**）: `backend/deskpet/model_provisioner.py`（`ProvisionStatus` + COS 直下 + `model_provision_status` control-WS 事件）；**不是** `provisioning/` 目录
- 外置下载先例: [`nsis-model-externalization`](../2026-06-05-nsis-model-externalization/PLAN.md)
- 冻结打包: `backend/deskpet-backend.spec`（`hiddenimports`，**未含** crawl4ai/playwright/browser_use → 冻结包不可 import）
- research_run 工具超时已 300s 且 deep 档慢网逼近: commit `ee38fc4`（TC-P2-03 实证）
- `[research]` 开关读取(务必走 `_research_raw()`): commit `2b7baa1`
- 直连源 best-effort 降级先例: `backend/deskpet/tools/research_sources.py`（cninfo/openstd/edgar 失败返空不抛）
- 轻量替代(R1 评审重点): 复用 Tauri WebView2/WebKit 渲染（`eval`），零体积、正式包可用

---

## 10. Follow-up（本期不做，但必须显式记录）

- **F1 让正式冻结包也能用 crawl4ai**：把 crawl4ai + 依赖作为**外置 wheel 包**，首次开启时运行时
  `pip install --target <user_dir>` 并 `sys.path` 注入（类比 model 外置，但对象是 Python 包）。
  涉及离线 wheel 镜像、ABI/平台匹配、`sys.path` 卫生，复杂度高 → 单列一期。**在此之前，crawl4ai 仅 dev 可用。**
- **F2（已晋升主线，见 §2.5/§3）WebView 渲染档**：原 follow-up 的"WebView 零体积渲染档"已被用户拍板为
  **主线**（`js_render_engine="webview"`），不再是 follow-up。保留此条仅记录沿革。
- **F3 务实分期：Windows 先 CDP-Edge 纯后端落地，三端统一作下一期（★ 第 3 轮新增，对应 §3.0 方案 C / R7）**：
  鉴于 Tauri-WebView 反向链路复杂度高（R7）且需 GUI 在线，可考虑**本期先把 CDP-系统Edge 在 Windows 落地**
  （纯后端、POC 已 PASS、`websockets` 可进冻结包正式可用、无跨层无 GUI 依赖、无隔离安全负担），让 Windows 用户
  当期受益；把"三端 Tauri-WebView 统一"作为独立下一期，先做 WI-0a 反向链路 spike-GATE 验证可行性再投入实现。
  **这不违背三端统一终态，只是把落地路径分两步走，先低风险拿到 Windows 的价值。是否采用由用户在 §3.0 取舍。**

---

## 评审迭代记录

> 评审方法：对照真实代码（非凭空），两轮对抗式挑战 + 就地修订。不推翻"做 Crawl4AI"的总方向，
> 目标是**强化落地性 + 暴露真风险**。

### 第 1 轮 — 可行性与硬伤

读了 `research_tools.py`(default_extract/_research_raw/_handle_research_run 及 300s 注册)、
`browser_use_tool.py`、`model_provisioner.py`、`deskpet-backend.spec`、`research_sources.py`、
`nsis-model-externalization/PLAN.md`，并实查了 venv（playwright 包未装、无 ms-playwright Chromium、
browser_use 已装）后，揪出的关键问题与改动：

1. **冻结包根本 import 不到 crawl4ai（最大认知偏差）**。生产 backend 是 PyInstaller 冻结 exe，
   无 site-packages，spec 的 `hiddenimports` 没有 crawl4ai/playwright/browser_use。
   → 改：在 §2 加「★ frozen 不可 import」硬约束，明确**本期 crawl4ai 仅 dev 可用**，正式包用上需 F1 外置 wheel（§10）；
   DoD#8 要求 plan/注释诚实标注；降级矩阵加"冻结包内"格子；WI-0 加实测项 #2、WI-6 加"确认非冻结 exe"前置。
2. **已有 `browser_use_tool.py` 把所有难题解过了**，plan 却当全新问题处理（"待定/虚"）。
   → 改：顶部加「评审关键前置」指向它；§3.2 加「强制复用范本」；§9 列为必读范本。asyncio/懒 import/下 Chromium 全部对齐它。
3. **下载镜像假设过于乐观**。`model_provisioner` 注释明确"hf-mirror 等第三方镜像实测不稳，已改自建 COS"。
   → 改：§2 + WI-2 把下载源优先级改为 **system 浏览器免下载（首选）→ 自建 COS 托管 Chromium → 第三方镜像（仅 WI-0 实测稳才用）**；R2 改为默认不信任镜像。
4. **超时预算会被顶穿**。research_run 工具上限已 300s 且 deep 档慢网实测逼近（ee38fc4）；
   default_extract 对所有候选 URL **并发** → crawl4ai 会同时起多个浏览器页。
   → 改：§2 把 R4 从"待定"升为**必决**（单例 crawler + `Semaphore(≤2)`）；加"单次 research 触发计数上限"；
   WI-0 加超时预算实测项；DoD#7 要求真机不触发 300s 超时。
5. **asyncio 共存没说清**。default_extract 在宿主事件循环里被 await。
   → 改：§3.2 加 asyncio 决策（直接 await，不需开线程；但 WI-0 #3 必须实测 crawl4ai 不内部 `asyncio.run`，冲突则退回线程模式）。
6. **`provisioning/` 目录不存在**（plan 笔误）+ **出厂 config.toml 无 `[research]` 段**（默认在代码）。
   → 改：WI-2 改指 `model_provisioner.py`；§2/§5/WI-4 澄清 config 默认来源是代码不是 toml。

### 第 2 轮 — 完成度与落地细节

在第 1 轮修订稿上再挑测试可验证性、DoD 可证伪、可观测证据、误触发/去重落地、R1 取舍：

1. **DoD#1「字节级不变」无法肉眼证伪** → 改：WI-5 新增 `test_crawl4ai_flag_off.py` 自动断言
   （返回 dict 逐字段相等 + `sys.modules` 无 crawl4ai + 工厂调用 0 次）；DoD#1 改为引用该测试。
2. **单测「mock 怎么写」太虚 + live smoke 选站不确定** → 改：WI-5 写清 monkeypatch 懒 import 符号/注入工厂的 mock 方式、
   每条降级矩阵都要单测、live smoke 钉死选确定性公开 SPA（`spa.smashing.work`），避免选会改版/被墙/需登录的站。
3. **误触发与去重只是 R5/R6 问句，没落地** → 改：WI-3 落地双闸（HTML>20KB 才触发 + 单次 research 触发计数上限）
   + crawl4ai 命中即跳过 jina（触发去重，防双重型兜底叠加超时），并加对应单测。
4. **真机 E2E 没有明确"PASS 锚点"** → 改：WI-6 钉死结构化日志锚点 `event="crawl4ai_extract"`，
   PASS = grep 到锚点 + 报告确含该 JS 站；并补 worktree backend 前置（防测到旧冻结 exe）。
5. **R1（WebView 更轻替代）应升级为前置判定而非脚注** → 改：R1 升级为 WI-(-1) 的 **0.5 天 WebView POC GATE**，
   结论回写用户做最终取舍（鉴于 crawl4ai 当前 dev-only + 重，WebView2 零体积且正式包可用，性价比可能反超）；
   新增 §10 F1/F2 follow-up 记录"正式包可用"与"WebView 档"两条未来线。

### 第 3 轮 — WebView 主线对齐（反向跨层链路 + WI 对齐 + 务实备选）

> 触发：用户经 POC + 三端最佳实践拍板，主线从 Crawl4AI 转向「复用 Tauri 自带 WebView 渲染」（§2.5），
> 但 §3 起的下游（WI-0~WI-6 / DoD / 配置 / 降级 / 风险）整套仍停留在 Crawl4AI 旧主线，且 §2.5 把跨层
> 复杂度一句"写一小块 Rust"带过。本轮**读真实跨层代码**（`process_manager.rs`、`lib.rs`、`commands.rs`、
> `ControlChannel.ts`、`tauri.conf.json`、`webview_permissions.rs`、`Cargo.toml`）后对抗式挑战 + 就地修订。

**本轮揪出的关键问题与改动**：

1. **【最大未填洞】反向跨层链路根本没写，且被严重低估**。读代码确认：`process_manager.rs` 里 Rust 只
   spawn backend + 读一次 stdout 的 SHARED_SECRET 后静默 drain，`lib.rs::invoke_handler` 全是「前端→Rust」
   command —— **Rust↔backend 单向，后端命令不动 Rust**；能开/控 webview 的只有前端 JS。所以"后端用渲染结果"
   要走一条 **后端(WS server)→push→前端(WS client)→Tauri JS 开离屏 webview eval 取 DOM→回传后端** 的
   **四跳异步请求-应答环路**，control-WS（`ControlChannel.ts` 证实后端已能 server→client push）是唯一可复用
   投递层但**缺"渲染请求/HTML 回传"这对消息**。复杂度=高。
   → **新增 §3.0** 完整画出进程拓扑 + 四跳链路 + 必解工程问题（reqId↔Future、超时降级、并发对齐、GUI 依赖、
   生命周期）；顶部加 🛑 前置块；§2.5 修正"写一小块 Rust"的低估；**新增 R7**。
2. **务实备选被忽略**。POC 已验证的 **CDP-系统Edge** 是**纯后端**（`websockets` 连本地无头 Edge），无跨层、
   无 GUI 依赖、`websockets` 可进 spec hiddenimports → **连冻结正式包都能用**，在实现复杂度/可测性/安全/不挂死
   四项上明显优于 Tauri-WebView，唯一短板是跨平台偏科（Mac/Linux 默认无 Edge）。
   → §3.0 摊开**方案 A（三端全 WebView，用户拍板）/ B（Win 用 CDP-Edge + Mac/Linux 用 WebView 混合）/
   C（本期 Win 先 CDP-Edge 落地，三端统一下一期）**三选项利弊，**尊重三端统一终态、不强推翻**，建议回写用户取舍；
   **新增 §10 F3**。
3. **安全洞：把不可信外部页加载进默认 webview = 可被 `__TAURI__.invoke` 攻击**。实查 `tauri.conf.json`
   `csp=null` + 仅 `default.json` capability + `withGlobalTauri:true`，对"外部 URL 窗"零隔离。
   → **新增 §3.3**（独立 label + 无 capability + 实测"隔离窗内 invoke 全被拒"）；**新增 R8**；DoD#5 加安全项；
   WI-0a #3 + WI-2 落地。
4. **下游 WI/DoD/配置/降级全对齐到新主线**。WI-0 重写为"反向链路头号 GATE"（WI-0a 主线 + WI-0b dev 档）；
   WI-1 拆为后端发起器 `research_webview.py` + WI-1b control-WS 回传分支 + WI-1c 前端渲染器；WI-2 改为隔离
   capability；WI-3/5/6 改用 `extractor="webview"` + `event="webview_render"` 锚点 + control-WS mock；
   §3.2 接入点表重写为"主线三层改动 + CDP-Edge/crawl4ai 收编为旁路/dev 档"；§5 配置改为
   `js_render` + `js_render_engine`(webview/cdp-edge/crawl4ai)；§6 降级矩阵加 control-WS 未连/桌宠隐藏/隔离
   eval 失败等主线特有格子；§7 DoD 加 GATE 前置 + 安全 + 主线降级；**新增 R9**（三端 eval 差异 + 前端串行瓶颈）。
   Crawl4AI 相关全部降为 dev 档 follow-up，POC 事实（WI-(-1)）原样保留。
