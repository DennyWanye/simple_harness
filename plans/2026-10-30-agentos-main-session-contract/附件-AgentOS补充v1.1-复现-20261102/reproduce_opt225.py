"""Reproduce opt.225 gaps offline; exit 0 means baseline gaps were reproduced."""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import importlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path

WHEEL_SHA = "1ec937c84cc009729315fd8e216ef4ae122b12a8b604cdf58969be1fd098c32d"


def inspect_continuation_gate(extracted: Path) -> dict[str, object]:
    """Record a static finding, not a real-model orchestration test."""
    source = extracted / "agent_orchestrator/host/session_chat.py"
    tree = ast.parse(source.read_text())
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_registered_tools"
    )
    query_set = next(
        node.value
        for node in ast.walk(method)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "queries" for target in node.targets)
    )
    assert isinstance(query_set, ast.Set)
    literals = {
        node.value
        for node in query_set.elts
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    blocked = {"delegate_task", "create_child", "child_send"}
    assert not blocked & literals, "Baseline continuation allowlist changed"
    return {
        "verification_kind": "static_source_only",
        "source": "src/agent_orchestrator/host/session_chat.py",
        "method_line": method.lineno,
        "controls_absent_from_query_allowlist": sorted(blocked),
        "real_model_or_authorized_continuation_executed": False,
    }


async def reproduce_runtime_gaps(root: Path) -> dict[str, object]:
    """Use SDK test fixtures for setup; never modify SDK persistence tables."""
    sdk = importlib.import_module("agent_orchestrator.host")
    continuation_fixture = importlib.import_module("test_continuation")
    goals_fixture = importlib.import_module("test_goals")

    class Policy:
        def __init__(self) -> None:
            self.allowed = False
            self.calls = 0

        async def decide(self, context: object) -> object:
            self.calls += 1
            if self.allowed:
                return sdk.ContinuationDecision.enqueue()
            return sdk.ContinuationDecision.hold("temporary")

    policy = Policy()
    _, parent = await continuation_fixture.parent_with_result(root / "hold", policy=policy)
    try:
        await continuation_fixture.tick(parent)
        before = parent.continuations()[0]["state"]
        policy.allowed = True
        for _ in range(3):
            await continuation_fixture.tick(parent)
        hold_result = {
            "before": before,
            "after_three_ticks": parent.continuations()[0]["state"],
            "policy_calls": policy.calls,
            "queued_inputs": len(parent.queued_inputs()),
            "model_calls": parent._chat.calls,
        }
        assert hold_result == {
            "before": "hold",
            "after_three_ticks": "hold",
            "policy_calls": 1,
            "queued_inputs": 0,
            "model_calls": 0,
        }, hold_result
    finally:
        await parent.close()

    _, session = goals_fixture.make(root / "goal")
    try:
        goals_fixture.put(session)
        receipt = session.finish_goal(
            "g", "succeeded", command_id="finish", expected_version=1, basis="已核验"
        )
        goal_result = {"state": receipt["state"], "tasks": len(session.tasks())}
        assert goal_result == {"state": "succeeded", "tasks": 0}, goal_result
    finally:
        await session.close()
    return {"G2_hold_then_allow": hold_result, "G3_success_without_evidence": goal_result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sdk-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    wheel_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if wheel_sha != WHEEL_SHA:
        parser.error("Wheel hash differs from the reviewed opt.225 artifact")
    fixtures = args.sdk_source.resolve() / "tests/orchestrator/host"
    for name in ("test_continuation.py", "test_goals.py", "test_session.py"):
        if not (fixtures / name).is_file():
            parser.error(f"Missing SDK fixture: {name}")

    with tempfile.TemporaryDirectory(prefix="sdk225-gap-repro-") as directory:
        root = Path(directory)
        extracted = root / "wheel"
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(extracted)
        sys.path[:0] = [str(extracted), str(fixtures)]
        result = {
            "sdk_version": "0.13.0.dev20260925+opt.225",
            "wheel_sha256": wheel_sha,
            "result_meaning": "Baseline gaps reproduced, NOT repaired SDK acceptance",
            "environment": "Temporary stores; SDK FakeHost/FakeChat; no real model or network",
            "fixture_sha256": {
                name: hashlib.sha256((fixtures / name).read_bytes()).hexdigest()
                for name in (
                    "test_continuation.py",
                    "test_goals.py",
                    "test_session.py",
                    "test_session_async.py",
                )
            },
            "G1_static_gate": inspect_continuation_gate(extracted),
            **asyncio.run(reproduce_runtime_gaps(root / "stores")),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
