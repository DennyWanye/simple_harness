#!/usr/bin/env python3
"""Run the versioned SearchGateway quality set against real providers.

This is intentionally a production-wiring benchmark, not a parser test. It
loads ``config.toml``, constructs the same process gateway as the desktop
backend, performs real requests, and emits a machine-readable JSON report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * 0.95 + 0.999) - 1))]


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _domain_matches(host: str, expected: str) -> bool:
    expected = expected.lower().removeprefix("www.")
    return host == expected or host.endswith("." + expected)


async def _run(args: argparse.Namespace) -> dict:
    from config import load_config
    from deskpet.retrieval.contracts import SearchRequest
    from deskpet.retrieval.runtime import build_search_gateway

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    config = load_config(args.config)
    gateway = build_search_gateway(config)
    cases: list[dict] = []
    try:
        for item in dataset["queries"]:
            response = await gateway.search(
                SearchRequest(
                    query=item["query"],
                    max_results=args.max_results,
                    mode="quick",
                    hydrate_top=args.hydrate_top,
                    total_timeout_s=args.timeout,
                )
            )
            top = list(response.results[:5])
            hosts = sorted({_host(candidate.canonical_url or candidate.url) for candidate in top})
            expected_domains = item["expected_domains"]
            domain_hits = {
                expected: any(_domain_matches(host, expected) for host in hosts)
                for expected in expected_domains
            }
            corpus = "\n".join(
                f"{candidate.title}\n{candidate.snippet}\n{candidate.text or ''}" for candidate in top
            ).casefold()
            keyword_hits = {
                keyword: keyword.casefold() in corpus for keyword in item["expected_keywords"]
            }
            cases.append(
                {
                    "id": item["id"],
                    "language": item["language"],
                    "category": item["category"],
                    "success": response.count > 0,
                    "empty": response.count == 0,
                    "count": response.count,
                    "elapsed_ms": response.elapsed_ms,
                    "cache_hit": response.cache_hit,
                    "degraded": response.degraded,
                    "engines_tried": response.engines_tried,
                    "engines_hit": response.engines_hit,
                    "errors": response.errors,
                    "top5_domains": hosts,
                    "expected_domain_hits": domain_hits,
                    "expected_keyword_hits": keyword_hits,
                    "recall_at_5": sum(domain_hits.values()) / len(domain_hits),
                }
            )
    finally:
        await gateway.shutdown()

    elapsed = [case["elapsed_ms"] for case in cases]
    return {
        "schema_version": 1,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": dataset["dataset_version"],
        "network_results_are_date_sensitive": True,
        "config": {
            "max_results": args.max_results,
            "hydrate_top": args.hydrate_top,
            "timeout_s": args.timeout,
        },
        "summary": {
            "cases": len(cases),
            "success_rate": sum(case["success"] for case in cases) / len(cases),
            "empty_rate": sum(case["empty"] for case in cases) / len(cases),
            "mean_recall_at_5": statistics.fmean(case["recall_at_5"] for case in cases),
            "p95_ms": _p95(elapsed),
        },
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=BACKEND / "tests" / "fixtures" / "search_gateway_queries.json",
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config.toml")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--hydrate-top", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    report = asyncio.run(_run(args))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
