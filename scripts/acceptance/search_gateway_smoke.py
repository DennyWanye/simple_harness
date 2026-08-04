#!/usr/bin/env python3
"""Real-network production-wiring smoke for SearchGateway and fetch/extract."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


async def _run(args: argparse.Namespace) -> tuple[dict, bool]:
    from config import load_config
    from deskpet.retrieval.contracts import FetchRequest, SearchRequest
    from deskpet.retrieval.runtime import build_search_gateway

    gateway = build_search_gateway(load_config(args.config))
    searches: list[dict] = []
    fetch: dict = {}
    ok = True
    try:
        for language, query in (("zh-CN", args.zh_query), ("en", args.en_query)):
            response = await gateway.search(
                SearchRequest(query=query, max_results=8, mode="quick", total_timeout_s=args.timeout)
            )
            searches.append(
                {
                    "language": language,
                    "query": query,
                    "count": response.count,
                    "engines_tried": response.engines_tried,
                    "engines_hit": response.engines_hit,
                    "errors": response.errors,
                    "elapsed_ms": response.elapsed_ms,
                    "degraded": response.degraded,
                    "top_results": [
                        {
                            "title": candidate.title,
                            "url": candidate.url,
                            "provider": candidate.provider,
                            "providers": list(candidate.providers),
                        }
                        for candidate in response.results[:3]
                    ],
                }
            )
            ok = ok and response.count > 0 and bool(response.engines_tried) and bool(response.engines_hit)

        service = gateway.fetch_service
        if service is None:
            fetch = {"ok": False, "error": "FetchExtractService was not wired"}
            ok = False
        else:
            document = await service.fetch(
                FetchRequest(
                    url=args.fetch_url,
                    timeout=args.fetch_timeout,
                    render_policy="auto",
                    max_chars=20_000,
                )
            )
            fetch = {
                "ok": len(document.text.strip()) >= args.min_fetch_chars,
                "url": args.fetch_url,
                "canonical_url": document.canonical_url,
                "title": document.title,
                "text_chars": len(document.text),
                "content_hash": document.content_hash,
                "fetcher": document.fetcher,
                "extractor": document.extractor,
                "quality_flags": list(document.quality_flags),
            }
            ok = ok and fetch["ok"]
    except Exception as exc:  # noqa: BLE001 - smoke must serialize a failure
        fetch = {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:240]}
        ok = False
    finally:
        await gateway.shutdown()

    return (
        {
            "schema_version": 1,
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "production_wiring": True,
            "mocked": False,
            "searches": searches,
            "fetch": fetch,
            "decision": "PASS" if ok else "FAIL",
        },
        ok,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config.toml")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--zh-query", default="生成式人工智能服务管理暂行办法 官方")
    parser.add_argument("--en-query", default="Rust async book official documentation")
    parser.add_argument("--fetch-url", default="https://developer.mozilla.org/en-US/docs/Web/API/WebGPU_API")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--fetch-timeout", type=float, default=15.0)
    parser.add_argument("--min-fetch-chars", type=int, default=200)
    args = parser.parse_args()
    report, ok = asyncio.run(_run(args))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
