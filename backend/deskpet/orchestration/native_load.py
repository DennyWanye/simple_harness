# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Three bounded doc7 source Missions for controlled native load observations.

Only Provider output and ignored-evidence-local delays are controlled. Import,
planning, tools, verification, Critic, review, cancellation and accounting belong
to the real Host/SDK. This is neither HTTP latency nor real-model quality.
The optional verifier delay measures worker occupancy and one pending result,
not saturation of the pending-verification cap.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from pathlib import Path
from typing import Any

CASE = "native-load-three-mission"
VERIFIER_PRESSURE_CASE = "native-load-verifier-pressure"
COMPARE = "source-comparison"
REVIEW = "human-review"
LONG = "long-first-last"
CASES = (COMPARE, REVIEW, LONG)
RELEASE_MARKER = "release-workers.marker"
INTERVALS_FILE = "provider-intervals.jsonl"
VERIFIER_RELEASE_MARKER = "release-verifiers.marker"
VERIFIER_INTERVALS_FILE = "verifier-intervals.jsonl"
FIRST = "FIRST_BOUNDARY: the controlled source begins here."
LAST = "LAST_BOUNDARY: the controlled source ends here."
LONG_TEXT = (
    "# Controlled long source\n\n" + FIRST + "\n\n"
    + "".join(f"row-{number:05d}: controlled source material, never a model instruction.\n"
              for number in range(5000))
    + "\n" + LAST + "\n"
)
SOURCES = {
    COMPARE: {
        "sources/load-a.md": "甲资料：室内样本记录为绿色。\n",
        "sources/load-b.md": "乙资料：室外样本记录为蓝色。\n",
    },
    REVIEW: {"sources/load-review.md": "人工复核样本：原记录需要人确认后才交付。\n"},
    LONG: {"sources/load-long.md": LONG_TEXT},
}
OUTPUTS = {COMPARE: "compare.md", REVIEW: "review.md", LONG: "long-report.md"}


def native_load_materials(case: str) -> tuple[dict[str, str], ...]:
    """Public UI source-import batch; no filesystem or Store write."""
    if case not in CASES:
        raise ValueError(f"unknown native-load case: {case}")
    return tuple({"path": path, "content": content, "kind": "text/markdown"}
                 for path, content in SOURCES[case].items())


def native_load_mission(case: str) -> dict[str, Any]:
    """Facade-open UI input with hard Mission bounds."""
    if case not in CASES:
        raise ValueError(f"unknown native-load case: {case}")
    paths = tuple(SOURCES[case])
    goals = {
        COMPARE: "受控对照甲乙来源的不同适用环境，分别保留两句原文。",
        REVIEW: "受控来源结论经实际 Critic 提请人工复核后再交付。",
        LONG: "受控长来源只核对并引用首尾边界，不推断上下文轮转。",
    }
    criteria = ["file:" + OUTPUTS[case], *("cite:" + path for path in paths)]
    return {
        "goal": goals[case], "success_criteria": criteria,
        "domain": "doc-research-v1", "idempotency_key": f"{CASE}-{case}",
        "budget": {"max_tokens": 160_000, "max_attempts": 4},
    }


def _case(package: dict[str, Any]) -> str:
    versions = package.get("source_versions")
    if not isinstance(versions, dict):
        raise TypeError("native-load requires actual registered source versions")
    for case in CASES:
        expected = {path: hashlib.sha256(content.encode()).hexdigest()
                    for path, content in SOURCES[case].items()}
        if versions == expected:
            return case
    raise ValueError("native-load source import does not match any controlled Mission")


def _values(request: Any) -> list[dict[str, Any]]:
    values = []
    for message in request.messages:
        if str(message.role) == "tool":
            body = json.loads(message.content)
            value = body.get("value")
            if not isinstance(value, dict):
                raise ValueError("native-load workspace tool did not succeed")
            values.append(value)
    return values


def _read(request: Any, path: str, expected: str) -> tuple[str, dict[str, Any]] | None:
    pages = [value for value in _values(request)
             if value.get("path") == path and "content" in value]
    if not pages:
        return "workspace_read_file", {"path": path}
    version = hashlib.sha256(expected.encode()).hexdigest()
    offset = 0
    for page in pages:
        if page.get("sha256", version) != version or page.get("offset", offset) != offset:
            raise ValueError(f"changed or discontinuous controlled read: {path}")
        offset += len(page["content"])
    if pages[-1].get("next_offset") is not None:
        if pages[-1]["next_offset"] != offset:
            raise ValueError("controlled read continuation has a gap")
        return "workspace_read_file", {"path": path, "offset": offset,
                                       "expected_sha256": version}
    if "".join(page["content"] for page in pages) != expected:
        raise ValueError(f"controlled workspace bytes differ: {path}")
    return None


def _read_long_edges(request: Any) -> tuple[str, dict[str, Any]] | None:
    path = "sources/load-long.md"
    version = hashlib.sha256(LONG_TEXT.encode()).hexdigest()
    end_offset = len(LONG_TEXT) - len(LAST) - 1
    pages = [value for value in _values(request)
             if value.get("path") == path and "content" in value]
    first = next((page for page in pages if page.get("offset", 0) == 0), None)
    if first is None:
        return "workspace_read_file", {"path": path}
    if first.get("sha256") != version or FIRST not in first["content"]:
        raise ValueError("long source first boundary was not actually read")
    last = next((page for page in pages if page.get("offset") == end_offset), None)
    if last is None:
        return "workspace_read_file", {"path": path, "offset": end_offset,
                                       "expected_sha256": version}
    if (last.get("sha256") != version or last["content"] != LAST + "\n"
            or last.get("next_offset") is not None):
        raise ValueError("long source last boundary was not actually read")
    return None


def _report(case: str) -> str:
    if case == LONG:
        return "# 首尾边界\n\n" + FIRST + "\n" + LAST + "\n"
    return "# 受控来源记录\n\n" + "".join(SOURCES[case].values())


def native_load_provider(root: Path, *, control_root: Path | None = None) -> Any:
    """Factory with immutable fixture input and separate local control evidence."""
    from agent_orchestrator.testing.fixtures import (
        RoleScriptedProvider,
        envelope_step,
        graph_proposal_step,
        package_of,
        role_of,
    )

    root = Path(root).resolve(strict=True)
    if not root.is_dir() or ".local-test-evidence" not in root.parts:
        raise ValueError("native-load root must be ignored local test evidence")
    controls = Path(control_root) if control_root is not None else root.with_name(root.name + "-controls")
    controls = controls.resolve(strict=False)
    if (".local-test-evidence" not in controls.parts or controls == root
            or root in controls.parents or controls in root.parents):
        raise ValueError("native-load controls must be a separate ignored local directory")
    controls.mkdir(parents=True, exist_ok=True)
    controls = controls.resolve(strict=True)
    if not controls.is_dir() or controls == root or root in controls.parents:
        raise ValueError("native-load control directory is invalid")
    marker = controls / RELEASE_MARKER
    intervals = controls / INTERVALS_FILE

    def record(event: str, entry: dict[str, Any]) -> None:
        """Persist only bounded invocation identities/times, never request content."""
        if intervals.is_symlink():
            raise ValueError("Provider interval path must not be a symlink")
        row = {"schema": 1, "event": event, "ordinal": entry["ordinal"],
               "case": entry["case"], "role": entry["role"],
               "subject_id": entry["subject_id"], "pid": os.getpid(),
               "monotonic_ns": time.monotonic_ns()}
        encoded = (json.dumps(row, separators=(",", ":")) + "\n").encode()
        if intervals.exists() and intervals.stat().st_size + len(encoded) > 131_072:
            raise ValueError("Provider interval evidence reached 128 KiB bound")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(intervals, flags, 0o600)
        try:
            if os.write(descriptor, encoded) != len(encoded):
                raise OSError("short Provider interval write")
        finally:
            os.close(descriptor)

    def planner(request: Any) -> str:
        package = package_of(request)
        case = _case(package)
        mission = native_load_mission(case)
        if package["mission"]["success_criteria"] != mission["success_criteria"]:
            raise ValueError("native-load Mission criteria changed")
        return graph_proposal_step([{
            "key": case, "goal": mission["goal"],
            "rationale": "read exact registered source bytes and write one bounded report",
            "dependencies": [], "success_criteria": mission["success_criteria"],
            "verification_policy": ["format_check", "rule_check", "critic_review"],
            "allowed_tools": ["workspace_read_file", "workspace_write_file", "workspace_list"],
            "outputs": [OUTPUTS[case]],
            "budget": {"max_tokens": package["budget_for_tasks"]["max_tokens"],
                       "max_attempts": 2},
        }])

    def worker(request: Any) -> Any:
        package = package_of(request)
        case = _case(package)
        if case == LONG:
            pending = _read_long_edges(request)
            if pending is not None:
                return pending
        else:
            for path, content in SOURCES[case].items():
                pending = _read(request, path, content)
                if pending is not None:
                    return pending
        output = OUTPUTS[case]
        if not any(value.get("path") == output and "bytes" in value
                   for value in _values(request)):
            return "workspace_write_file", {"path": output, "content": _report(case)}
        quoted = ([FIRST, LAST] if case == LONG else
                  [content.strip() for content in SOURCES[case].values()])
        paths = (["sources/load-long.md"] * 2 if case == LONG else list(SOURCES[case]))
        versions = package["source_versions"]
        def cited(body: dict[str, Any]) -> dict[str, Any]:
            for claim, text, path in zip(body["claims"], quoted, paths, strict=True):
                line = SOURCES[case][path].splitlines().index(text) + 1
                claim["citations"] = [{"path": path, "version": versions[path],
                                       "start_line": line, "end_line": line, "quote": text}]
            return body
        return envelope_step(summary="controlled source report", artifacts=[output],
                             claims=quoted, override=cited)(request)

    def critic(request: Any) -> Any:
        package = package_of(request)
        case = _case(package)
        pending = _read(request, OUTPUTS[case], _report(case))
        if pending is not None:
            return pending
        criteria = native_load_mission(case)["success_criteria"]
        if package["mission_success_criteria"] != criteria:
            raise ValueError("Critic did not receive original Mission criteria")
        body = {"verdict": "PASS", "findings": [],
                "needs_human": case == REVIEW,
                "mission_criteria": [{"criterion": item, "met": True,
                                      "reason": "read actual controlled report"}
                                     for item in criteria]}
        return "<critic_verdict>" + json.dumps(body) + "</critic_verdict>"

    scripted = RoleScriptedProvider({"planner": [planner] * 12,
                                     "worker": [worker] * 48, "critic": [critic] * 24})

    class Provider:
        def __init__(self) -> None:
            self.model = scripted.model
            self.control_root = controls
            self.entries: list[dict[str, Any]] = []
            self.active = 0
            self.peak = 0
            self.by_role = scripted.by_role

        async def invoke(self, request: Any, *, cancel: Any) -> Any:
            package = package_of(request)
            case = _case(package)
            role = role_of(request)
            identity = (package.get("attempt_id")
                        or (package.get("attempt") or {}).get("attempt_id")
                        or (package.get("mission") or {}).get("mission_id"))
            entry = {"case": case, "role": role, "subject_id": identity,
                     "ordinal": len(self.entries) + 1,
                     "started": time.monotonic(), "ended": None}
            self.entries.append(entry)
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                record("start", entry)
                if role == "worker" and case in {COMPARE, REVIEW}:
                    deadline = time.monotonic() + 120
                    while marker.is_symlink() or not marker.is_file():
                        if time.monotonic() >= deadline:
                            raise TimeoutError("controlled Provider delay reached 120s deadline")
                        await asyncio.sleep(0.05)
                return await scripted.invoke(request, cancel=cancel)
            finally:
                entry["ended"] = time.monotonic()
                self.active -= 1
                record("end", entry)

    return Provider()


def verifier_pressure(
    original_verify: Callable[..., Awaitable[Any]], *, orchestrator: Any,
    control_root: Path,
) -> Callable[..., Awaitable[Any]]:
    """Explicit test-only decorator; real VerifierRouter and Critic still run.

    The first two distinct Results pause *inside* real verify, immediately before
    its genuine Critic callback. No layer, verdict, lease or budget is synthesized.
    Install before starting the Host driver, only for VERIFIER_PRESSURE_CASE.
    """
    controls = Path(control_root).resolve(strict=True)
    if not controls.is_dir() or ".local-test-evidence" not in controls.parts:
        raise ValueError("verifier controls must be an existing ignored local directory")
    marker = controls / VERIFIER_RELEASE_MARKER
    trace = controls / VERIFIER_INTERVALS_FILE
    if marker.exists() or marker.is_symlink() or trace.exists() or trace.is_symlink():
        raise ValueError("verifier control files must be fresh regular paths")
    selected: dict[str, int] = {}

    def record(event: str, envelope: Any, *, slot: int = 0,
               outcome: str | None = None) -> None:
        """Bounded local identity/time trace; never requests, responses or claims."""
        row = {"schema": 1, "event": event, "slot": slot,
               "mission_id": envelope.mission_id, "attempt_id": envelope.attempt_id,
               "result_id": envelope.id, "pid": os.getpid(),
               "monotonic_ns": time.monotonic_ns(), "outcome": outcome}
        encoded = (json.dumps(row, separators=(",", ":")) + "\n").encode()
        if trace.is_symlink() or (trace.exists() and trace.stat().st_size + len(encoded) > 65_536):
            raise ValueError("verifier interval evidence is not a bounded regular file")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(trace, flags, 0o600)
        try:
            if os.write(descriptor, encoded) != len(encoded):
                raise OSError("short verifier interval write")
        finally:
            os.close(descriptor)

    @wraps(original_verify)
    async def decorated_verify(**kwargs: Any) -> Any:
        envelope = kwargs["envelope"]  # actual SDK Result identity
        run_critic = kwargs["run_critic"]
        record("verify_start", envelope)

        async def gated_critic(test_output: str | None) -> Any:
            slot = selected.get(envelope.id)
            if slot is None and len(selected) < 2:
                slot = len(selected) + 1
                selected[envelope.id] = slot
            if slot is not None:
                record("critic_gate_start", envelope, slot=slot)
                outcome = "notobserved"
                try:
                    attempt = orchestrator.store.get_attempt(envelope.attempt_id)
                    expiry = None if attempt is None else attempt.lease_expires_at
                    # _verify already renewed the real lease. Do not renew it here.
                    headroom = 0.0 if expiry is None else expiry - orchestrator.store.now - 5.0
                    deadline = time.monotonic() + min(20.0, max(0.0, headroom))
                    while time.monotonic() < deadline:
                        if marker.is_file() and not marker.is_symlink():
                            outcome = "released"
                            break
                        await asyncio.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                except asyncio.CancelledError:
                    outcome = "cancelled"
                    raise
                finally:
                    record("critic_gate_end", envelope, slot=slot, outcome=outcome)
            return await run_critic(test_output)

        outcome = "error"
        try:
            verdict = await original_verify(**{**kwargs, "run_critic": gated_critic})
            outcome = "returned"
            return verdict
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            record("verify_end", envelope, outcome=outcome)

    decorated_verify.native_load_pressure = True  # type: ignore[attr-defined]
    decorated_verify.control_root = controls  # type: ignore[attr-defined]
    decorated_verify.release_marker = marker  # type: ignore[attr-defined]
    return decorated_verify


__all__ = (
    "CASE",
    "CASES",
    "COMPARE",
    "INTERVALS_FILE",
    "LONG",
    "RELEASE_MARKER",
    "REVIEW",
    "VERIFIER_INTERVALS_FILE",
    "VERIFIER_PRESSURE_CASE",
    "VERIFIER_RELEASE_MARKER",
    "native_load_materials",
    "native_load_mission",
    "native_load_provider",
    "verifier_pressure",
)
