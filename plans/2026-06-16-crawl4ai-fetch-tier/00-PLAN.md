# Crawl4AI JS 渲染抓取层 — 实施 PLAN

> **状态**: 📋 规划中（待评审迭代）
> **目标**: 给 deep-research 抓取链路加一级 **Crawl4AI 真浏览器渲染**兜底，治 trafilatura/Jina
> 都拿不下的 JS/SPA/强反爬站，输出 LLM 友好的干净 Markdown。
> **仓库**: https://github.com/unclecode/crawl4ai （pip `crawl4ai`，Python 3.10+，底层 Playwright）
> **最后更新**: 2026-06-16

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
| **体积**：Crawl4AI 底层 Playwright + Chromium ≈ **100~160MB**，与 NSIS 瘦包定位冲突 | **绝不进默认安装包**。复用现有 [`nsis-model-externalization`](../2026-06-05-nsis-model-externalization/PLAN.md) 的「首启外置下载」模式：基础包不含 Chromium，用户**显式开启**后首次使用时下载 |
| **中国可达**：Playwright 默认从 `playwright.azureedge.net` 下 Chromium，中国慢/不稳 | 下载走**国内镜像**（`PLAYWRIGHT_DOWNLOAD_HOST` 指 npmmirror 等）；或**复用系统已装 Chrome/Edge**（CDP），无浏览器再降级 |
| **opt-in**：默认关，避免普通用户被几百 MB 吓到 | `[research].crawl4ai = false`（出厂默认）。开关 + 首启下载双门 |
| **降级**：浏览器没装好 / 渲染失败 / 超时 | best-effort：任何失败 → 回落现有 trafilatura/Jina 结果，绝不让抓取整体崩 |
| **不阻塞**：浏览器渲染慢（秒级） | 仅在 trafilatura 抽空/过短时**才触发**（和 Jina 同条件）；独立超时 |

> **本质**：把 Crawl4AI 做成"**可选的、外置的、中国镜像的、best-effort 的**第三级抓取兜底"，
> 而不是默认链路。这样既拿到它的 JS 渲染能力，又不违背"单机桌宠 + 瘦包 + 中国用户"定位。

---

## 3. 架构与接入点

### 3.1 抓取链路（升级后，三级降级）

```
httpx 直抓 → trafilatura(主)
   ↓ 正文 < _JINA_MIN_CHARS(300)
[research].crawl4ai=on 且浏览器就绪 → Crawl4AI 渲染抽 Markdown   ← 本 plan 新增
   ↓ 仍不行 / crawl4ai 关 / 浏览器没装
[research].jina_reader=on → Jina Reader(国外,代理)
   ↓
返回现有最好结果(哪怕短)
```

> **次序决策**：Crawl4AI（本地、中国可用）排在 Jina（国外、需代理）**之前** —— 中国用户优先用能直连的。

### 3.2 代码接入点（最小侵入）

| 文件 | 改动 |
|---|---|
| `backend/deskpet/tools/research_crawl4ai.py`（新） | `async crawl4ai_extract(url, *, timeout) -> Optional[dict]`：封装 `AsyncWebCrawler.arun`，取 `result.markdown`/`result.cleaned_html`；best-effort 返 None。浏览器/库缺失时**静默不可用** |
| `research_tools.py` `default_extract` | trafilatura 短 → 若 `_crawl4ai_enabled()` 且就绪，调 `crawl4ai_extract`；取更长正文，`extractor="crawl4ai"` |
| `research_tools.py` | 加 `_crawl4ai_enabled()`（走 `_research_raw().get("crawl4ai", False)`）+ `_crawl4ai_ready()`（探浏览器是否就绪，缓存） |
| `backend/deskpet/provisioning/`（复用现有 provisioner） | 首启/首次开启时下载 Chromium（国内镜像）；`crawl4ai-setup` 等价逻辑 |
| `config.toml` `[research]` | `crawl4ai = false`（出厂默认）；可选 `crawl4ai_browser_channel`（system/chromium）、`crawl4ai_timeout` |
| `pyproject`/依赖 | `crawl4ai` 列为**可选 extra**（`pip install deskpet[crawl4ai]`），不进基础依赖 |

---

## 4. 分阶段任务（WI）

### WI-0 决策 spike（先做，1 步）
- 验证 Crawl4AI 在本机：`pip install crawl4ai` + `crawl4ai-setup`，跑一个已知 JS 站（如某 SPA 新闻）→ 确认 `result.markdown` 拿到正文。
- 量 Chromium 实际体积 + 首装耗时；测 `PLAYWRIGHT_DOWNLOAD_HOST` 国内镜像可行性。
- 测 **CDP 复用系统 Chrome/Edge**（`BrowserConfig(use_persistent_context, user_data_dir)` / CDP）能否免下载 Chromium。
- **产出**：可行性结论 + 选定"下载 Chromium(镜像)" vs "复用系统浏览器" vs "二者皆备"。

### WI-1 适配器 `research_crawl4ai.py`
- `crawl4ai_extract(url, *, timeout=20)`：`AsyncWebCrawler(config=BrowserConfig(headless=True, ...))` → `arun(url)` → 返 `{text, title, markdown}`。
- 库未装 / 浏览器缺失 / 超时 / 异常 → 返 None（best-effort，不抛）。
- `crawl4ai_available() -> bool`：探 import + 浏览器就绪，结果**进程缓存**（避免每次 import 重探）。

### WI-2 provisioning（外置下载）
- 复用现有 `model_provisioner` 思路：`[research].crawl4ai=on` 且浏览器未就绪 → 触发**首启下载 Chromium**（国内镜像 env），下载中给前端进度/提示。
- 下载失败 → `crawl4ai_available()=False` → 自动降级，不卡。

### WI-3 pipeline 接入 `default_extract`
- trafilatura 短（< `_JINA_MIN_CHARS`）→ 先试 crawl4ai（若 on+ready），再试 jina。
- 标 `extractor="crawl4ai"`；取最长正文；保留 `ai_generated`/`mojibake` 过滤（扫渲染后 HTML）。

### WI-4 config + 开关
- `_crawl4ai_enabled()` 走统一 `_research_raw()`（注意：本仓刚修过 `[research]` 开关读取 bug，commit `2b7baa1`，务必走 `_research_raw()` 不要再写 `_cfg.config.raw`）。
- 出厂 `config.toml` 加 `crawl4ai = false` + 注释说明体积/首启下载。

### WI-5 测试
- **单测**：mock `AsyncWebCrawler` → 验 `crawl4ai_extract` 取 markdown / 失败返 None / 库缺失静默；`default_extract` 在 crawl4ai on 且 trafilatura 短时切到 crawl4ai。
- **live smoke**（标记，不进 CI 默认）：真跑一个 JS 站验证渲染。
- 回归：`tests/ -k "research or search or scoring"` 全绿。

### WI-6 真机 E2E（windows-mcp，按项目纪律）
- 开 `[research].crawl4ai=true` 重启桌宠 → 深度调研一个"内容靠 JS 渲染"的主题 → 报告引用里出现该 JS 站正文（原来抓空壳丢源）。
- 关 `crawl4ai` → 同主题回落原行为（证明开关 + 降级）。
- 截图 + 抓日志（`extractor=crawl4ai` 事件）。归档 `plans/manual-results-2026-06-16-crawl4ai/`。

---

## 5. 配置项（`config.toml [research]`）

```toml
[research]
# Crawl4AI 本地真浏览器渲染抓取(治 JS/SPA 空壳站)。默认关:底层 Playwright+Chromium
# ~100-160MB,不进基础安装包,开启后首次使用走国内镜像下载。中国可用(本地渲染)。
crawl4ai = false
# 浏览器来源: "chromium"(下载) | "system"(复用已装 Chrome/Edge,免下载,CDP)
crawl4ai_browser = "system"
# 单页渲染超时(秒),超时降级
crawl4ai_timeout = 20
```

---

## 6. 降级矩阵（best-effort，任何一格失败都不崩）

| 情况 | 行为 |
|---|---|
| `crawl4ai=false`（默认） | 完全跳过，走现有 trafilatura/Jina |
| `crawl4ai=true` 但库没装 | `crawl4ai_available()=False` → 跳过 + 一次性日志提示 |
| `crawl4ai=true` 但 Chromium 没下好 | 触发下载；下载失败 → 跳过降级 |
| 渲染超时/异常 | 返 None → 回落 trafilatura 结果 |
| `crawl4ai_browser=system` 但用户没装 Chrome/Edge | 尝试下载 chromium 或跳过 |

---

## 7. 验收标准（DoD）

1. `crawl4ai=false`（出厂）时：**抓取链路字节级不变**，无新依赖被 import，包体积不增。
2. `crawl4ai=true` + 浏览器就绪：JS 空壳站能抓到正文（真机 E2E 证明引用里多了原来丢的源）。
3. 任何失败路径都**降级不崩**（库缺/浏览器缺/超时/异常）。
4. `[research].crawl4ai` 开关真生效（走 `_research_raw()`，复测开/关行为不同）。
5. 单测 + 回归全绿；真机 windows-mcp E2E 截图 + 日志归档。
6. 基础 NSIS 安装包体积**不因本功能增大**（Chromium 外置下载）。

---

## 8. 风险与待定问题（评审重点）

- **R1 体积/价值比**：Crawl4AI 背 Chromium，即便外置下载，对单机桌宠仍重。**WebView 复用方案**（Tauri 自带内核渲染，零体积）是更轻的同能力替代——本 plan 选 Crawl4AI 是用户明确指定；**待评审：是否仍要并列保留 WebView 作为 `crawl4ai_browser=webview` 的更轻档？**
- **R2 中国下载 Chromium**：`PLAYWRIGHT_DOWNLOAD_HOST` 国内镜像是否稳定可用？（WI-0 验证）
- **R3 system 浏览器 CDP**：中国 Windows 很多人只有 360/QQ 浏览器或纯 WebView2，`crawl4ai_browser=system` 命中率多少？是否需要 chromium 下载兜底？
- **R4 异步/资源**：AsyncWebCrawler 起浏览器进程开销大，并发抓取时是否要池化 + 上限？是否每次新建 crawler（慢）还是复用单例（占内存）？
- **R5 触发频率**：JS 空壳判定阈值（< 300 字）会不会误触发让正常站也走重型渲染？
- **R6 与 Jina 次序/共存**：两个 JS 兜底都开时的次序与去重。

---

## 9. 关键来源
- Crawl4AI: https://github.com/unclecode/crawl4ai （AsyncWebCrawler / BrowserConfig / CDP 复用系统浏览器）
- 现有抓取链路: `backend/deskpet/tools/research_tools.py::default_extract`（trafilatura + Jina 两级）
- 外置下载先例: [`nsis-model-externalization`](../2026-06-05-nsis-model-externalization/PLAN.md)
- `[research]` 开关读取(务必走 `_research_raw()`): commit `2b7baa1`
- 轻量替代(评审参考): 复用 Tauri WebView2/WebKit 渲染（`eval_with_callback`），零体积
