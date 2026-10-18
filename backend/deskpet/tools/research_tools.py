# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""DeepResearch orchestrator — multi-stage research with citations.

Pipeline
--------
    deepresearch(topic)
    ├── 1. plan          ─ LLM splits the topic into 3-6 sub-questions
    ├── 2. search        ─ each sub-q → DuckDuckGo HTML SERP → top-N URLs
    ├── 3. fetch+extract ─ concurrent web_extract_article on those URLs
    ├── 4. score+filter  ─ authority × content-length × keyword-coverage
    ├── 5. synthesize    ─ LLM merges high-score passages into a Markdown
    │                       report with inline footnote refs [^n]
    ├── 6. cite_check    ─ every [^n] must map to a real Citation entry
    └── 7. return        ─ ResearchReport dataclass

Design constraints
------------------
* No paid search APIs (DuckDuckGo HTML endpoint is free + permissive).
* Authority list is a small per-domain bonus map — we don't try to
  rank Wikipedia vs Quora "scientifically"; we just nudge.
* Every claim in the final report MUST cite at least one source. The
  ``cite_check`` step rejects reports with dangling footnotes.
* LLM failure / web failure → return whatever stage succeeded with
  ``coverage`` reflecting the partial state. We never fabricate
  conclusions.

This module is **library-only** — no FastAPI / IPC glue. The SKILL.md
side calls the orchestrator via the ToolRegistry façade.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from llm.context_messages import provider_purpose_scope
from paths import deepresearch_dir

from deskpet.retrieval import runtime
from deskpet.retrieval.contracts import FetchRequest
from deskpet.retrieval.fetch_extract import FetchExtractError, FetchExtractService
from deskpet.tools.research_fetch import (
    _DEFAULT_TIMEOUT,
    _SOURCE_PACK_RULES,
    _claim_js_render,
    _host,
)
from deskpet.tools.research_report import (
    _DEEPRESEARCH_TOOL_TIMEOUT,
    _DEPTH_PRESETS,
    _EXPAND_PROMPT,
    _FANOUT_OUTER_RESERVE,
    _FANOUT_SYNTH_PROMPT,
    _GAP_PROMPT,
    _INDEX_HEADER,
    _MIN_SUBRUN,
    _RERANK_PROMPT,
    _RESEARCH_SCHEMA,
    _SUBREPORT_MONITOR_PROMPT,
    _SYNTH_PROMPT,
    Citation,
    FanoutCollection,
    Passage,
    ResearchReport,
    _aggregate_fanout_route,
    _extract_summary,
    _Extractor,
    _finalize_report_md,
    _insert_row_after_header,
    _is_cjk,
    _LLMCall,
    _no_results_template,
    _parse_rerank_scores,
    _passages_only_fallback,
    _rewrite_local_refs,
    _Searcher,
    _strip_footnote_definitions,
    _subreport_quality,
    _tier_label,
    parse_sub_questions,
)

from . import research_cdp_edge as _ce
from . import research_core, research_scoring
from .capabilities import ToolExecutionContext
from .context_adapter import bind_context_handler, legacy_execution_context
from .office_paths import title_slug
from .scrapling_tools import _scrapling_get_html

log = logging.getLogger(__name__)
_CHILD_PROGRESS_TIMEOUT = 5.0


# Optional BGE-M3 semantic relevance hook. ``main.py`` may inject a
# ``(query: str, passages: list[str]) -> list[float]`` scorer (cosine in
# [0,1]) so relevance uses real semantics. Unset → keyword coverage only
# (graceful degrade, no embedder dependency in tests).
_SEMANTIC_SCORER = None


# Optional LLM-as-reranker (默认精排手段)。main.py 注入一个【廉价模型】
# (如 gpt-4.1-mini) 的 (prompt:str)->str 调用,research 召回后用它对候选段落做
# cross-encoder 式精排 —— 复用中转站 relay,免下载本地 bge-reranker 模型/免占本地
# 内存。未注入 → deepresearch 跳过精排(不回退主 llm,省 token)。模式由
# [research].reranker 配置门控: "llm"(默认) / "local"(本地 bge-reranker,
# Phase-future) / "off"。
_RERANK_LLM_CALL: _LLMCall | None = None


def set_rerank_llm_call(fn: _LLMCall | None) -> None:
    """Wire a cheap-model rerank LLM into deep-research (called from main.py)."""
    global _RERANK_LLM_CALL
    _RERANK_LLM_CALL = fn


def _source_packs_enabled() -> bool:
    """``[research].source_packs`` (默认 True)。"""
    return bool(_research_raw().get("source_packs", True))


def _source_pack_max_queries_per_question() -> int:
    """Bound source-pack expansion so broad plans do not explode search cost."""
    try:
        n = int(_research_raw().get("source_pack_max_queries_per_question", 3))
    except (TypeError, ValueError):
        n = 3
    return max(0, min(n, 6))


def _source_pack_queries_for(text: str) -> list[tuple[str, str]]:
    """Return ``[(pack_name, source_query)]`` for source packs matching text."""
    if not _source_packs_enabled():
        return []
    t = (text or "").lower()
    if not t:
        return []
    limit = _source_pack_max_queries_per_question()
    if limit <= 0:
        return []
    out: list[tuple[str, str]] = []
    for pack_name, keywords, suffixes in _SOURCE_PACK_RULES:
        if not any(kw in t for kw in keywords):
            continue
        for source_query in suffixes:
            out.append((pack_name, source_query))
            if len(out) >= limit:
                return out
    return out


# [research] 配置读取 —— 统一缓存入口。
# 历史 bug(真机 UI 测 TC-P2-05 发现): 旧代码各处 `import config as _cfg;
# _cfg.config.raw.get("research")` 读配置,但 config 模块并**没有** `config`
# 属性(loaded AppConfig 是 main.py 的 main.config 全局,不是 config 模块属性),
# → 每次 AttributeError 被 except 吞掉 → 所有 [research] 开关恒取默认,关不掉
# (direct_sources=false 被忽略,cninfo 照常直连)。改为正确解析配置文件并缓存:
# 优先用已发布的单例(若某运行模式设了),否则 load_config(resolve_config_path())。
_RESEARCH_RAW_CACHE: dict | None = None


def _research_raw() -> dict:
    """返回 ``[research]`` 段(dict),进程内缓存。读不到返回 {}。"""
    global _RESEARCH_RAW_CACHE
    if _RESEARCH_RAW_CACHE is None:
        raw: dict = {}
        try:
            # 按需导入：测试替身（config）
            import config as _cfg  # type: ignore[import-not-found]

            obj = getattr(_cfg, "config", None)  # 若有发布的单例优先用
            if obj is not None and hasattr(obj, "raw"):
                raw = obj.raw.get("research") or {}
            else:
                cfg = _cfg.load_config(_cfg.resolve_config_path())
                raw = cfg.raw.get("research") or {}
        except Exception:  # noqa: BLE001
            log.debug("research_raw_import_failed", exc_info=True)
            raw = {}
        _RESEARCH_RAW_CACHE = raw if isinstance(raw, dict) else {}
    return _RESEARCH_RAW_CACHE


_SUBAGENT_SCHEDULER = None


def set_subagent_scheduler(s) -> None:
    """Wire the process-global SubagentScheduler for deepresearch fan-out."""
    global _SUBAGENT_SCHEDULER
    _SUBAGENT_SCHEDULER = s


def get_subagent_scheduler():
    """Return the wired SubagentScheduler, if any."""
    return _SUBAGENT_SCHEDULER


def _fanout_enabled() -> bool:
    # 测试阶段：已完成并通过真机 E2E 的能力出厂即开启。
    return bool(_research_raw().get("subagent_fanout", True))


def _fanout_min_subquestions() -> int:
    try:
        return int(_research_raw().get("fanout_min_subquestions", 2))
    except (TypeError, ValueError):
        return 2


def _fanout_max_subquestions() -> int:
    try:
        return int(_research_raw().get("fanout_max_subquestions", 6))
    except (TypeError, ValueError):
        return 6


def _fanout_subrun_mode(outer: str) -> str:
    v = str(_research_raw().get("fanout_subrun_mode", "auto") or "auto").strip().lower()
    outer = (outer or "standard").strip().lower()
    if v == "auto":
        if outer == "deep":
            return "standard"
        if outer == "standard":
            return "light"
        return "light"
    if v in ("light", "standard", "deep"):
        return v
    if v == "inherit":
        return outer if outer in _DEPTH_PRESETS else "standard"
    return "light"


def _fanout_concurrency() -> int:
    try:
        # 按需导入：测试替身（config）
        import config as _cfg  # type: ignore[import-not-found]

        obj = getattr(_cfg, "config", None)
        if obj is None:
            obj = _cfg.load_config(_cfg.resolve_config_path())
        glob, lanes = _cfg.get_subagent_concurrency(obj)
        return max(1, min(int(glob), int((lanes or {}).get("research", 2))))
    except Exception:  # noqa: BLE001
        log.debug("fanout_concurrency_import_failed", exc_info=True)
        return 2


def _site_directed_enabled() -> bool:
    """``[research].site_directed`` (默认 True)。纯 prompt/query 改动,零成本,
    可关。"""
    return bool(_research_raw().get("site_directed", True))


# P2 multi-query / HyDE 查询扩展 + 中文一手源直连开关 ----------------
def _query_expansion_enabled() -> bool:
    """``[research].query_expansion`` (默认 True)。纯 LLM 零外部依赖,中国友好。"""
    return bool(_research_raw().get("query_expansion", True))


def _direct_sources_enabled() -> bool:
    """``[research].direct_sources`` (默认 True)。巨潮/国标 中国可直连。"""
    return bool(_research_raw().get("direct_sources", True))


# JS 渲染抓取兜底开关(治 JS/SPA 空壳站)。默认关(opt-in,避免对正常短页误触发重渲染)。
# 本期(Option C)落地引擎 = cdp-edge(连系统 Edge 无头,纯后端,POC 验证,Windows);
# webview(三端复用 Tauri 内核)为下一期主线,本期未实现 → 选它时按平台降级。
def _js_render_enabled() -> bool:
    """``[research].js_render`` (默认 False / opt-in)。"""
    return bool(_research_raw().get("js_render", False))


def _js_render_engine() -> str:
    """``[research].js_render_engine`` ∈ {cdp-edge, webview, crawl4ai}。

    真实默认按平台给"本期能用"的引擎: Windows → cdp-edge(已落地);非 Win 暂无本期引擎
    → 返回配置原值(webview/crawl4ai),由 default_extract 路由时降级。配置显式指定则尊重之。"""
    v = str(_research_raw().get("js_render_engine", "") or "").strip().lower()
    if v in ("cdp-edge", "cdp_edge", "cdpedge"):
        return "cdp-edge"
    if v in ("webview", "crawl4ai"):
        return v
    # 未配置: Windows 默认走已落地的 cdp-edge,其它平台留 webview(本期未实现→降级)
    return "cdp-edge" if sys.platform == "win32" else "webview"


def _js_render_timeout() -> float:
    """``[research].js_render_timeout`` (秒,默认 20)。"""
    try:
        return float(_research_raw().get("js_render_timeout", 20.0) or 20.0)
    except (TypeError, ValueError):
        return 20.0


async def _js_render_dispatch(url: str) -> str | None:
    """按 ``_js_render_engine()`` 路由渲染 url → 渲染后 HTML 字符串;不可用/失败返 None。
    best-effort,绝不抛(失败回落 jina/原结果)。"""
    engine = _js_render_engine()
    timeout = _js_render_timeout()
    try:
        if engine == "cdp-edge":
            return await _ce.cdp_edge_render(url, timeout=timeout)
        if engine == "crawl4ai":
            from . import research_crawl4ai as _c4  # dev 档,懒 import

            r = await _c4.crawl4ai_extract(url, timeout=timeout)
            return (r or {}).get("html") if isinstance(r, dict) else None
        # webview: 三端主线,本期(Option C)未实现 → 降级(返 None 走 jina/原结果)
        log.debug("js_render engine=webview 本期未实现,跳过 (url=%s)", url)
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("js_render dispatch failed engine=%s url=%s: %s", engine, url, exc, exc_info=True)
        return None


async def _expand_queries(
    llm_call: _LLMCall,
    topic: str,
    sub_questions: list[str],
    errors: list[str],
    *,
    max_extra: int = 4,
) -> list[str]:
    """multi-query + HyDE: 一次 LLM 调用产出≤4 条额外搜索 query。
    best-effort,失败/解析空 → []。去掉与原子问题重复的。"""
    subqs = "\n".join(f"- {q}" for q in sub_questions) or "(none)"
    try:
        raw = await llm_call(_EXPAND_PROMPT.format(topic=topic, subqs=subqs))
    except Exception as exc:  # noqa: BLE001
        log.debug("expand_queries_llm_call_failed", exc_info=True)
        errors.append(f"query_expansion: {exc}")
        return []
    qs = parse_sub_questions(raw, max_questions=max_extra)
    seen = {q.strip().lower() for q in sub_questions} | {topic.strip().lower()}
    return [q for q in qs if q.strip().lower() not in seen][:max_extra]


def _rerank_mode() -> str:
    """``[research].reranker`` ∈ {llm(默认), local, off}。

    "local"(本地 bge-reranker)是 plan 文档里的 Phase-future 可选档,尚未实现 →
    当前退化为 "llm"(中转站重排),保证开关存在、行为安全。"""
    v = str(_research_raw().get("reranker", "llm")).strip().lower()
    if v == "off":
        return "off"
    if v in ("llm", "local"):
        return "llm"  # local(本地 bge-reranker)暂未实现 → 安全退化 llm
    # 非法/空白值 → 默认 llm(保守开启),记一条 debug 便于排查
    log.debug("research: unknown [research].reranker=%r → using 'llm'", v)
    return "llm"


_RERANK_TIMEOUT = 25.0  # 独立超时:rerank 模型卡住不拖垮整个 deepresearch


async def _llm_rerank(
    topic: str,
    passages: list[Passage],
    rerank_call: _LLMCall,
    errors: list[str],
    *,
    velocity: str,
) -> bool:
    """Cross-encoder-style精排 via a cheap relay model: score each candidate
    0-10 for relevance, set it as the relevance dim, recompute composite.

    Best-effort — 失败/超时/解析空/覆盖率过低 → 保留原打分,返回 ``False``
    (调用方据此把 coverage.reranker 标 'llm_failed' 而非误标 'llm')。
    成功应用 → 返回 ``True``。"""
    if not passages:
        return False
    # 用【列表位置 1..N】作候选 id —— 此处 citation.n 还是 0(重编号在 rerank
    # 之后),不能用 c.n。
    n = len(passages)
    lines: list[str] = []
    for idx, p in enumerate(passages, start=1):
        c = p.citation
        head = (p.text[:200].replace("\n", " ")).strip()
        lines.append(f"{idx} | {_tier_label(c.authority)} | {c.title[:80]} | {head}")
    try:
        raw = await asyncio.wait_for(
            rerank_call(_RERANK_PROMPT.format(topic=topic, candidates="\n".join(lines))),
            timeout=_RERANK_TIMEOUT,
        )
    except TimeoutError:
        errors.append("rerank_timeout")
        return False
    except Exception as exc:  # noqa: BLE001
        log.debug("llm_rerank_wait_for_failed", exc_info=True)
        errors.append(f"rerank_llm: {exc}")
        return False
    scores = _parse_rerank_scores(raw)
    # 只保留 1..n 的合法 id(丢超界;重复已在 parse 去重);覆盖率过低视为模型没
    # 认真打分 → 整次 no-op。小候选集(≤3)要求全量覆盖,大集合 ceil(n/2)。
    scores = {i: s for i, s in scores.items() if 1 <= i <= n}
    need = n if n <= 3 else (n + 1) // 2
    if len(scores) < need:
        errors.append(f"rerank_low_coverage:{len(scores)}/{n}")
        return False
    for idx, p in enumerate(passages, start=1):
        s = scores.get(idx)
        if s is None:
            continue
        rel = max(0.0, min(10.0, float(s)))
        d = p.dims or {}
        d["relevance"] = rel
        d["llm_rerank_verified"] = True
        p.dims = d
        p.score = research_scoring.composite_score(
            authority=float(d.get("authority", 3.0)),
            recency=float(d.get("recency", 3.0)),
            relevance=rel,
            depth=float(d.get("depth", 0.0)),
            topic_velocity=velocity,
        )
    return True


# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------


_INDEX_LOCK = asyncio.Lock()

# ----------------------------------------------------------------------
# Public dataclasses
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# Protocols / type aliases
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# Default network implementations — both can be swapped in tests
# ----------------------------------------------------------------------


def _jina_enabled() -> bool:
    """``[research].jina_reader`` (默认 False / opt-in)。r.jina.ai 国外需代理,
    默认关;有代理的用户显式开。best-effort。"""
    return bool(_research_raw().get("jina_reader", False))


def _scrapling_fetch_html(url: str, timeout: float = _DEFAULT_TIMEOUT) -> dict[str, Any] | None:
    """Fetch HTML with Scrapling for real deepresearch runs.

    The indirection keeps Scrapling optional at import time and lets tests
    monkeypatch this layer without pulling in browser-like fetchers.
    """
    try:
        fetched = _scrapling_get_html(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        log.debug("scrapling extract fetch unavailable for %s: %s", url, exc, exc_info=True)
        return None
    if not fetched.get("ok"):
        log.debug("scrapling extract fetch failed for %s: %s", url, fetched.get("error"))
        return None
    html = str(fetched.get("html") or "")
    status = int(fetched.get("status") or 0)
    if not html or not (200 <= status < 300):
        return None
    return fetched


async def default_extract(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    fetch_port: object | None = None,
) -> dict[str, Any]:
    """Thin DeepResearch adapter over the shared FetchExtractService."""

    service = runtime.get_default_gateway().fetch_service
    owns_service = False
    if client is not None:

        class _InjectedClientTransport:
            def fetch(self, url: str, *, timeout: float):
                return {"ok": False, "error": "injected_client"}

        service = FetchExtractService(
            transport=_InjectedClientTransport(),
            client=client,
            respect_robots=False,
            request_interval_ms=0,
            allow_jina=_jina_enabled(),
            render_call=_js_render_dispatch,
        )
        owns_service = True
    try:
        render_enabled = _js_render_enabled() and _js_render_engine() == "cdp-edge"

        class _LegacyRenderBudget:
            async def claim_cdp(self) -> bool:
                return await _claim_js_render(fetch_port)

        render_budget = _LegacyRenderBudget() if render_enabled else None
        document = await service.fetch(
            FetchRequest(
                url=url,
                timeout=_DEFAULT_TIMEOUT,
                render_policy="auto" if render_enabled else "never",
                request_budget=render_budget,
                allow_jina=_jina_enabled(),
            )
        )
        if not document.text:
            return {"ok": False, "error": "no text extracted", "url": url}
        return {
            "ok": True,
            "url": document.canonical_url,
            "title": document.title or url,
            "text": document.text,
            "fetched_at": time.time(),
            "date": document.published_at or "",
            "ai_generated": "ai_disclosure" in document.quality_flags,
            "extractor": (
                document.fetcher if document.fetcher in {"cdp-edge", "jina"} else document.extractor
            ),
            "fetcher": document.fetcher,
            "content_hash": document.content_hash,
            "quality_flags": list(document.quality_flags),
        }
    except FetchExtractError as exc:
        return {"ok": False, "error": exc.code, "url": url}
    finally:
        if owns_service:
            await service.close()


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------


def _norm_url(u: str) -> str:
    try:
        p = urllib.parse.urlsplit(u)
        return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}".lower()
    except Exception:  # noqa: BLE001
        log.debug("norm_url_urlsplit_failed", exc_info=True)
        return (u or "").rstrip("/").lower()


# ----------------------------------------------------------------------
# Cite check
# ----------------------------------------------------------------------


def _merge_subreport_citations(sub_reports) -> tuple[list[Citation], dict[tuple[int, int], int]]:
    """Merge sub-report citations by normalized URL and build local→global refs."""
    merged: list[Citation] = []
    url_to_global: dict[str, int] = {}
    refmap: dict[tuple[int, int], int] = {}
    for report_idx, item in enumerate(sub_reports):
        report = item[1] if isinstance(item, tuple) else item
        for c in getattr(report, "citations", []) or []:
            key = _norm_url(c.url)
            if key in url_to_global:
                refmap[(report_idx, int(c.n))] = url_to_global[key]
                continue
            global_n = len(merged) + 1
            url_to_global[key] = global_n
            refmap[(report_idx, int(c.n))] = global_n
            merged.append(
                Citation(
                    n=global_n,
                    url=c.url,
                    title=c.title,
                    snippet=c.snippet,
                    fetched_at=c.fetched_at,
                    authority=c.authority,
                )
            )
    return merged, refmap


async def _fanout_synthesize(
    topic: str,
    user_request: str,
    sub_reports: list[tuple[str, ResearchReport]],
    llm_call: _LLMCall,
    errors: list[str],
) -> tuple[str, list[Citation]]:
    request_topic = (user_request or topic).strip()
    merged, refmap = _merge_subreport_citations(sub_reports)
    sections: list[str] = []
    for report_idx, (q, report) in enumerate(sub_reports):
        local_to_global = {
            local_n: global_n for (idx, local_n), global_n in refmap.items() if idx == report_idx
        }
        body = _strip_footnote_definitions(report.report_md)
        body = _rewrite_local_refs(body, local_to_global)
        sections.append(f"### {q}\n\n{body}")

    sub_report_block = "\n\n---\n\n".join(sections)
    citations_block = "\n".join(c.as_footnote() for c in merged) or "(none)"
    try:
        report_md = await llm_call(
            _FANOUT_SYNTH_PROMPT.format(
                topic=topic,
                request_topic=request_topic,
                user_request=user_request,
                sub_reports=sub_report_block,
                citations=citations_block,
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("fanout_synthesize_llm_call_failed", exc_info=True)
        errors.append(f"fanout_synth_llm: {exc}")
        report_md = f"# {request_topic}\n\n" + sub_report_block

    report_md = (report_md or "").strip()
    if not report_md:
        report_md = f"# {request_topic}\n\n" + sub_report_block
    report_md, merged, _cc = _finalize_report_md(report_md, merged, errors)
    return report_md, merged


async def _diagnose_subreport_retry(
    llm_call: _LLMCall,
    *,
    question: str,
    reason_code: str,
    report: object,
    error: BaseException | None,
) -> tuple[str, str]:
    coverage = dict(report.coverage) if isinstance(report, ResearchReport) else {}
    child_errors = list(report.errors or []) if isinstance(report, ResearchReport) else []
    if error is not None:
        child_errors.append(type(error).__name__)
    fallback = (
        f"子方向返回 {reason_code}；需要补充可访问、可引用的独立来源。",
        "重新规划更具体的检索词，优先官方/一手来源；至少取得一个可引用来源后再成文。",
    )
    try:
        raw = await llm_call(
            _SUBREPORT_MONITOR_PROMPT.format(
                question=question,
                reason_code=reason_code,
                errors=json.dumps(child_errors[:6], ensure_ascii=False),
                coverage=json.dumps(coverage, ensure_ascii=False, sort_keys=True)[:1200],
            )
        )
        lb, rb = raw.find("{"), raw.rfind("}")
        payload = json.loads(raw[lb : rb + 1]) if 0 <= lb < rb else {}
        diagnosis = str(payload.get("diagnosis") or "").strip()
        continuation = str(payload.get("continuation") or "").strip()
        if diagnosis and continuation:
            return diagnosis[:400], continuation[:800]
    except Exception as exc:  # noqa: BLE001 - deterministic fallback is required
        log.debug("deepresearch child diagnosis fallback: %s", exc, exc_info=True)
    return fallback


async def collect_subagent_research(
    *,
    topic: str,
    sub_questions: list[str],
    llm_call: _LLMCall,
    search: _Searcher,
    extract: _Extractor,
    scheduler,
    parent_sid: str,
    mode: str,
    route: dict[str, Any],
    max_attempts: int = 2,
    subrun_mode: str | None = None,
    simple_children: bool = False,
    progress_callback: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
) -> FanoutCollection:
    """Run, inspect, and selectively continue independent research children."""

    if scheduler is None:
        raise RuntimeError("subagent_scheduler_unavailable")
    conc = _fanout_concurrency()
    max_waves = int((_DEEPRESEARCH_TOOL_TIMEOUT - _FANOUT_OUTER_RESERVE) // _MIN_SUBRUN)
    cap = min(_fanout_max_subquestions(), conc * max_waves)
    eff_subq = sub_questions[:cap]
    collected_errors: list[str] = []
    if len(eff_subq) < len(sub_questions):
        collected_errors.append(f"fanout_dropped_subquestions:{len(sub_questions) - len(eff_subq)}")
    requested_submode = str(subrun_mode or "").strip().lower()
    submode = (
        requested_submode if requested_submode in _DEPTH_PRESETS else _fanout_subrun_mode(mode)
    )
    waves = max(1, (len(eff_subq) + conc - 1) // conc)
    timeout = min(150.0, (_DEEPRESEARCH_TOOL_TIMEOUT - _FANOUT_OUTER_RESERVE) / waves)
    attempt_cap = max(1, min(3, int(max_attempts)))
    snapshot_lock = asyncio.Lock()
    snapshot_by_child: dict[str, dict[str, Any]] = {
        f"dr-{i}": {
            "child_id": f"dr-{i}",
            "question": question,
            "status": "queued",
            "attempt": 0,
            "max_attempts": attempt_cap,
            "n_sources": 0,
            "reason_code": "",
        }
        for i, question in enumerate(eff_subq)
    }

    async def _publish_snapshot(
        i: int | None = None,
        *,
        status: str | None = None,
        attempt: int | None = None,
        n_sources: int | None = None,
        reason_code: str | None = None,
    ) -> None:
        if progress_callback is None:
            return
        async with snapshot_lock:
            if i is not None:
                child_id = f"dr-{i}"
                current = snapshot_by_child[child_id]
                if status is not None:
                    current["status"] = status
                if attempt is not None:
                    current["attempt"] = attempt
                if n_sources is not None:
                    current["n_sources"] = max(0, n_sources)
                if reason_code is not None:
                    current["reason_code"] = reason_code
            snapshot = [copy.deepcopy(snapshot_by_child[key]) for key in snapshot_by_child]
            try:
                await asyncio.wait_for(progress_callback(snapshot), timeout=_CHILD_PROGRESS_TIMEOUT)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # progress must never change research results
                log.warning(
                    "deepresearch_child_progress_emit_failed parent=%s error=%s",
                    parent_sid,
                    type(exc).__name__,
                    exc_info=True,
                )

    await _publish_snapshot()

    async def _run_one(i: int, question: str):
        diagnosis = ""
        continuation = ""
        attempts: list[dict[str, Any]] = []
        accepted: ResearchReport | None = None
        local_errors: list[str] = []
        last_reason_code = "not_started"
        last_coverage: dict[str, Any] = {}
        last_child_errors: list[str] = []
        started = time.perf_counter()
        for attempt in range(1, attempt_cap + 1):
            await _publish_snapshot(
                i,
                status="running" if attempt == 1 else "retrying",
                attempt=attempt,
                reason_code="",
            )
            attempt_mode = "standard" if attempt > 1 and submode == "light" else submode
            _, d_urls, d_pass, d_rounds = _DEPTH_PRESETS[attempt_mode]
            research_topic = (
                question
                if attempt == 1
                else f"{question}\n\nMANAGER CONTINUATION INSTRUCTION:\n{continuation}"
            )

            async def _coro():
                if simple_children:
                    return await asyncio.wait_for(
                        _research_subdirection(
                            research_topic,
                            request_topic=question,
                            parent_topic=topic,
                            llm_call=llm_call,
                            search=search,
                            extract=extract,
                            max_urls=d_urls,
                            max_passages=d_pass,
                        ),
                        timeout=timeout,
                    )
                return await asyncio.wait_for(
                    deepresearch(
                        research_topic,
                        llm_call=llm_call,
                        search=search,
                        extract=extract,
                        max_sub_questions=1,
                        max_urls_per_query=d_urls,
                        max_total_passages=d_pass,
                        max_rounds=d_rounds,
                        mode=submode,
                        # A manager continuation must be authoritative for the
                        # retry.  Passing the original question here silently
                        # caused the planner to ignore the continuation.
                        user_request=research_topic,
                        scheduler=None,
                        _depth=1,
                        skip_plan=(attempt == 1),
                    ),
                    timeout=timeout,
                )

            result: object = None
            failure: BaseException | None = None
            try:
                result = await scheduler.run(
                    kind="research",
                    run_id=f"{parent_sid}.dr-{i}.a{attempt}",
                    task_id=f"dr-{i}-a{attempt}",
                    parent_sid=parent_sid,
                    coro_factory=_coro,
                )
            except BaseException as exc:  # isolate sibling failures; cancellation still re-raised
                if isinstance(exc, asyncio.CancelledError):
                    raise
                failure = exc
            status, reason_code = _subreport_quality(result)
            last_reason_code = reason_code
            if isinstance(result, ResearchReport):
                coverage = result.coverage or {}
                last_coverage = {
                    key: copy.deepcopy(coverage[key])
                    for key in (
                        "n_sources",
                        "n_domains",
                        "rounds",
                        "n_dropped_by_reason",
                        "source_discovery",
                    )
                    if key in coverage
                }
                last_child_errors = [str(value) for value in (result.errors or [])[-8:]]
            if failure is not None:
                status, reason_code = "retryable", type(failure).__name__
                last_reason_code = reason_code
                last_child_errors = [type(failure).__name__]
                local_errors.append(f"fanout:{question!r}: {failure}")
            attempts.append(
                {
                    "attempt": attempt,
                    "status": status,
                    "reason_code": reason_code,
                    "n_sources": len(result.citations) if isinstance(result, ResearchReport) else 0,
                }
            )
            if status == "valid" and isinstance(result, ResearchReport):
                accepted = result
                local_errors.extend(result.errors or [])
                break
            if attempt < attempt_cap:
                await _publish_snapshot(
                    i,
                    status="retrying",
                    attempt=attempt,
                    n_sources=len(result.citations) if isinstance(result, ResearchReport) else 0,
                    reason_code=reason_code,
                )
                diagnosis, continuation = await _diagnose_subreport_retry(
                    llm_call,
                    question=question,
                    reason_code=reason_code,
                    report=result,
                    error=failure,
                )

        final_status = "valid" if accepted is not None else "insufficient"
        record = {
            "child_id": f"dr-{i}",
            "question": question,
            "status": final_status,
            "attempt": len(attempts),
            "attempts": attempts,
            "diagnosis": diagnosis,
            "reason_code": "ok" if accepted is not None else last_reason_code,
            "last_coverage": last_coverage,
            "last_errors": last_child_errors,
            "n_sources": len(accepted.citations) if accepted is not None else 0,
            "n_domains": int((accepted.coverage or {}).get("n_domains", 0)) if accepted else 0,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        log.info(
            "deepresearch_child_result parent=%s child=%s status=%s attempt=%s sources=%s reason=%s",
            parent_sid,
            record["child_id"],
            final_status,
            record["attempt"],
            record["n_sources"],
            record["reason_code"],
        )
        await _publish_snapshot(
            i,
            status=final_status,
            attempt=record["attempt"],
            n_sources=record["n_sources"],
            reason_code=record["reason_code"],
        )
        return question, accepted, record, local_errors

    raw_results = await asyncio.gather(
        *[_run_one(i, question) for i, question in enumerate(eff_subq)],
        return_exceptions=True,
    )
    sub_reports: list[tuple[str, ResearchReport]] = []
    child_records: list[dict[str, Any]] = []
    for i, (question, raw) in enumerate(zip(eff_subq, raw_results)):
        if isinstance(raw, BaseException):
            if isinstance(raw, asyncio.CancelledError):
                raise raw
            collected_errors.append(f"fanout:{question!r}: {raw}")
            child_records.append(
                {
                    "child_id": f"dr-{i}",
                    "question": question,
                    "status": "insufficient",
                    "attempt": 0,
                    "attempts": [],
                    "diagnosis": "",
                    "reason_code": type(raw).__name__,
                    "last_coverage": {},
                    "last_errors": [type(raw).__name__],
                    "failure_class": "fatal",
                    "n_sources": 0,
                    "n_domains": 0,
                    "duration_ms": 0,
                }
            )
            await _publish_snapshot(
                i,
                status="insufficient",
                attempt=0,
                n_sources=0,
                reason_code=type(raw).__name__,
            )
            continue
        _, accepted, record, local_errors = raw
        child_records.append(record)
        collected_errors.extend(local_errors)
        if accepted is not None:
            sub_reports.append((question, accepted))

    return FanoutCollection(
        topic=topic,
        sub_questions=eff_subq,
        sub_reports=sub_reports,
        child_records=child_records,
        errors=collected_errors,
        route=_aggregate_fanout_route(route, sub_reports),
        waves=waves,
        per_subrun_timeout_s=timeout,
    )


async def _research_subdirection(
    query: str,
    *,
    request_topic: str,
    parent_topic: str | None = None,
    llm_call: _LLMCall,
    search: _Searcher,
    extract: _Extractor,
    max_urls: int,
    max_passages: int,
) -> ResearchReport:
    """Focused v7 child: search once, fetch, filter, and synthesize once."""

    original_request = (parent_topic or request_topic).strip()
    errors: list[str] = []
    try:
        hits = await search(query, max_results=max(1, int(max_urls)))
    except Exception as exc:  # noqa: BLE001
        log.debug("research_subdirection_failed", exc_info=True)
        errors.append(f"search:{type(exc).__name__}: {exc}")
        hits = []
    urls: list[str] = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        url = str(hit.get("url") or "").strip()
        if url and url not in urls:
            urls.append(url)
        if len(urls) >= max(1, int(max_urls)):
            break
    source_discovery = "search"
    if not urls:
        # The local search gateway can legitimately degrade when public SERPs
        # present CAPTCHAs or the browser-backed providers time out.  Keep the
        # child small, but let it nominate a bounded set of direct URLs; every
        # URL still has to survive the real fetch and passage gates below.
        errors.append("no search results")
        source_discovery = "llm_verified_urls"
        try:
            raw_urls = await llm_call(
                "You are the research subagent responsible for one focused direction.\n"
                f"Research direction: {query}\n"
                f"Parent topic: {original_request}\n\n"
                f"Return at most {max(1, int(max_urls))} likely direct source URLs. "
                "Prefer official documentation, primary sources, standards, or "
                "well-established technical publications. Return URLs only, one "
                "per line. Do not explain and do not invent tracking parameters."
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("research_subdirection_llm_call_failed", exc_info=True)
            errors.append(f"source_plan:{type(exc).__name__}: {exc}")
            raw_urls = ""
        for match in re.findall(r"https?://[^\s<>\"'\]\)]+", raw_urls or ""):
            url = match.rstrip(".,;:!?`})")
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme in {"http", "https"} and parsed.netloc and url not in urls:
                urls.append(url)
            if len(urls) >= max(1, int(max_urls)):
                break
    if not urls:
        return ResearchReport(
            topic=request_topic,
            summary="",
            report_md=_no_results_template(request_topic, [request_topic]),
            citations=[],
            sub_questions=[request_topic],
            coverage={
                "n_sources": 0,
                "n_domains": 0,
                "pipeline": "focused_child_v7",
                "source_discovery": source_discovery,
            },
            errors=errors,
        )

    payloads = await _gather_safe([extract(url) for url in urls], label="focused_extract")
    state = research_core.ResearchCoreState(
        request_topic=request_topic,
        llm_topic=query,
        mode="focused_child_v7",
        route={},
    )
    state.sub_questions = [request_topic]
    state.velocity = research_scoring.infer_topic_velocity(request_topic)
    config = research_core.ResearchCoreConfig.from_legacy(
        max_sub_questions=1,
        max_urls_per_query=max(1, int(max_urls)),
        max_total_passages=max(1, int(max_passages)),
        min_passage_chars=250,
        max_rounds=1,
    )
    passages: list[Passage] = []
    for url, payload in zip(urls, payloads):
        passage = research_core._passage_from_extract(state, config, url, payload)
        if passage is not None and research_core._passes_topic_anchor_gate(state, passage):
            passages.append(passage)
    errors.extend(state.errors)
    passages.sort(key=lambda passage: passage.score, reverse=True)
    passages = passages[: max(1, int(max_passages))]
    numbered: list[Passage] = []
    for index, passage in enumerate(passages, start=1):
        citation = Citation(
            n=index,
            url=passage.citation.url,
            title=passage.citation.title,
            snippet=passage.citation.snippet,
            fetched_at=passage.citation.fetched_at,
            authority=passage.citation.authority,
        )
        numbered.append(
            Passage(
                citation=citation,
                text=passage.text,
                score=passage.score,
                dims=dict(passage.dims or {}),
            )
        )
    if not numbered:
        errors.append("no usable passages")
        return ResearchReport(
            topic=request_topic,
            summary="",
            report_md=_no_results_template(request_topic, [request_topic]),
            citations=[],
            sub_questions=[request_topic],
            coverage={
                "n_sources": 0,
                "n_domains": 0,
                "pipeline": "focused_child_v7",
                "source_discovery": source_discovery,
                "n_dropped_by_reason": dict(state.dropped_by_reason),
            },
            errors=errors,
        )

    citations = [passage.citation for passage in numbered]
    try:
        report_md = await llm_call(
            _SYNTH_PROMPT.format(
                user_request=original_request,
                request_topic=request_topic,
                n_passages=len(numbered),
                passages=_format_passages_for_llm(numbered),
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("research_subdirection_llm_call_failed", exc_info=True)
        errors.append(f"focused_synth:{type(exc).__name__}: {exc}")
        report_md = _passages_only_fallback(request_topic, numbered)
    report_md = (report_md or "").strip() or _passages_only_fallback(request_topic, numbered)
    report_md, citations, cite_result = _finalize_report_md(report_md, citations, errors)
    domains = {_host(citation.url) for citation in citations if _host(citation.url)}
    return ResearchReport(
        topic=request_topic,
        summary=_extract_summary(report_md),
        report_md=report_md,
        citations=citations,
        sub_questions=[request_topic],
        coverage={
            "n_sources": len(citations),
            "n_domains": len(domains),
            "pipeline": "focused_child_v7",
            "source_discovery": source_discovery,
            "cite_check_ok": bool(cite_result.get("ok", False)),
            "n_dropped_by_reason": dict(state.dropped_by_reason),
        },
        errors=errors,
    )


async def _run_subagent_fanout(
    *,
    topic: str,
    sub_questions: list[str],
    llm_call: _LLMCall,
    search: _Searcher,
    extract: _Extractor,
    scheduler,
    parent_sid: str,
    mode: str,
    user_request: str,
    errors: list[str],
    route: dict[str, Any],
) -> ResearchReport:
    collection = await collect_subagent_research(
        topic=topic,
        sub_questions=sub_questions,
        llm_call=llm_call,
        search=search,
        extract=extract,
        scheduler=scheduler,
        parent_sid=parent_sid,
        mode=mode,
        route=route,
    )
    errors.extend(collection.errors)
    sub_reports = collection.sub_reports
    fanout_obs = collection.observation

    base_cov = {
        "n_sub_questions": len(sub_questions),
        "mode": "fanout",
        "subagent_fanout": fanout_obs,
        "route": collection.route,
    }

    if not sub_reports:
        return ResearchReport(
            topic=topic,
            summary="",
            report_md=_no_results_template(topic, sub_questions),
            citations=[],
            sub_questions=sub_questions,
            coverage={"n_sources": 0, "n_domains": 0, **base_cov},
            errors=errors,
        )

    report_md, merged_citations = await _fanout_synthesize(
        topic, user_request, sub_reports, llm_call, errors
    )
    domains = {_host(c.url) for c in merged_citations if _host(c.url)}
    return ResearchReport(
        topic=topic,
        summary=_extract_summary(report_md),
        report_md=report_md,
        citations=merged_citations,
        sub_questions=sub_questions,
        coverage={
            "n_sources": len(merged_citations),
            "n_domains": len(domains),
            **base_cov,
        },
        errors=errors,
    )


# ----------------------------------------------------------------------
# Plan / Synthesize parsers — defensive against LLM drift
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------


async def deepresearch(
    topic: str,
    *,
    llm_call: _LLMCall,
    search: _Searcher | None = None,
    extract: _Extractor | None = None,
    max_sub_questions: int = 5,
    max_urls_per_query: int = 4,
    max_total_passages: int = 12,
    min_passage_chars: int = 250,
    max_rounds: int = 1,
    mode: str = "standard",
    user_request: str | None = None,
    scheduler=None,
    parent_sid: str = "default",
    _depth: int = 0,
    skip_plan: bool = False,
) -> ResearchReport:
    """Legacy blocking adapter over the reusable DeepResearch stages.

    Report persistence, artifact publication, and delivery remain in the tool
    handler below; the extracted core only performs research computation.
    """

    config = research_core.ResearchCoreConfig.from_legacy(
        max_sub_questions=max_sub_questions,
        max_urls_per_query=max_urls_per_query,
        max_total_passages=max_total_passages,
        min_passage_chars=min_passage_chars,
        max_rounds=max_rounds,
    )
    _ur = (user_request or "").strip()
    request_topic = _ur or topic.strip()
    ports = research_core.legacy_ports(llm_call=llm_call, search=search, extract=extract)
    return await research_core.run_research_core(
        topic,
        ports=ports,
        config=config,
        mode=mode,
        user_request=request_topic,
        scheduler=scheduler,
        parent_sid=parent_sid,
        depth=_depth,
        skip_plan=skip_plan,
    )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


async def _gap_followup_queries(
    llm_call: _LLMCall,
    topic: str,
    sub_questions: list[str],
    passages: list[Passage],
    errors: list[str],
    *,
    max_followups: int = 3,
) -> list[str]:
    """Ask the LLM which evidence gaps remain → follow-up search queries.

    Best-effort: any failure (LLM error / unparseable) → ``[]`` so the
    pipeline just proceeds with round-1 evidence."""
    titles = "\n".join(f"- {p.citation.title}" for p in passages[:20]) or "(none)"
    subqs = "\n".join(f"- {q}" for q in sub_questions) or "(none)"
    try:
        raw = await llm_call(_GAP_PROMPT.format(topic=topic, titles=titles, subqs=subqs))
    except Exception as exc:  # noqa: BLE001
        log.debug("gap_followup_queries_llm_call_failed", exc_info=True)
        errors.append(f"reflect_llm: {exc}")
        return []
    qs = parse_sub_questions(raw, max_questions=max_followups)
    # Drop queries that just restate an original sub-question verbatim.
    seen = {q.strip().lower() for q in sub_questions}
    return [q for q in qs if q.strip().lower() not in seen][:max_followups]


async def _gather_safe(tasks: list, *, label: str) -> list[Any]:
    """asyncio.gather with return_exceptions=True + debug logging."""
    if not tasks:
        return []
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            log.debug("%s task raised: %s", label, r)
    return results


def _topic_keywords(text: str, *, max_keywords: int = 8) -> list[str]:
    """Cheap keyword extractor — split on whitespace + punctuation, drop
    stopwords + 1-2 char fragments. Good enough for scoring."""
    if not text:
        return []
    # Cheap CJK-aware tokenization: just keep word characters + CJK.
    raw = re.findall(r"[\w一-鿿]+", text.lower())
    stop = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "of",
        "and",
        "or",
        "to",
        "in",
        "on",
        "for",
        "by",
        "with",
        "what",
        "how",
        "why",
        "this",
        "that",
        "it",
        "its",
        "as",
        "be",
        "do",
        "does",
        "did",
        "我",
        "的",
        "了",
        "是",
        "在",
        "有",
        "和",
        "也",
        "但",
        "什么",
        "怎么",
        "哪些",
        "如何",
    }
    out: list[str] = []
    seen: set[str] = set()
    for tok in raw:
        if len(tok) < 3 and not _is_cjk(tok):
            continue
        if tok in stop or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= max_keywords:
            break
    return out


def _format_passages_for_llm(passages: list[Passage]) -> str:
    """Render passages in a stable format the LLM can cite from. 每条带
    来源层级标签,让 synth 的"官方源优先/弱证据须标注"规则可执行。"""
    chunks: list[str] = []
    for p in passages:
        c = p.citation
        tier = _tier_label(c.authority)
        chunks.append(f"({c.n}) [来源层级: {tier}] [{c.title}] {c.url}\n{p.text[:1800]}\n")
    return "\n---\n".join(chunks)


async def _update_deepresearch_index(
    report_path: Path,
    topic: str,
    report: ResearchReport,
) -> None:
    """Best-effort update of ``DeepResearch/index.md``."""
    try:
        async with _INDEX_LOCK:
            idx = report_path.parent / "index.md"
            cov = report.coverage or {}
            safe_topic = str(topic or "").replace("|", "/").replace("\r", " ").replace("\n", " ")
            # 模式列区分 flat / fanout —— 不能用 cov["mode"]（那是档位 standard/deep/light，
            # §6.0 观测占用），按 subagent_fanout 块是否存在判定（Phase 2 fanout 才有）。
            pipeline_mode = "fanout" if cov.get("subagent_fanout") else "flat"
            row = (
                f"| {time.strftime('%Y-%m-%d %H:%M')} "
                f"| {safe_topic} "
                f"| [{report_path.name}]({report_path.name}) "
                f"| {cov.get('n_sources', 0)} "
                f"| {cov.get('n_domains', 0)} "
                f"| {pipeline_mode} "
                f"| {cov.get('n_sub_questions', 0)} |\n"
            )
            if idx.exists():
                existing = idx.read_text(encoding="utf-8")
            else:
                existing = _INDEX_HEADER
            if report_path.name in existing:
                return
            updated = _insert_row_after_header(existing, row)
            tmp = idx.with_suffix(".md.tmp")
            tmp.write_text(updated, encoding="utf-8")
            os.replace(tmp, idx)
    except Exception as exc:  # noqa: BLE001
        log.debug("deepresearch index update skipped: %s", exc, exc_info=True)


# ---------------------------------------------------------------------
# Tool registry wiring
# ---------------------------------------------------------------------


async def _handle_deepresearch(
    args: dict,
    task_id: str,
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    """Async handler. Bridges the registry's ``args`` dict to the
    orchestrator. The LLM call is resolved from the global provider
    chain inside ``main.py`` — for unit tests we go directly through
    ``deepresearch`` so this code path isn't exercised.
    """
    topic = str(args.get("topic") or "").strip()
    if not topic:
        return json.dumps({"ok": False, "error": "topic is required"}, ensure_ascii=False)
    user_request = str(args.get("user_request") or "").strip() or None
    context = legacy_execution_context(args, task_id, execution_context)
    sid = context.session_id
    # Resolve an LLM callable from the running provider chain. We import
    # lazily so test environments without an LLM still pass.
    try:
        llm_call = await _resolve_default_llm_call()
    except Exception as exc:  # noqa: BLE001
        log.debug("handle_deepresearch_resolve_default_llm_call_failed", exc_info=True)
        return json.dumps(
            {"ok": False, "error": f"no LLM available: {exc}"},
            ensure_ascii=False,
        )

    # depth 档位给默认；显式 max_* 参数仍可覆盖。
    depth = str(args.get("depth") or "standard").lower()
    d_subq, d_urls, d_pass, d_rounds = _DEPTH_PRESETS.get(depth, _DEPTH_PRESETS["standard"])

    report = await deepresearch(
        topic,
        llm_call=llm_call,
        max_sub_questions=int(args.get("max_sub_questions") or d_subq),
        max_urls_per_query=int(args.get("max_urls_per_query") or d_urls),
        max_total_passages=int(args.get("max_total_passages") or d_pass),
        max_rounds=int(args.get("max_rounds") or d_rounds),
        mode=depth,
        user_request=user_request,
        scheduler=get_subagent_scheduler(),
        parent_sid=sid,
    )

    out = {"ok": True, **report.as_dict()}

    # WI-8: 报告落 DeepResearch/<slug>-<ts>.md(用户好找),并 emit
    # artifacts[] → 聊天卡片可点开。落盘失败不影响返回报告正文。
    if report.report_md and report.citations:
        try:
            save_topic = report.topic or (user_request or topic)
            saved = _save_report(save_topic, report)
            if saved:
                await _update_deepresearch_index(saved, save_topic, report)
                out["path"] = str(saved)
                out["artifacts"] = [
                    {
                        "kind": "file",
                        "path": str(saved),
                        "mime": "text/markdown",
                        "title": Path(saved).name,
                    }
                ]
        except Exception as exc:  # noqa: BLE001
            log.debug("research report save skipped: %s", exc, exc_info=True)

    return json.dumps(out, ensure_ascii=False)


def _save_report(topic: str, report: ResearchReport) -> Path | None:
    """Write the report markdown to ``DeepResearch/`` with a
    metadata header. Returns the path, or None if the dir is unavailable."""
    try:
        base = deepresearch_dir()
    except Exception:  # noqa: BLE001 - 报告目录不可用时不保存报告
        log.warning("research_report_dir_failed", exc_info=True)
        return None
    cov = report.coverage or {}
    header = (
        f"> 调研覆盖 **{cov.get('n_sources', 0)} 个来源** 来自 "
        f"**{cov.get('n_domains', 0)} 个独立域名**"
        f"（{cov.get('rounds', 1)} 轮检索，velocity={cov.get('topic_velocity', '?')}，"
        f"引用自检 {'通过' if cov.get('cite_check_ok') else '未通过'}）。\n\n"
    )
    body = header + (report.report_md or "")
    ts = int(time.time())
    try:
        slug = title_slug(topic, max_grapheme=40)
    except Exception:  # noqa: BLE001
        log.debug("save_report_title_slug_failed", exc_info=True)
        slug = "report"
    path = base / f"{slug}-{ts}.md"
    path.write_text(body, encoding="utf-8")
    return path


# An optional process-global live-LLM bridge. ``main.py`` may set this at
# boot to the *same* provider the chat agent uses (relay base_url + the
# keychain-resolved cloud key), so deep-research's plan/synthesize calls go
# through the live relay instead of a config-rebuilt provider. When unset we
# fall back to reconstructing from config below.
_LIVE_LLM_CALL: _LLMCall | None = None


def set_live_llm_call(fn: _LLMCall | None) -> None:
    """Wire the running agent's LLM into deep-research (called from main.py)."""
    global _LIVE_LLM_CALL
    _LIVE_LLM_CALL = fn


async def _resolve_default_llm_call() -> _LLMCall:
    """Resolve an LLM callable for the live tool path.

    Priority:
      1. The live bridge injected by ``main.py`` (same relay + keychain key
         the chat agent uses) — set via :func:`set_live_llm_call`.
      2. Reconstruct from ``config.llm.local`` + the OS-keychain cloud key
         (``resolve_cloud_api_key``), mirroring how ``main.py`` builds its
         ``local_llm``. The OLD code read ``cfg.llm.providers[0]`` (a stale
         multi-provider shape) with no keychain key → research silently lost
         its LLM after relay login. This is that bug fixed.
    """
    if _LIVE_LLM_CALL is not None:
        return _LIVE_LLM_CALL

    try:
        # 按需导入：可选依赖
        from config import load_config, resolve_cloud_api_key  # type: ignore
        from providers.openai_compatible import (  # type: ignore
            OpenAICompatibleProvider,
        )
    except ImportError as exc:
        raise RuntimeError(f"provider modules unavailable: {exc}") from exc

    cfg = load_config()
    local = getattr(cfg.llm, "local", None)
    if local is None or not getattr(local, "base_url", ""):
        raise RuntimeError("no llm provider configured")
    api_key = resolve_cloud_api_key() or getattr(local, "api_key", "") or ""
    provider = OpenAICompatibleProvider(
        base_url=local.base_url,
        api_key=api_key,
        model=getattr(local, "model", ""),
    )

    async def _call(prompt: str) -> str:

        with provider_purpose_scope("research"):
            result = await provider.chat_with_tools(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=4096,  # synthesis needs room (was 2048 → truncated long reports)
            )
        return (result or {}).get("content") or ""

    return _call


def register_static_tools(registry) -> None:
    registry.register(
        "deepresearch",
        "web",
        _RESEARCH_SCHEMA,
        _handle_deepresearch,
        context_handler=bind_context_handler(_handle_deepresearch),
        permission_category="read_file",
        # deep 档要跑 多引擎降级搜索 + 二级抓取 + 反思补证轮 + LLM 精排,
        # 慢网区(代理/必应跳转 cn.bing)单轮就逼近 180s。提到 300s(对齐
        # code/os 重工具),给 deep 档完整跑完的余量,避免半途 tool_timeout
        # 丢掉已抓到的一手源(真机 UI 测 TC-P2-03 deep 档 180s 超时实证)。
        timeout_seconds=_DEEPRESEARCH_TOOL_TIMEOUT,
    )


research_run = deepresearch  # deprecated alias: use deepresearch
