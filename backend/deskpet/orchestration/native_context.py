# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""A07 controlled native business fixture; no Context/Store instrumentation.

Hook proposal: in the explicit document-ui case ``native-context-rotation``,
construct ``native_context_provider(fixture_root, control_root=library_root /
'native-context-controls')``. Keep source_runtime_options and all formal runtime
callbacks unchanged. Import the one exported material through the actual UI.
Requires a fresh editable-source doc9 pool (default 32K Context, 24 model calls).

17 physical fixture calls: Planner 1, Worker 12 reads + write + envelope, Critic
read + verdict. This proves neither real-model recall nor SIGKILL recovery. The
UNRESOLVED marker is deliberately a controlled input requirement, not an SDK
Claim status. Only the parent may claim rotation after reading SDK selections.
An existing observation file permits completed cold reopen but refuses new calls.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

CASE = "native-context-rotation"
SOURCE_PATH = "sources/native-context.md"
REPORT_PATH = "REPORT.md"
PAGE_CHARS = 8100
READS = 12
EXPECTED_CALLS = 17
OBSERVATIONS_FILE = "provider-observations.jsonl"
FIRST = "FIRST_BOUNDARY: the original controlled source starts here."
EARLIEST_CONSTRAINT = "EARLIEST_CONSTRAINT: source attribution is not world-fact verification."
LAST = "LAST_BOUNDARY: the original controlled source ends here."
UNRESOLVED_MARKER = "UNRESOLVED_CONTROL: independent world-fact verification remains pending."
CURRENT_INPUT = "CURRENT_INPUT: read all twelve pages and cite first, earliest constraint, and last."
GOAL = (
    "Controlled native Context rotation: deliver REPORT.md from the registered source.\n"
    f"{EARLIEST_CONSTRAINT}\n{UNRESOLVED_MARKER}\n{CURRENT_INPUT}"
)


def _page(number: int) -> str:
    head = f"CONTROLLED_SOURCE_PAGE_{number + 1:02d}\n\n"
    if number == 0:
        head += FIRST + "\n\n" + EARLIEST_CONSTRAINT + "\n\n"
    tail = "\n\n" + LAST + "\n" if number == READS - 1 else "\n"
    # ASCII, exactly 8100 bytes, with a newline precisely at every page boundary.
    size = PAGE_CHARS - len(head) - len(tail)
    phrase = "original source data only; "
    padding = (phrase * (size // len(phrase) + 1))[:size]
    return head + padding + tail


SOURCE_TEXT = "".join(_page(number) for number in range(READS))
SOURCE_SHA256 = hashlib.sha256(SOURCE_TEXT.encode()).hexdigest()
QUOTES = (FIRST, EARLIEST_CONSTRAINT, LAST)
REPORT_TEXT = "# Controlled source attribution\n\n" + "\n".join(QUOTES) + "\n\n" + (
    UNRESOLVED_MARKER + "\n" + CURRENT_INPUT + "\n"
)
REPORT_SHA256 = hashlib.sha256(REPORT_TEXT.encode()).hexdigest()


def _case(case: str) -> None:
    if case != CASE:
        raise ValueError(f"unknown native-context case: {case}")


def native_context_materials(case: str = CASE) -> tuple[dict[str, str], ...]:
    """Pure UI import export: write this single file outside the UI input field."""
    _case(case)
    return ({"path": SOURCE_PATH, "content": SOURCE_TEXT, "kind": "text/markdown"},)


def native_context_mission(case: str = CASE) -> dict[str, Any]:
    """Pure public facade/UI fields; doc9 is resolved by the current domain registry."""
    _case(case)
    return {
        "goal": GOAL,
        "success_criteria": [f"file:{REPORT_PATH}", f"cite:{SOURCE_PATH}"],
        "domain": "doc-research-v1",
        "idempotency_key": CASE,
        "budget": {"max_tokens": 160_000, "max_attempts": 4},
    }


def _values(request: Any) -> list[dict[str, Any]]:
    values = []
    for message in request.messages:
        if str(message.role) != "tool":
            continue
        value = json.loads(message.content).get("value")
        if not isinstance(value, dict):
            raise TypeError("native-context actual tool result failed or was truncated")
        values.append(value)
    return values


def _source_page(value: dict[str, Any]) -> int:
    offset = value.get("offset")
    if type(offset) is not int or offset % PAGE_CHARS or not 0 <= offset < len(SOURCE_TEXT):
        raise ValueError("native-context unexpected page offset")
    end = offset + PAGE_CHARS
    if (value.get("sha256") != SOURCE_SHA256
            or value.get("content") != SOURCE_TEXT[offset:end]
            or value.get("total_chars") != len(SOURCE_TEXT)
            or value.get("next_offset") != (end if end < len(SOURCE_TEXT) else None)):
        raise ValueError("native-context source version/content/continuation changed")
    return offset // PAGE_CHARS


def native_context_provider(root: Path, *, control_root: Path | None = None) -> Any:
    """A bounded external Provider fixture, leaving Context and tools untouched."""
    from agent_orchestrator.testing.fixtures import (
        RoleScriptedProvider,
        envelope_step,
        graph_proposal_step,
        package_of,
        role_of,
    )
    from simple_harness.execution.provider_invocations import (
        provider_request_fingerprint,
    )

    root = Path(root).resolve(strict=True)
    controls = (Path(control_root) if control_root is not None
                else root.with_name(root.name + "-controls")).resolve(strict=False)
    if not root.is_dir() or ".local-test-evidence" not in root.parts:
        raise ValueError("native-context root must be ignored local test evidence")
    if (".local-test-evidence" not in controls.parts or controls == root
            or root in controls.parents or controls in root.parents):
        raise ValueError("native-context controls must be a separate ignored local directory")
    controls.mkdir(parents=True, exist_ok=True)
    trace = controls / OBSERVATIONS_FILE
    if trace.is_symlink():
        raise ValueError("native-context observation path must not be a symlink")
    cold = trace.exists()
    criteria = native_context_mission()["success_criteria"]
    observed_pages: set[int] = set()
    current_inputs: dict[tuple[str, str], str] = {}
    worker_calls = 0

    def planner(request: Any) -> str:
        package = package_of(request)
        return graph_proposal_step([{
            "key": CASE, "goal": GOAL,
            "rationale": "read twelve actual pages; retain the original attribution constraint",
            "dependencies": [], "success_criteria": criteria,
            "verification_policy": ["format_check", "rule_check", "critic_review"],
            "allowed_tools": ["workspace_read_file", "workspace_write_file", "workspace_list"],
            "outputs": [REPORT_PATH],
            "budget": {"max_tokens": package["budget_for_tasks"]["max_tokens"],
                       "max_attempts": 1},
        }])

    def worker(request: Any) -> Any:
        nonlocal worker_calls
        # The phase counter controls the fixture's next response; it is not recall.
        # Every advance requires the previous *actual outbound* tool page.
        phase = worker_calls
        worker_calls += 1
        pages = {_source_page(value) for value in _values(request)
                 if value.get("path") == SOURCE_PATH and "content" in value}
        if phase and phase <= READS and phase - 1 not in pages:
            raise ValueError("native-context latest actual source page missing")
        observed_pages.update(pages)
        if phase < READS:
            return "workspace_read_file", {
                "path": SOURCE_PATH, "offset": phase * PAGE_CHARS,
                "max_chars": PAGE_CHARS, "expected_sha256": SOURCE_SHA256,
            }
        if observed_pages != set(range(READS)):
            raise ValueError("native-context did not observe all twelve exact source pages")
        if phase == READS:
            return "workspace_write_file", {"path": REPORT_PATH, "content": REPORT_TEXT}
        if phase != READS + 1 or not any(
            value.get("path") == REPORT_PATH and value.get("bytes") == len(REPORT_TEXT.encode())
            for value in _values(request)
        ):
            raise ValueError("native-context actual report write missing")

        def cited(body: dict[str, Any]) -> dict[str, Any]:
            for claim, quote in zip(body["claims"], QUOTES, strict=True):
                line = SOURCE_TEXT.splitlines().index(quote) + 1
                claim["citations"] = [{"path": SOURCE_PATH, "version": SOURCE_SHA256,
                                       "start_line": line, "end_line": line, "quote": quote}]
            return body

        return envelope_step(
            summary="Controlled attribution only; independent verification remains unresolved.",
            artifacts=[REPORT_PATH], claims=QUOTES, override=cited,
        )(request)

    def critic(request: Any) -> Any:
        reports = [value for value in _values(request)
                   if value.get("path") == REPORT_PATH and "content" in value]
        if not reports:
            return "workspace_read_file", {"path": REPORT_PATH}
        if reports[-1]["content"] != REPORT_TEXT:
            raise ValueError("native-context Critic did not read the exact report")
        body = {"verdict": "PASS", "findings": [], "needs_human": False,
                "mission_criteria": [{"criterion": item, "met": True,
                                      "reason": "actual report read; attributed quotes only"}
                                     for item in criteria]}
        return "<critic_verdict>" + json.dumps(body) + "</critic_verdict>"

    class Provider(RoleScriptedProvider):
        def __init__(self) -> None:
            super().__init__({"planner": [planner], "worker": [worker] * (READS + 2),
                              "critic": [critic] * 2})
            self.control_root = controls

        async def invoke(self, request: Any, *, cancel: Any) -> Any:
            if cold:
                raise AssertionError("completed native-context cold reopen must not call Provider")
            if self.calls >= EXPECTED_CALLS:
                raise AssertionError("native-context exceeded its 17-call bound")
            package = package_of(request)
            role = role_of(request)
            if package.get("source_versions") != {SOURCE_PATH: SOURCE_SHA256}:
                raise ValueError("native-context requires actual registered source version")
            domain = package.get("domain", {})
            if domain.get("id") != "doc-research-v1" or str(domain.get("version")) != "9":
                raise ValueError("native-context requires the formal doc9 runtime")
            if role == "planner":
                mission = package["mission"]
                goal, original_criteria = mission["goal"], mission["success_criteria"]
                subject = mission["mission_id"]
            else:
                goal, original_criteria = package["mission_root_goal"], package["mission_success_criteria"]
                contract = package["task_contract"]
                if contract["goal"] != GOAL or contract["success_criteria"] != criteria:
                    raise ValueError("native-context original Task constraint changed")
                subject = package.get("attempt_id") or package["attempt"]["attempt_id"]
            if goal != GOAL or original_criteria != criteria:
                raise ValueError("native-context original Mission constraint changed")
            user = next(message.content for message in reversed(request.messages)
                        if str(message.role) == "user")
            input_hash = hashlib.sha256(user.encode()).hexdigest()
            key = (role, subject)
            if current_inputs.setdefault(key, input_hash) != input_hash:
                raise ValueError("native-context current input changed across requests")
            if any(marker not in user for marker in (EARLIEST_CONSTRAINT, UNRESOLVED_MARKER, CURRENT_INPUT)):
                raise ValueError("native-context required current-input marker missing")
            pages = [value for value in _values(request)
                     if value.get("path") == SOURCE_PATH and "content" in value]
            for value in pages:
                _source_page(value)
            latest = max(pages, key=lambda value: value["offset"]) if pages else None
            # Whitelist only; never persist request text, source text, or a PASS/rotation claim.
            row = {
                "schema": 1, "case": CASE, "ordinal": self.calls + 1, "role": role,
                "subject_id": subject, "request_id": request.request_id.value,
                "request_hash": provider_request_fingerprint(request),
                "current_input_hash": input_hash,
                "goal_markers": ["earliest_constraint", "unresolved_control", "current_input"],
                "source_sha256": SOURCE_SHA256,
                "latest_offset": latest["offset"] if latest else None,
                "latest_page_sha256": hashlib.sha256(latest["content"].encode()).hexdigest() if latest else None,
            }
            encoded = (json.dumps(row, sort_keys=True) + "\n").encode()
            if trace.exists() and trace.stat().st_size + len(encoded) > 32768:
                raise ValueError("native-context observation bound exceeded")
            descriptor = os.open(trace, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                if os.write(descriptor, encoded) != len(encoded):
                    raise OSError("short native-context observation write")
            finally:
                os.close(descriptor)
            return await super().invoke(request, cancel=cancel)

    return Provider()
