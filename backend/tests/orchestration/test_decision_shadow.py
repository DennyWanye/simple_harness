# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PR-7: the Host seam for a Shadow-only READY_TASK_PRIORITY observation.

The SDK half of the seam is tested in the SDK repo.  This suite pins the Host
half — the part the parent's ruling put here:

* the Host owns ``decision.mode``, it comes from ``config.toml [orchestration]``,
  and **absent, unknown and malformed all mean the existing path**;
* there is no ad-hoc environment variable, and no Host value reaches the SDK's
  Primary mode;
* an observation never changes the allocator's grant set, and a failing shadow
  provider is reported rather than raised;
* ``RETRY_OR_ESCALATE`` is not wired.

The tests run against whatever SDK is installed.  Machine-independent behaviour is
pinned directly; the tests that need the real decision contract are skipped with a
reason when the installed wheel predates it, so the module's own contract is never
proved only by a fallback.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

from deskpet.orchestration import decision as seam
from deskpet.orchestration.native_fixture import FixtureWordCounter
from deskpet.orchestration.settings import (
    HOST_DECISION_MODES,
    SHADOW_TIMEOUT_CEILING_SECONDS,
    OrchestrationSettings,
    load_settings,
)
from deskpet.orchestration.wiring import read_section

# Everything below that names an SDK type lives behind this guard so the module
# still imports on a wheel that predates the decision package: the sealed tests
# then skip with a reason instead of the file erroring at collection.
if seam.seam_available():  # pragma: no cover - the guard is the point of the skip
    from agent_orchestrator.contracts import (
        Attempt,
        AttemptStatus,
        Budget,
        Task,
        TaskStatus,
    )
    from agent_orchestrator.decision import DecisionEventJournal, DecisionProvider
    from agent_orchestrator.decision.types import DecisionResult, DecisionScore
    from agent_orchestrator.scheduling.allocator import allocate
    from agent_orchestrator.storage.store import Store

    def task_row(task_id, *, priority=1.0, status=None, deps=(), paused=False, ready_at=0.0):
        return Task(
            id=task_id,
            mission_id=MISSION,
            parent_task_ids=(),
            dependency_ids=deps,
            goal=f"goal of {task_id}",
            rationale="r",
            success_criteria=("file:x",),
            verification_policy=("format_check",),
            allowed_tools=(),
            budget=Budget(max_tokens=10_000, max_attempts=3),
            priority=priority,
            status=TaskStatus.READY if status is None else status,
            version=1,
            paused=paused,
            ready_at=ready_at,
            kind="work",
        )


    def attempt_row(task_id, ordinal=1, status=None):
        return Attempt(
            id=f"{task_id}:attempt-{ordinal}",
            task_id=task_id,
            mission_id=MISSION,
            role="worker",
            model="x",
            prompt_version="v",
            context_version="c",
            budget_reserved=Budget(max_tokens=1),
            lease_owner=None,
            lease_expires_at=None,
            status=AttemptStatus.RUNNING if status is None else status,
            retry_of=None,
            created_at=1.0,
            version=1,
            ordinal=ordinal,
            creation_key=f"k-{task_id}-{ordinal}",
            input_id="i",
            failure=None,
        )


    def two_ready():
        return [task_row(f"{MISSION}:task-{i}", priority=float(i)) for i in (1, 2)]


    class StubShadow(DecisionProvider):
        """A shadow provider whose answer, lateness and failure the test chooses."""

        def __init__(self, selected=None, *, error=None, sticky=None):
            self.selected = selected
            self.error = error
            self.sticky = sticky
            self.calls = 0

        async def decide(self, request):
            self.calls += 1
            if self.sticky is not None:
                await self.sticky.wait()
            if self.error is not None:
                raise self.error
            chosen = self.selected or request.candidates[0].id
            scores = tuple(
                DecisionScore(c.id, 1.0 if c.id == chosen else 0.0) for c in request.candidates
            )
            return DecisionResult(
                decision_id=request.decision_id,
                provider="stub-shadow",
                selected=chosen,
                scores=scores,
                top1_probability=1.0,
                margin=1.0,
                latency_ms=0.0,
            )


    def host_journal(tmp_path) -> object:
        return DecisionEventJournal(Store.open(tmp_path / "orchestrator.db"))




    # --------------------------------------------------------------------------------------
    # Builders
    # --------------------------------------------------------------------------------------




requires_contract = pytest.mark.skipif(
    not seam.seam_available(),
    reason="installed SDK predates the Host-facing decision seam",
)

MISSION = "m-host-pr7"


# ---- builders (need the contract types) ----
def host_events(journal) -> list[tuple[str, dict]]:
    rows = journal.store.connection.execute(
        "SELECT type, payload_json FROM events ORDER BY seq"
    ).fetchall()
    return [(row["type"], json.loads(row["payload_json"])) for row in rows]


def referenced_names(path: Path) -> set[str]:
    """Every identifier a module's code mentions — imports, calls, attributes.

    Reading the source through the AST means a docstring that *talks about* a
    deferred seam is not mistaken for a seam that exists.
    """

    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                found.add(alias.name.rsplit(".", 1)[-1])
                if alias.asname:
                    found.add(alias.asname)
    return found


# --------------------------------------------------------------------------------------
# 1. The Host owns the mode, and the default is EXISTING
# --------------------------------------------------------------------------------------


def test_settings_default_to_existing():
    """No key at all is the ordinary install, and it must not observe."""

    settings = OrchestrationSettings()
    assert settings.decision_mode == "existing"
    assert settings.decision_shadow_timeout_seconds is None
    assert load_settings({}).decision_mode == "existing"
    assert load_settings(None).decision_mode == "existing"
    assert load_settings({}).decision_shadow_timeout_seconds is None


@pytest.mark.parametrize(
    "written",
    ["nanojev", "NANOJEV", "primary", "on", "true", "shadow-mode", "1", "", "  "],
)
def test_an_unrecognised_mode_falls_back_to_existing(written):
    """A typo must never turn an observation on — and never reach Primary."""

    assert load_settings({"decision_mode": written}).decision_mode == "existing"


@pytest.mark.parametrize("written", [True, False, 1, 0, 1.0, None, [], {}, ["shadow"]])
def test_a_non_string_mode_falls_back_to_existing(written):
    assert load_settings({"decision_mode": written}).decision_mode == "existing"


def test_shadow_is_the_only_mode_beyond_existing_and_it_is_explicit():
    assert load_settings({"decision_mode": "shadow"}).decision_mode == "shadow"
    assert load_settings({"decision_mode": "  SHADOW  "}).decision_mode == "shadow"
    assert HOST_DECISION_MODES == ("existing", "shadow")


def test_the_host_has_no_value_that_reaches_the_sdk_primary_mode():
    """Primary is a later gate; no Host configuration may select it."""

    assert "nanojev" not in HOST_DECISION_MODES
    source = Path(seam.__file__).read_text(encoding="utf-8")
    assert "DecisionMode.NANOJEV" not in source
    # The only two policies the seam can build are EXISTING and SHADOW.
    for mode in HOST_DECISION_MODES:
        built = seam.build_decision_seam(replace(OrchestrationSettings(), decision_mode=mode))
        if seam.seam_available():
            assert str(built.policy().mode) == mode
        assert built.observation_enabled in (True, False)


def test_the_mode_is_never_read_from_the_environment(monkeypatch):
    """The ruling forbids an ad-hoc variable, so one must change nothing."""

    monkeypatch.setenv("DESKPET_DECISION_MODE", "shadow")
    monkeypatch.setenv("DECISION_MODE", "shadow")
    monkeypatch.setenv("NANOJEV_SHADOW", "1")
    assert load_settings({}).decision_mode == "existing"
    assert load_settings({"decision_mode": "existing"}).decision_mode == "existing"
    source = Path(seam.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "getenv" not in source


def test_config_toml_loads_the_key_from_a_real_file(tmp_path):
    """The carrier is the Host's own config file, read the way startup reads it."""

    config = tmp_path / "config.toml"
    config.write_text(
        '[orchestration]\nenabled = true\ndecision_mode = "shadow"\n'
        "decision_shadow_timeout_seconds = 2.5\n",
        encoding="utf-8",
    )
    settings = load_settings(read_section(config))
    assert settings.decision_mode == "shadow"
    assert settings.decision_shadow_timeout_seconds == 2.5

    config.write_text("[orchestration]\nenabled = true\n", encoding="utf-8")
    assert load_settings(read_section(config)).decision_mode == "existing"


@pytest.mark.parametrize(
    "written, expected",
    [
        (2, 2.0),
        (0.25, 0.25),
        ("2", None),
        (-1, None),
        (0, None),
        (True, None),
        (float("inf"), SHADOW_TIMEOUT_CEILING_SECONDS),
        (1e9, SHADOW_TIMEOUT_CEILING_SECONDS),
    ],
)
def test_the_shadow_timeout_is_bounded_or_ignored(written, expected):
    result = load_settings({"decision_mode": "shadow", "decision_shadow_timeout_seconds": written})
    assert result.decision_shadow_timeout_seconds == expected


def test_an_unknown_mode_value_survives_no_further_than_the_parser():
    """Defence in depth: even a hand-built settings object cannot smuggle a mode in."""

    built = seam.build_decision_seam(replace(OrchestrationSettings(), decision_mode="nanojev"))
    assert built.mode == seam.EXISTING
    assert built.observation_enabled is False
    assert seam.build_decision_seam(object()).mode == seam.EXISTING
    assert seam.build_decision_seam(replace(OrchestrationSettings(), decision_mode=7)).mode == (
        seam.EXISTING
    )


# --------------------------------------------------------------------------------------
# 2. Without the SDK contract the seam degrades, it does not fail
# --------------------------------------------------------------------------------------


def test_the_seam_never_raises_when_the_sdk_is_absent(monkeypatch):
    """The vendored wheel may predate the contract; that is not a startup failure."""


    def unavailable():
        return None

    built = seam.build_decision_seam(
        replace(OrchestrationSettings(), decision_mode="shadow"), journal=object()
    )
    monkeypatch.setattr(built, "_sdk", unavailable)
    assert built.observation_enabled is False
    assert built.status.available is False


# --------------------------------------------------------------------------------------
# 3. The observation never moves the allocator's grant set
# --------------------------------------------------------------------------------------


@requires_contract
def test_existing_mode_returns_exactly_what_allocate_returns(tmp_path):
    tasks, attempts = two_ready(), []
    built = seam.build_decision_seam(
        OrchestrationSettings(), journal=host_journal(tmp_path)
    )
    plan, observed = built.ready_priority_plan(tasks, attempts, concurrency_limit=4)
    assert observed is False
    assert plan.to_json() == allocate(tasks, attempts, concurrency_limit=4).to_json()
    assert host_events(built.journal) == [], "no observation means no event at all"


@requires_contract
def test_shadow_observation_leaves_the_plan_identical(tmp_path):
    tasks, attempts = two_ready(), [attempt_row(f"{MISSION}:task-1")]
    shadow = StubShadow()
    built = seam.build_decision_seam(
        replace(OrchestrationSettings(), decision_mode="shadow"),
        journal=host_journal(tmp_path),
        shadow_provider=shadow,
    )
    plan, observed = built.ready_priority_plan(tasks, attempts, concurrency_limit=4)
    quiet = allocate(tasks, attempts, concurrency_limit=4)
    assert observed is True
    assert shadow.calls == 1
    assert plan.to_json() == quiet.to_json()
    assert plan.grants == quiet.grants
    assert plan.open_attempts == quiet.open_attempts
    assert plan.eligible == quiet.eligible
    assert plan.slots == quiet.slots


@requires_contract
def test_shadow_observation_without_a_journal_does_not_call_the_model(tmp_path):
    """No sink means no attribution, so there is nothing to observe."""

    shadow = StubShadow()
    built = seam.build_decision_seam(
        replace(OrchestrationSettings(), decision_mode="shadow"),
        journal=None,
        shadow_provider=shadow,
    )
    assert built.observation_enabled is False
    plan, observed = built.ready_priority_plan(two_ready(), [], concurrency_limit=4)
    assert observed is False
    assert shadow.calls == 0
    assert plan.grants


@requires_contract
def test_single_ready_task_does_not_call_the_model(tmp_path):
    shadow = StubShadow()
    built = seam.build_decision_seam(
        replace(OrchestrationSettings(), decision_mode="shadow"),
        journal=host_journal(tmp_path),
        shadow_provider=shadow,
    )
    plan, observed = built.ready_priority_plan(
        [task_row(f"{MISSION}:task-1")], [], concurrency_limit=4
    )
    assert plan.grants
    assert shadow.calls == 0
    assert observed is False


@requires_contract
def test_no_ready_task_does_not_call_the_model(tmp_path):
    shadow = StubShadow()
    built = seam.build_decision_seam(
        replace(OrchestrationSettings(), decision_mode="shadow"),
        journal=host_journal(tmp_path),
        shadow_provider=shadow,
    )
    plan, observed = built.ready_priority_plan(
        [task_row(f"{MISSION}:task-1", status=TaskStatus.BLOCKED)], [], concurrency_limit=4
    )
    assert plan.grants == ()
    assert shadow.calls == 0
    assert observed is False


@requires_contract
def test_shadow_cannot_grant_a_task_the_allocator_held_back(tmp_path):
    """The shadow answer is recorded, never applied — even when it is 'confident'.

    The candidate set is the frontier, so a paused Task is not even offered.  A
    provider that answers with a Task outside that set is refused by the SDK's own
    result validation, which is the strongest form of "cannot be applied".
    """

    tasks = [
        task_row(f"{MISSION}:task-1", priority=1.0, paused=True),  # held back by the allocator
        task_row(f"{MISSION}:task-2", priority=2.0),
        task_row(f"{MISSION}:task-3", priority=3.0),
    ]
    shadow = StubShadow()
    built = seam.build_decision_seam(
        replace(OrchestrationSettings(), decision_mode="shadow"),
        journal=host_journal(tmp_path),
        shadow_provider=shadow,
    )
    plan, observed = built.ready_priority_plan(tasks, [], concurrency_limit=4)
    assert observed is True
    assert shadow.calls == 1
    direct = allocate(tasks, [], concurrency_limit=4)
    assert plan.to_json() == direct.to_json()
    granted = [task.id for task, _ in plan.grants]
    assert f"{MISSION}:task-1" not in granted
    assert granted == [task.id for task, _ in direct.grants]
    events = host_events(built.journal)
    requested = next(payload for name, payload in events if name == "DecisionRequested")
    assert f"{MISSION}:task-1" not in requested["candidates"]
    assert requested["candidates"] == [f"{MISSION}:task-3", f"{MISSION}:task-2"]


@requires_contract
def test_a_shadow_answer_outside_the_candidate_set_is_refused(tmp_path):
    """A drifting provider cannot smuggle a non-candidate into the record."""

    tasks = two_ready()
    shadow = StubShadow(selected="not-a-candidate")
    built = seam.build_decision_seam(
        replace(OrchestrationSettings(), decision_mode="shadow"),
        journal=host_journal(tmp_path),
        shadow_provider=shadow,
    )
    plan, observed = built.ready_priority_plan(tasks, [], concurrency_limit=4)
    assert plan.to_json() == allocate(tasks, [], concurrency_limit=4).to_json()
    assert plan.grants
    kinds = [name for name, _ in host_events(built.journal)]
    assert kinds == ["DecisionRequested", "ShadowDecisionFailed"]
    assert built.status.failures >= 1
    assert observed is True


# --------------------------------------------------------------------------------------
# 4. A shadow failure is reported, never raised
# --------------------------------------------------------------------------------------


@requires_contract
@pytest.mark.parametrize(
    "error",
    [RuntimeError("model exploded"), ValueError("bad scores"), OSError("no checkpoint")],
)
def test_a_raising_shadow_provider_does_not_break_the_plan(tmp_path, error):
    shadow = StubShadow(error=error)
    built = seam.build_decision_seam(
        replace(OrchestrationSettings(), decision_mode="shadow"),
        journal=host_journal(tmp_path),
        shadow_provider=shadow,
    )
    plan, observed = built.ready_priority_plan(two_ready(), [], concurrency_limit=4)
    assert plan.to_json() == allocate(two_ready(), [], concurrency_limit=4).to_json()
    assert plan.grants
    assert observed is True, "the observation was attempted and recorded as a failure"
    assertion = built.status
    assert assertion.failures >= 1
    assert assertion.last_error is not None
    kinds = [name for name, _ in host_events(built.journal)]
    assert "ShadowDecisionFailed" in kinds
    assert "ShadowDecisionProduced" not in kinds


@requires_contract
def test_a_hanging_shadow_provider_is_bounded_by_the_host_timeout(tmp_path):
    sticky = asyncio.Event()
    shadow = StubShadow(sticky=sticky)
    built = seam.build_decision_seam(
        replace(
            OrchestrationSettings(),
            decision_mode="shadow",
            decision_shadow_timeout_seconds=0.05,
        ),
        journal=host_journal(tmp_path),
        shadow_provider=shadow,
    )
    plan, observed = built.ready_priority_plan(two_ready(), [], concurrency_limit=4)
    assert plan.to_json() == allocate(two_ready(), [], concurrency_limit=4).to_json()
    assert observed is True
    kinds = [name for name, _ in host_events(built.journal)]
    assert "ShadowDecisionFailed" in kinds


@requires_contract
def test_shadow_mode_without_a_provider_degrades_instead_of_raising(tmp_path):
    """A misconfigured shadow is recorded, and the production plan still comes back."""

    built = seam.build_decision_seam(
        replace(OrchestrationSettings(), decision_mode="shadow"),
        journal=host_journal(tmp_path),
        shadow_provider=None,
    )
    plan, observed = built.ready_priority_plan(two_ready(), [], concurrency_limit=4)
    quiet = allocate(two_ready(), [], concurrency_limit=4)
    # Either outcome is acceptable for production; what is not acceptable is a raise
    # or a changed plan.  The seam reports which happened.
    assert plan.to_json() in (quiet.to_json(), quiet.to_json())
    assert observed in (True, False)
    assert plan.to_json() == quiet.to_json()


@requires_contract
def test_a_broken_journal_does_not_lose_the_plan(tmp_path, monkeypatch):
    """The store is a sink, not a dependency: a store failure must not fail dispatch.

    The observation is still *attempted* — the provider is asked and the failure
    is recorded against the thread — but the store can no longer file it, so the
    seam reports the failure in its status and hands back the untouched plan.
    """

    built = seam.build_decision_seam(
        replace(OrchestrationSettings(), decision_mode="shadow"),
        journal=host_journal(tmp_path),
        shadow_provider=StubShadow(),
    )

    def explode(*args, **kwargs):
        raise RuntimeError("store is gone")

    monkeypatch.setattr(built.journal, "append", explode)
    plan, observed = built.ready_priority_plan(two_ready(), [], concurrency_limit=4)
    assert plan.to_json() == allocate(two_ready(), [], concurrency_limit=4).to_json()
    assert plan.grants
    assert observed is True, "the provider was asked; only the filing failed"
    assert built.status.failures >= 1
    assert built.status.last_error is not None
    assert host_events(built.journal) == [], "nothing could be written, and that is reported"


@requires_contract
def test_an_absent_mission_id_is_reported_not_crashed():
    """The seam cannot mint a deterministic id without a Mission, so it declines."""

    orphan = [task_row("task-1"), task_row("task-2")]
    for task in orphan:
        object.__setattr__(task, "mission_id", "")
    built = seam.build_decision_seam(
        replace(OrchestrationSettings(), decision_mode="shadow"),
        journal=object(),
        shadow_provider=StubShadow(),
    )
    plan, observed = built.ready_priority_plan(orphan, [], concurrency_limit=4)
    assert observed is False
    assert built.status.failures >= 1
    assert plan.grants


# --------------------------------------------------------------------------------------
# 5. The recorded thread, and the retry seam that is deliberately absent
# --------------------------------------------------------------------------------------


@requires_contract
def test_the_observation_is_filed_under_a_deterministic_njr_id(tmp_path):
    built = seam.build_decision_seam(
        replace(OrchestrationSettings(), decision_mode="shadow"),
        journal=host_journal(tmp_path),
        shadow_provider=StubShadow(),
    )
    built.ready_priority_plan(two_ready(), [], concurrency_limit=4)
    events = host_events(built.journal)
    assert events, "an observation must be attributable"
    for _, payload in events:
        assert payload["decision_id"].startswith("njr-")
        assert not payload["decision_id"].startswith("pd-")
        assert payload["decision_type"] == "ready_task_priority"
    # Deterministic: the same frontier yields the same id, so a replay is one thread.
    built.ready_priority_plan(two_ready(), [], concurrency_limit=4)
    again = host_events(built.journal)
    assert [p["decision_id"] for _, p in again] == [p["decision_id"] for _, p in events]


def test_no_retry_or_escalate_path_is_wired():
    """RetryAction has six values; mapping them is a ruling this slice does not have.

    The scan is over the module's *code* — every identifier it references — not
    over its prose.  The docstring deliberately explains the deferral, so a raw
    text scan would fail on the explanation instead of on an adapter.
    """

    exported = set(seam.__all__)
    assert not {name for name in exported if "retry" in name.lower()}
    assert not {name for name in exported if "escalate" in name.lower()}
    names = referenced_names(Path(seam.__file__))
    # The only retry-shaped name in the module is the status flag that reports the
    # absence; everything else would be an adapter this ruling has not authorised.
    assert {name for name in names if "retry" in name.lower()} == {"retry_wired"}
    assert not {name for name in names if "escalate" in name.lower()}
    assert "RetryAction" not in names
    assert "RetryDecision" not in names
    assert "DecisionType" not in names, "the seam never names the decision types at all"
    callables = {
        name
        for name, value in vars(seam).items()
        if (callable(value) or inspect.isclass(value))
        and getattr(value, "__module__", "") == seam.__name__
    }
    assert callables == {
        "DecisionSeam",
        "DecisionSeamStatus",
        "build_decision_seam",
        "seam_available",
    }
    signature = inspect.signature(seam.DecisionSeam.ready_priority_plan)
    assert "retry" not in " ".join(signature.parameters).lower()


def test_the_status_projection_carries_no_secrets_and_never_claims_retry():
    projection = seam.DecisionSeamStatus().to_json()
    assert projection["retry_wired"] is False
    assert set(projection) == {
        "mode",
        "observation_enabled",
        "observations",
        "failures",
        "last_error",
        "available",
        "detail",
        "retry_wired",
    }


def test_the_seam_module_stays_out_of_the_drive_loop():
    """The seam is a library the allocator site calls; it owns no loop and no task."""

    source = Path(seam.__file__).read_text(encoding="utf-8")
    assert "create_task" not in source
    assert "get_running_loop" not in source
    assert "asyncio.run(" in source, "the seam is synchronous at the allocator boundary"
    assert "logging" in source or "logger" in source


def test_ready_priority_plan_signature_is_the_allocator_seam():
    parameters = list(inspect.signature(seam.DecisionSeam.ready_priority_plan).parameters)
    assert parameters == ["self", "tasks", "attempts", "allocate_kwargs"]


# --------------------------------------------------------------------------------------
# 6. The service builds the seam and projects it
# --------------------------------------------------------------------------------------


def test_the_service_builds_the_seam_from_its_own_settings(tmp_path):
    """The service is where the key actually reaches a running process."""

    from deskpet.orchestration.service import OrchestrationService

    service = OrchestrationService(
        tmp_path / "orchestration",
        replace(OrchestrationSettings(), decision_mode="existing"),
        principal=object(),
        drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    assert service._decision is None, "nothing is built before start()"
    service._install_decision_seam()
    assert service._decision is not None
    assert service._decision.mode == "existing"
    assert service._decision.observation_enabled is False

    shadow_service = OrchestrationService(
        tmp_path / "orchestration-shadow",
        replace(OrchestrationSettings(), decision_mode="shadow"),
        principal=object(),
        drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    shadow_service._install_decision_seam()
    assert shadow_service._decision.mode == "shadow"
    # No orchestrator was opened here, so there is no journal and nothing observes.
    assert shadow_service._decision.observation_enabled is False


def test_the_service_status_projects_the_decision_seam(tmp_path):
    from deskpet.orchestration.service import OrchestrationService

    service = OrchestrationService(
        tmp_path / "orchestration",
        replace(OrchestrationSettings(), decision_mode="shadow"),
        principal=object(),
        drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    service._install_decision_seam()
    projected = service.status()["decision"]
    assert projected["mode"] == "shadow"
    assert projected["retry_wired"] is False
    assert set(projected) == {
        "mode",
        "observation_enabled",
        "observations",
        "failures",
        "last_error",
        "available",
        "detail",
        "retry_wired",
    }


def test_the_service_status_projects_existing_when_the_seam_was_never_built(tmp_path):
    from deskpet.orchestration.service import OrchestrationService

    service = OrchestrationService(
        tmp_path / "orchestration", OrchestrationSettings(), principal=object(), drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    projected = service.status()["decision"]
    assert projected["mode"] == "existing"
    assert projected["observation_enabled"] is False


def test_the_service_imports_the_seam_lazily(tmp_path):
    """An absent SDK must not stop the module from importing or the service from starting."""

    source = Path(seam.__file__).read_text(encoding="utf-8")
    assert "from agent_orchestrator" in source
    # every SDK import in the seam is inside a function, so an old wheel is survivable
    import ast

    tree = ast.parse(source)
    module_level = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and "agent_orchestrator" in ast.unparse(node)
    ]
    assert module_level == []
