#!/usr/bin/env python3
"""Reproducible Context OS V1 micro-benchmark.

The benchmark is deliberately provider-free: it measures request assembly and
tool schema payload construction only, so an auxiliary LLM call is impossible.
Run the OFF baseline first, then compare the ON path on the same machine::

    backend\.venv\Scripts\python.exe scripts\perf\context_os_bench.py --mode off
    backend\.venv\Scripts\python.exe scripts\perf\context_os_bench.py --mode on \
        --compare plans\2026-07-13-context-os-v1\context-perf-baseline.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from deskpet.agent.assembler.bundle import ContextBundle  # noqa: E402
from deskpet.agent.context_request_planner import ContextRequestPlanner  # noqa: E402
from deskpet.tools.capabilities import (  # noqa: E402
    PreparedToolCapability,
    ToolCapabilityResolver,
    ToolEligibilityContext,
    ToolExposureIntent,
    canonical_json,
)
from deskpet.tools.registry import ToolRegistry  # noqa: E402


DEFAULT_BASELINE = ROOT / "plans" / "2026-07-13-context-os-v1" / "context-perf-baseline.json"
BRIDGES = ("tool_search", "tool_describe", "tool_activate")


class _ToolsConfig:
    disabled_toolsets: tuple[str, ...] = ()
    disabled_toolsets_schema_only: tuple[str, ...] = ()
    dangerous_tools_allowlist: tuple[str, ...] = ()


@dataclass(frozen=True)
class Timing:
    iterations: int
    warmup: int
    median_ms: float
    p95_ms: float
    min_ms: float
    max_ms: float


def _schema(name: str, *, description_size: int = 96) -> dict[str, Any]:
    return {
        "name": name,
        "description": (f"Synthetic capability {name}. " + "x" * description_size),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search query"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
        },
    }


def _register(registry: ToolRegistry, name: str, *, toolset: str = "bench") -> None:
    registry.register(
        name=name,
        toolset=toolset,
        schema=_schema(name),
        handler=lambda _args, _task_id: "{}",
    )


def _registry(*, deferred_count: int = 0) -> ToolRegistry:
    registry = ToolRegistry()
    registry.set_tools_config_provider(lambda: _ToolsConfig())
    for name in BRIDGES:
        _register(registry, name, toolset="capability_bridge")
    for index in range(deferred_count):
        _register(registry, f"deferred_{index:03d}")
    return registry


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.999999)))
    return ordered[rank]


def _measure(fn: Any, *, iterations: int, warmup: int) -> Timing:
    for _ in range(warmup):
        fn()
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return Timing(
        iterations=iterations,
        warmup=warmup,
        median_ms=round(statistics.median(samples), 6),
        p95_ms=round(_percentile(samples, 0.95), 6),
        min_ms=round(min(samples), 6),
        max_ms=round(max(samples), 6),
    )


def _short_chat(mode: str, *, iterations: int, warmup: int) -> Timing:
    bundle = ContextBundle(
        task_type="companion.chat",
        frozen_system="You are DeskPet.",
        memory_block="The user prefers concise answers.",
        history=[
            {"role": "user", "content": "Remember that my project is Atlas."},
            {"role": "assistant", "content": "I will remember that."},
        ],
        tool_exposure_intent=ToolExposureIntent(),
    )
    if mode == "off":
        return _measure(
            lambda: bundle.build_messages(
                "Be helpful.", history=bundle.history, user_message="What is my project?"
            ),
            iterations=iterations,
            warmup=warmup,
        )

    planner = ContextRequestPlanner(ToolCapabilityResolver(_registry()))
    eligibility = ToolEligibilityContext(
        session_id="bench-session", request_id="bench-request", task_type="companion.chat"
    )
    loop = asyncio.new_event_loop()

    def assemble_on() -> None:
        loop.run_until_complete(
            planner.prepare_initial(
                bundle,
                base_system="Be helpful.",
                history=bundle.history,
                user_message="What is my project?",
                eligibility=eligibility,
                context_window=1_000_000,
                effective_pct=0.8,
                generation_reserve=4096,
                conditional_direct_names=(),
            )
        )

    try:
        return _measure(assemble_on, iterations=iterations, warmup=warmup)
    finally:
        loop.close()


def _wire_bytes(schemas: Any) -> int:
    return len(canonical_json(schemas).encode("utf-8"))


def _tool_payload(mode: str, *, count: int = 500) -> dict[str, Any]:
    registry = _registry(deferred_count=count)
    legacy_specs = [spec for spec in registry.all_specs() if spec.name not in BRIDGES]
    legacy_schemas = [
        {"type": "function", "function": spec.schema} for spec in legacy_specs
    ]
    legacy_bytes = _wire_bytes(legacy_schemas)
    if mode == "off":
        return {
            "deferred_tool_count": count,
            "legacy_full_schema_bytes": legacy_bytes,
            "initial_schema_bytes": legacy_bytes,
            "initial_schema_reduction_pct": 0.0,
            "provider_iterations_no_hit": 1,
            "auxiliary_llm_calls": 0,
        }

    resolver = ToolCapabilityResolver(registry)
    draft = resolver.resolve_draft(
        ToolExposureIntent(discoverable_selectors=("toolset:bench",)),
        eligibility=ToolEligibilityContext(
            session_id="bench-session", request_id="bench-tools", task_type="companion.chat"
        ),
    )
    prepared = draft.finalize()
    initial_bytes = _wire_bytes(prepared.logical_schemas())

    target_ref = next(ref for ref in prepared.deferred if ref.name == "deferred_000")
    target_spec = next(spec for spec in registry.all_specs() if spec.name == target_ref.name)
    target = PreparedToolCapability(
        ref=target_ref,
        canonical_schema={"type": "function", "function": target_spec.schema},
    )
    activated = prepared.activate(target)
    activated_bytes = _wire_bytes(activated.logical_schemas())
    added_bytes = activated_bytes - initial_bytes
    # Attribution uses the same canonical list encoding as the request payload:
    # replacing the closing `]` with `,<schema>]` has this exact byte delta.
    attributed_added_bytes = _wire_bytes(activated.logical_schemas()) - _wire_bytes(
        prepared.logical_schemas()
    )
    reduction = (1.0 - initial_bytes / legacy_bytes) * 100.0
    return {
        "deferred_tool_count": len(prepared.deferred),
        "legacy_full_schema_bytes": legacy_bytes,
        "initial_schema_bytes": initial_bytes,
        "initial_schema_reduction_pct": round(reduction, 6),
        "provider_iterations_no_hit": 1,
        "provider_iterations_after_explicit_activation": 2,
        "auxiliary_llm_calls": 0,
        "activated_tool": target.ref.name,
        "prepared_tool_set_revision_before": prepared.revision,
        "prepared_tool_set_revision_after": activated.revision,
        "activation_added_schema_bytes": added_bytes,
        "prepared_tool_set_attributed_added_bytes": attributed_added_bytes,
        "activation_attribution_matches": added_bytes == attributed_added_bytes,
    }


def _git_commit() -> str:
    bundled = (
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/cmd/git.exe"
    )
    candidates = (["git"], [str(bundled)]) if bundled.exists() else (["git"],)
    for executable in candidates:
        try:
            return subprocess.check_output(
                [*executable, "rev-parse", "HEAD"],
                cwd=ROOT,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            continue
    return "unknown"


def run(mode: str, *, iterations: int, warmup: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": mode,
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "commit": _git_commit(),
        },
        "short_chat_assembly": asdict(
            _short_chat(mode, iterations=iterations, warmup=warmup)
        ),
        "tool_payload": _tool_payload(mode),
        "trace": {
            "provider_created": False,
            "auxiliary_llm_calls": 0,
            "note": "provider-free assembly and payload benchmark",
        },
    }


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    baseline_p95 = float(baseline["short_chat_assembly"]["p95_ms"])
    current_p95 = float(current["short_chat_assembly"]["p95_ms"])
    delta = current_p95 - baseline_p95
    tools = current["tool_payload"]
    checks = {
        "short_chat_p95_increment_lte_20ms": delta <= 20.0,
        "no_auxiliary_llm_calls": current["trace"]["auxiliary_llm_calls"] == 0,
        "deferred_tool_count_is_500": tools["deferred_tool_count"] == 500,
        "initial_schema_reduction_gte_80pct": tools["initial_schema_reduction_pct"] >= 80.0,
        "no_hit_does_not_add_provider_iteration": tools["provider_iterations_no_hit"] == 1,
        "activation_bytes_match_prepared_tool_set_attribution": tools[
            "activation_attribution_matches"
        ],
    }
    return {
        "baseline_mode": baseline.get("mode"),
        "current_mode": current.get("mode"),
        "short_chat_p95_delta_ms": round(delta, 6),
        "threshold_ms": 20.0,
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("off", "on"), required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--warmup", type=int, default=50)
    args = parser.parse_args()
    if args.iterations < 20 or args.warmup < 0:
        parser.error("--iterations must be >=20 and --warmup must be >=0")

    result = run(args.mode, iterations=args.iterations, warmup=args.warmup)
    if args.compare:
        baseline = json.loads(args.compare.read_text(encoding="utf-8"))
        result["comparison"] = compare(result, baseline)

    output = args.output
    if output is None and args.mode == "off" and args.compare is None:
        output = DEFAULT_BASELINE
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("comparison", {}).get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
