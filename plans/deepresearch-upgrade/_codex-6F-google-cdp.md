# CODEX 任务：§6.0-B 扩展 — google-cdp 引擎(无头浏览器渲染谷歌 SERP, 可达门控)（只动 search_provider.py）

仓库 G:/projects/deskpet。先读 `plans/deepresearch-upgrade/00-upgrade-plan.md` §6.0-B + §6.0.1 契约-3/4 + §6.0.2(captcha)。**唯一可改文件**：`backend/deskpet/tools/search_provider.py`（+ 测试 `backend/tests/test_search_provider.py` + fixture）。**不要动** research_sources.py / research_tools.py / config.py。

## 背景
已有 `bing-cdp` 引擎：search_async 主循环里 `if engine == "bing-cdp"` 走 `research_cdp_edge.cdp_edge_render(serp_url, timeout=_serp_render_timeout())` 取 outerHTML → `parse_bing_html`，绕 httpx。现要**照同款加 `google-cdp`**（谷歌），但谷歌国内常被墙 → 须**可达门控**（VPN/能访问才用，不通自动跳过）。用户要求："谷歌如果有 VPN 或可访问就用，没有不用"。

## 必做
1. **注册引擎**：`_KNOWN_ENGINES` 加 `"google-cdp"`。
2. **search_async 分派分支**（紧邻 bing-cdp 分支）：`if engine == "google-cdp"`：
   - **先可达门控**：`if not _google_reachable(): 记 error/skip 该引擎、降级队列下一个`（不傻等渲染超时）。
   - 构造谷歌 SERP URL：`https://www.google.com/search?q={quote(query)}&hl={zh-CN/en}`（region 同 bing 逻辑）。
   - `html = await cdp_edge_render(serp_url, timeout=_serp_render_timeout())`（复用 8s）→ 新增 `_parse_google_html(html, max_results)` 解析谷歌结果（organic：`div.g`/`div[data-hveid]` 里 `a[href] h3` + snippet；选 selectolax，容错）。
   - 绕 httpx；`cdp_edge_render` 返 None → 降级，不抛。复用 search-CDP 预算计数（与 bing-cdp 共享 `_search_cdp_count` ≤ K）。
   - captcha sentinel：parse 返空但 html 非空 >10KB 且命中 google captcha 指纹(`recaptcha`/`unusual traffic`/`/sorry/`)→ 记 `google_cdp_captcha_suspected`（不无声返空）。
3. **`_google_reachable()`**：轻量探测 `https://www.google.com/` ≤4s，**进程内缓存**（模块级 + 短 TTL，用可注入 `_now`，提供 reset）。通→True。
4. **暴露 engines_hit**：google-cdp 命中时同样进 `_last_engines_hit`（现有机制）。

## 硬约束
- 零新 pip 依赖（selectolax/httpx 已有；渲染用现成 research_cdp_edge）。
- 不接付费 API。可达门控让"没 VPN"时 google-cdp 自动不参与，不报错、不拖慢。

## 验收
1. 测试（`test_search_provider.py`）：
   - **google-cdp 渲染**：monkeypatch `cdp_edge_render` 返存盘 fixture(`tests/fixtures/google_serp_sample.html`，含 ≥2 条 organic) + `_google_reachable→True` → 断言结果非空 + httpx 未被调（绕 SERP 抓取）+ engines_hit 含 google-cdp。
   - **不可达自动跳过**：`_google_reachable→False` → google-cdp 不渲染、降级下一引擎、不抛。
   - **captcha sentinel**：mock 返回 google captcha 页 → 识别 `google_cdp_captcha_suspected`。
   - **可达缓存**：注入 `_now` 推进，测 TTL（不真 sleep）。
   - `_KNOWN_ENGINES` 含 google-cdp；`_parse_google_html` 纯函数可单测。
2. `cd backend && .venv/Scripts/python.exe -m pytest tests/test_search_provider.py -q` 全绿；现有 bing-cdp/searxng 测不回归。

## 完成后
输出：新引擎/函数名 + `_google_reachable` 签名 + 新测名 + passed 数。**不要 commit**。
