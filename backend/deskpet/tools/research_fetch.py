# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""深度研究的取材层（从 research_tools 下沉，阶段 8F）。

站点与资料包规则、搜索结果与网页抽取解析、来源权威度、JS 渲染名额。
"""

from __future__ import annotations

import asyncio
import contextvars
import ipaddress
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx

from deskpet.retrieval import runtime
from deskpet.retrieval.contracts import SearchRequest
from deskpet.tools import search_provider


def _is_loopback_url(base_url: str) -> bool:
    """True 当 base_url 的 host 是本地回环(localhost / 127.0.0.0/8 / ::1)。

    用 hostname 解析 + ipaddress.is_loopback,避免裸字符串匹配漏 ::1/127.0.0.2/
    大小写,也不误伤含 'localhost' 子串的远端域名。main.py 据此决定是否注入 rerank
    桥(本地 ollama 通常没有 gpt-4.1-mini)。"""

    try:
        host = (urllib.parse.urlparse(base_url).hostname or "").strip().lower()
    except (ValueError, TypeError):
        return False
    if not host:
        return False
    if host in ("localhost", "localhost."):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# P1-2 site: 定向官方域 —— 按子问题意图把搜索锁定到一手权威域,提升"找到一手源"
# 命中率(命中域名 gov.cn/cninfo/arxiv 天然 TIER_1,后续打分/精排自然favor)。
# 顺序即优先级,命中第一条即用。keywords 用小写(中文不受 lower 影响,英文转小写匹配)。
_SITE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "上市公司",
            "公告",
            "财报",
            "年报",
            "季报",
            "半年报",
            "业绩预告",
            "招股",
            "招股书",
            "问询函",
            "巨潮",
        ),
        "site:cninfo.com.cn",
    ),
    (
        (
            "政策",
            "法规",
            "规定",
            "通知",
            "方案",
            "规划",
            "监管",
            "办法",
            "意见",
            "部委",
            "工信部",
            "发改委",
            "国务院",
            "条例",
            "国标",
            "国家标准",
            "技术规范",
            "标准化",
        ),
        "site:gov.cn",
    ),
    (
        ("论文", "arxiv", "preprint", "学术研究", "综述论文", "算法原理", "sota", "paper"),
        "site:arxiv.org",
    ),
)


def _site_directive_for(text: str) -> str | None:
    """子问题命中政策/企业/学术意图 → 返回对应 site: 定向(如 site:gov.cn);
    都不命中 → None(只走普通搜索)。"""
    t = (text or "").lower()
    for kws, site in _SITE_RULES:
        if any(k in t for k in kws):
            return site
    return None


# Source packs add a small, deterministic set of source-directed searches for
# topics where generic SERP ranking often misses the sources users actually
# need. Keep these as standalone source-directed queries (not direct fetches) so the existing
# search/extract/scoring/rerank pipeline still decides what is usable.
_SOURCE_PACK_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "ai_agent_harness",
        (
            "ai agent",
            "agent harness",
            "agentic system",
            "agent framework",
            "智能体",
            "智能体框架",
            "智能体工程",
            "代理框架",
        ),
        (
            "AI agent harness best practices site:anthropic.com OR site:openai.com",
            "durable AI agent execution human in the loop site:langchain.com OR site:microsoft.com",
            "AI agent observability evaluation tracing site:openai.com OR site:langchain.com",
        ),
    ),
    (
        "geopolitics_ukraine",
        (
            "俄乌",
            "乌克兰",
            "俄乌战争",
            "俄乌局势",
            "俄军",
            "乌军",
            "russia ukraine",
            "ukraine war",
            "russian invasion",
            "ukraine front",
            "russian offensive",
            "isw",
        ),
        (
            "Russia Ukraine latest situation ISW site:understandingwar.org",
            "Russia Ukraine civilian casualties humanitarian impact latest site:un.org",
            "Russia Ukraine war latest Reuters AP BBC Al Jazeera (site:reuters.com OR site:apnews.com OR site:bbc.com OR site:aljazeera.com)",
        ),
    ),
    (
        "gold_market",
        (
            "金价",
            "黄金价格",
            "黄金走势",
            "黄金市场",
            "xau",
            "xau/usd",
            "gold price",
            "gold market",
            "spot gold",
        ),
        (
            "gold price benchmark latest site:lbma.org.uk",
            "gold demand trends latest site:gold.org",
            "XAU USD gold price latest site:investing.com",
        ),
    ),
)


# 单次 research 内 JS 渲染触发计数(护 deepresearch 300s 预算,见 plan WI-3 双闸②)。
_JS_RENDER_MAX_PER_RUN = 4


_JS_RENDER_MIN_SHELL_HTML = 20_000  # 原始 HTML > 此值 + trafilatura 短 = 疑 JS 空壳(双闸①)


@dataclass(slots=True)
class _StandaloneJSRenderBudget:
    """Compatibility budget for callers using ``default_extract`` directly.

    DeepResearch runs pass an explicit workflow ``FetchPort`` instead.  This
    task-local fallback keeps the public extractor useful without restoring a
    process-global counter that concurrent runs could corrupt.
    """

    limit: int = _JS_RENDER_MAX_PER_RUN
    used: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def claim_js_render(self) -> bool:
        async with self.lock:
            if self.used >= self.limit:
                return False
            self.used += 1
            return True


_standalone_js_budget: contextvars.ContextVar[_StandaloneJSRenderBudget | None] = (
    contextvars.ContextVar("deskpet_research_js_budget", default=None)
)


def _reset_js_render_budget() -> None:
    """Reset the task-local compatibility budget for direct extractor calls."""
    _standalone_js_budget.set(_StandaloneJSRenderBudget())


async def _claim_js_render(fetch_port: object | None) -> bool:
    budget = fetch_port
    if budget is None:
        budget = _standalone_js_budget.get()
        if budget is None:
            budget = _StandaloneJSRenderBudget()
            _standalone_js_budget.set(budget)
    claim = getattr(budget, "claim_js_render", None)
    if claim is None:
        raise TypeError("fetch_port must provide async claim_js_render()")
    return bool(await claim())


# DuckDuckGo HTML SERP. Returns plain HTML (no JS needed). The /html
# subdomain is the "lite" / no-JS path; results are stable across years.
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"


# A bounded UA so DuckDuckGo doesn't aggressively bot-check us.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120 Safari/537.36 DeskPet/0.5 (+research)"
)


# Per-host fetch politeness — we hand work off to web_tools for the
# real extract step; this client only hits DuckDuckGo.
_DEFAULT_TIMEOUT = 12.0


# Authority bonus map — small, hand-curated. Higher = more trustworthy.
# Anything not listed gets ``_DEFAULT_AUTHORITY``. Tuned for the
# typical "research a topic" use case: academic + reference + .gov >
# named news > random blogs > content farms (we don't try to block
# them, just down-weight).
_AUTHORITY: dict[str, float] = {
    "wikipedia.org": 1.5,
    "nature.com": 1.6,
    "arxiv.org": 1.5,
    "github.com": 1.3,
    "stackoverflow.com": 1.2,
    "mit.edu": 1.5,
    "stanford.edu": 1.5,
    "ox.ac.uk": 1.5,
    "harvard.edu": 1.5,
    "nih.gov": 1.6,
    "europa.eu": 1.4,
    "who.int": 1.4,
    "ietf.org": 1.5,
    "developer.mozilla.org": 1.4,
    "docs.python.org": 1.5,
    "python.org": 1.4,
    "anthropic.com": 1.3,
    "openai.com": 1.3,
    # Common low-quality patterns — slight penalty rather than ban
    "quora.com": 0.7,
    "medium.com": 0.85,
    "csdn.net": 0.8,
    "zhihu.com": 0.95,
}


_DEFAULT_AUTHORITY = 1.0


async def default_search(
    query: str,
    *,
    max_results: int = 5,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """DuckDuckGo HTML SERP → ``[{url, title, snippet}]``.

    Thin wrapper over the unified :mod:`search_provider` (region-aware:
    Chinese queries now hit the 中文区 instead of the old hardcoded
    us-en). No API key. Failure → empty list.
    """

    return await search_provider.search_async(
        query, max_results=max_results, timeout=_DEFAULT_TIMEOUT, client=client
    )


async def gateway_search_response(
    query: str,
    *,
    max_results: int = 20,
    run_id: str | None = None,
):
    """V2/native adapter exposing request-local diagnostics.

    The v1 ``default_search`` list contract remains unchanged above. Native
    workflows can consume this response without touching legacy process-global
    ``_last_*`` observations.
    """

    return await runtime.get_default_gateway().search(
        SearchRequest(
            query=query,
            max_results=max_results,
            mode="research",
            run_id=run_id,
        )
    )


def _parse_ddg_results(html: str, *, max_results: int) -> list[dict[str, Any]]:
    """Pure parser — easier to unit-test without network."""
    out: list[dict[str, Any]] = []
    # Try selectolax first (fast); fall back to regex if missing.
    try:
        # 按需导入：可选依赖
        from selectolax.parser import HTMLParser  # type: ignore
    except ImportError:
        return _parse_ddg_results_regex(html, max_results=max_results)

    tree = HTMLParser(html)
    # DDG result blocks: <div class="result results_links_deep ...">
    nodes = tree.css("div.result")
    for node in nodes:
        if len(out) >= max_results:
            break
        a = node.css_first("a.result__a")
        s = node.css_first(".result__snippet")
        if a is None:
            continue
        url = a.attributes.get("href", "")
        title = (a.text() or "").strip()
        snippet = (s.text() if s else "").strip()
        cleaned_url = _clean_ddg_url(url)
        if not cleaned_url or not title:
            continue
        out.append({"url": cleaned_url, "title": title, "snippet": snippet})
    return out


def _parse_ddg_results_regex(html: str, *, max_results: int) -> list[dict[str, Any]]:
    """Regex fallback for environments without selectolax (shouldn't happen
    in dev — listed as fallback for defensive coding)."""
    pattern = re.compile(
        r'<a class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_re = re.compile(
        r'<a class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    urls = pattern.findall(html)
    snippets = snippet_re.findall(html)
    out: list[dict[str, Any]] = []
    for i, (url, title_html) in enumerate(urls):
        if len(out) >= max_results:
            break
        title = re.sub(r"<[^>]+>", "", title_html).strip()
        snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
        cleaned_url = _clean_ddg_url(url)
        if not cleaned_url or not title:
            continue
        out.append({"url": cleaned_url, "title": title, "snippet": snippet})
    return out


def _clean_ddg_url(url: str) -> str:
    """DDG wraps real URLs in /l/?uddg=ENCODED. Unwrap to the real URL."""
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if "duckduckgo.com/l/" in url or url.startswith("/l/"):
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        uddg = qs.get("uddg", [""])[0]
        if uddg:
            return urllib.parse.unquote(uddg)
    return url


# ── P1-3 二级抓取: Jina Reader (r.jina.ai) ─────────────────────────────
# trafilatura 只解析静态 HTML;现代 JS/SPA 站正文是浏览器跑 JS 才出来的,
# 原始 HTML 是空壳 → trafilatura 抽不到。r.jina.ai 在它服务器上用真浏览器跑完
# JS、返回干净 Markdown,作为 trafilatura 抽空/过短时的二级兜底。
# ⚠️ 真机实测: r.jina.ai 是【国外服务,中国大陆需代理】(直连 ConnectError),
# 对裸中国用户连不上,只对有代理的用户有效 → 故【默认关】(opt-in),且超时缩到
# 8s 快速失败,避免裸中国用户每个 JS 页白等。有代理可 [research].jina_reader=true 开。
_JINA_READER_BASE = "https://r.jina.ai/"


_JINA_MIN_CHARS = 300  # trafilatura 正文短于此 → 疑似 JS 空壳,试 Jina


_JINA_TIMEOUT = 8.0  # 快速失败(国外服务,无代理直接连不上)


def _parse_jina(body: str) -> dict[str, str]:
    """r.jina.ai 返回形如 ``Title: ...\\nURL Source: ...\\nMarkdown Content:\\n<正文>``;
    也可能直接是 Markdown。解析出 {title, text}。"""
    title = ""
    text = body or ""
    m = re.search(r"^Title:\s*(.+)$", body, re.MULTILINE)
    if m:
        title = m.group(1).strip()
    mc = body.find("Markdown Content:")
    if mc != -1:
        text = body[mc + len("Markdown Content:") :].strip()
    return {"title": title, "text": text.strip()}


def _host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def authority_for_url(url: str) -> float:
    """Look up the authority bonus for the URL's host (with parent-domain
    fallback so subdomains inherit). Unknown → :data:`_DEFAULT_AUTHORITY`.
    """
    h = _host(url)
    if not h:
        return _DEFAULT_AUTHORITY
    # Walk from full host down to TLD; return first match
    parts = h.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in _AUTHORITY:
            return _AUTHORITY[candidate]
    return _DEFAULT_AUTHORITY
