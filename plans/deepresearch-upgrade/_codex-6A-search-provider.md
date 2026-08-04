# CODEX 任务 1：§6.0-B/C/D + 引擎基建（只动 search_provider.py + 只读用 research_cdp_edge）

仓库 G:/projects/deskpet。**先读** `plans/deepresearch-upgrade/00-upgrade-plan.md` 的 §6.0-B/§6.0-C/§6.0-D + §6.0.1 契约-3/4/5/6 + §6.0.2(captcha/门控两态/可注入时钟) + §6.0.3(运行时预算/captcha sentinel) + §6.0.4(实现顺序/待定量取值)。严格照它。

**唯一可改文件**：`backend/deskpet/tools/search_provider.py`（+ 新增 `backend/tests/fixtures/bing_serp_sample.html`、测试加进 `backend/tests/test_search_provider.py` 若无则新建）。**不要动** research_tools.py / research_sources.py / config.py（集成由 Lead 做）。

## 必做（逐项）
1. **注册引擎**：`_KNOWN_ENGINES`(:49) 加 `"bing-cdp"`、`"searxng"`（共 5 个）。`_DEFAULT_ENGINE_QUEUE`(:48) **保持不变** `("bing","duckduckgo")`。
2. **search_async 分派分支**（主循环 :305 附近，在现有 `_engine_request`+`cli.request` 路径前加前置分支）：
   - `engine == "bing-cdp"`：构造 Bing SERP URL（复用 bing 的 URL+region 逻辑，mkt=zh-CN/en-US）→ `html = await research_cdp_edge.cdp_edge_render(serp_url, timeout=_serp_render_timeout())`（默认 **8s**，不复用 20s）→ `results = parse_bing_html(html, max_results=n)`。**绕开 httpx `cli.request`**。`cdp_edge_render` 返 None → 该引擎记失败、降级队列下一个，不抛。
   - `engine == "searxng"`：读 `[research].searxng_url`（无则该引擎跳过/不入队）→ httpx GET `{url}?q={q}&format=json` → 新增 `_parse_searxng_json(body,max_results)` 解析 `results[].{url,title,content}`。
3. **search-CDP 预算**（§6.0.3）：模块级独立计数器 `_search_cdp_count`，每 run ≤ `K=4`（加 reset 函数供 research_tools 在每轮开头调，或自带按 query 计数）；注意与 fetch 的 JS 渲染**共用** research_cdp_edge 的 `_render_semaphore`（无需自己加锁）。
4. **captcha sentinel**（§6.0.3）：bing-cdp 拿到 html 后若 `parse_bing_html` 返空 **但** html 非空且 >10KB 且命中 captcha 指纹（无 `li.b_algo` + 文本含 `verify/unusual traffic/captcha/机器人` 任一）→ 记显式错误标记 `bing_cdp_captcha_suspected`（log.warning + 让上层能区分"真无结果"与"被验证码挡"；可在返回里带 sentinel 或抛专用可捕获信号由 search_async 转成 errors 文案）。**不要无声返空**。
5. **D 硬化**（§6.0-D + 契约-6，门控 `[research].serp_hardening` 默认 **off=旧行为**）：开启时加 ①真实浏览器 headers + 轮换 UA ②引擎被封后**冷却**（失败计数→该引擎冷却 N 分钟跳过，进程内模块级 dict）③同 query **结果缓存**（短 TTL）④退避重试。**时间读取必须经可注入 clock**（模块级 `_now=lambda:time.monotonic()`，**禁止单测用真 time.sleep**，规避 flaky）。
6. **暴露 engines-hit 供观测**：search_async 记录本次实际返回结果的引擎名，暴露给调用方（如模块级 `get_last_engines_hit()` 或在返回结构带元数据）——research_tools 的 route 观测要读它。给出清晰 API + docstring 说明。

## 硬约束
- **零新 pip 依赖**（httpx + selectolax 已有；searxng JSON 用 httpx+.json()）。
- 不接付费 API。`_engine_queue()` 的 `[e for e in q if e in _KNOWN_ENGINES]` 过滤逻辑要让新引擎能入队（靠第1步注册）。
- 纯解析函数（parse_bing_html/_parse_searxng_json）保持可单测无网络。

## 验收（必须）
1. 新增/更新 `backend/tests/test_search_provider.py`：
   - **bing-cdp**：monkeypatch `search_provider` 内 `cdp_edge_render` 返存盘 fixture(`tests/fixtures/bing_serp_sample.html`，造一段含 ≥2 个 `li.b_algo` 的最小 Bing SERP) → 断言 ①结果非空 ②`cli.request`/httpx 未被调用(证绕开) ③`cdp_edge_render` 返 None 时不抛、降级。
   - **captcha sentinel**：mock 返回无 b_algo 的 captcha 页 → 断言识别为 captcha（非空结果误判）。
   - **searxng 两态**：配 `searxng_url`→入队且解析 mock JSON 正确 / 不配→不入队。
   - **冷却/缓存可注入时钟**：monkeypatch `_now` 推进虚拟时间，测冷却到期恢复 + 缓存 TTL 过期，**不用真 sleep**。
   - **_KNOWN_ENGINES** 含 bing-cdp/searxng（封堵静默过滤）。
2. 跑 `cd backend && .venv/Scripts/python.exe -m pytest tests/test_search_provider.py -q` 全绿。
3. 跑现有 research 相关测试不回归：`tests/test_deskpet_research_tools.py` 仍绿。

## 完成后
输出：改了哪些函数/行、新增引擎/函数名、`get_last_engines_hit`(或等价)的确切 API、新增测试名、pytest passed 数。**不要 commit**。
