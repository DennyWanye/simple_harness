"""NEXT-TG-1.0 §6.4: every new Mission runs on the strict TaskGraph (default ON).

The Host writes the requirement in the creation transaction (only for a Mission it
really created) and, after every planning grant and every loop round, binds each
required Mission through the original authenticated enable command with a
Mission-derived command id.  "No grant yet" is the normal wait; anything else is a
logged deployment fault and the Mission keeps waiting.
"""

from __future__ import annotations

from types import SimpleNamespace


from deskpet.orchestration.service import OrchestrationService
from ._word_counter import FixtureWordCounter
from deskpet.orchestration.settings import OrchestrationSettings, load_settings


class _Policy:
    def __init__(self, refusals):
        self.refusals, self.calls = dict(refusals), []

    def enable_taskgraph_contract(self, mission_id, command_id):
        self.calls.append((mission_id, command_id))
        if mission_id in self.refusals:
            raise RuntimeError(self.refusals[mission_id])
        return {"mission_id": mission_id}


def _service(tmp_path, monkeypatch, *, waiting, refusals=(), strict=True):
    import agent_orchestrator.orchestrator.taskgraph_requirement as requirement

    service = OrchestrationService(tmp_path, OrchestrationSettings(strict_taskgraph=strict),
                                   principal=object(), drive=False, native_test_counter=FixtureWordCounter())
    store = object()
    service._orchestrator = SimpleNamespace(store=store)
    service._taskgraph = SimpleNamespace(policy=_Policy(dict(refusals)))
    written = []
    monkeypatch.setattr(requirement, "missions_awaiting_taskgraph", lambda s: list(waiting))
    monkeypatch.setattr(requirement, "require_taskgraph", lambda s, m: written.append(m))
    wakes = []
    service.wake = lambda: wakes.append(1)
    return service, written, wakes


def test_default_on_and_only_an_explicit_false_turns_it_off():
    assert OrchestrationSettings().strict_taskgraph is True
    assert load_settings({}).strict_taskgraph is True
    assert load_settings({"strict_taskgraph": "false"}).strict_taskgraph is True
    assert load_settings({"strict_taskgraph": False}).strict_taskgraph is False


def test_the_requirement_is_written_only_for_a_mission_really_created(tmp_path, monkeypatch):
    service, written, _ = _service(tmp_path, monkeypatch, waiting=())
    service._require_strict_taskgraph({"mission_id": "m1", "created": True})
    service._require_strict_taskgraph({"mission_id": "m2", "created": False})  # idempotent replay
    assert written == ["m1"]
    off, written_off, _ = _service(tmp_path, monkeypatch, waiting=(), strict=False)
    off._require_strict_taskgraph({"mission_id": "m3", "created": True})
    assert written_off == []
    uninstalled, written_none, _ = _service(tmp_path, monkeypatch, waiting=())
    uninstalled._taskgraph = None
    uninstalled._require_strict_taskgraph({"mission_id": "m4", "created": True})
    assert written_none == []


def test_the_coordinator_binds_granted_missions_and_waits_for_the_rest(tmp_path, monkeypatch, caplog):
    service, _, wakes = _service(
        tmp_path, monkeypatch, waiting=("m-ok", "m-no-grant", "m-broken"),
        refusals={"m-no-grant": "TASKGRAPH_ENABLE_PLANNING_AUTHORIZATION_REQUIRED",
                  "m-broken": "taskgraph_deployed_source_unverified"})
    assert service._enable_required_taskgraphs() == 1
    policy = service._taskgraph.policy
    assert policy.calls[0] == ("m-ok", "taskgraph-enable:m-ok:taskgraph-exec-v2")
    assert wakes == [1]
    assert "m-no-grant" not in service._taskgraph_faults
    assert "source_unverified" in service._taskgraph_faults["m-broken"]
    # the same fault is logged once, not every round
    caplog.clear()
    service._enable_required_taskgraphs()
    assert not [r for r in caplog.records if "taskgraph enable refused" in r.getMessage()]


def test_the_detail_says_whether_the_mission_waits_and_why(tmp_path, monkeypatch):
    import agent_orchestrator.orchestrator.taskgraph_requirement as requirement

    service, _, _ = _service(tmp_path, monkeypatch, waiting=())
    monkeypatch.setattr(requirement, "taskgraph_required", lambda s, m: m != "m-old")
    monkeypatch.setattr(requirement, "awaiting_taskgraph", lambda s, m: m == "m-wait")
    service._taskgraph_faults["m-wait"] = "taskgraph_deployed_source_unverified"
    service._taskgraph_faults["m-bound"] = "stale"
    assert service._taskgraph_state("m-old") == {"required": False, "waiting": False, "fault": None}
    assert service._taskgraph_state("m-bound") == {"required": True, "waiting": False, "fault": None}
    assert service._taskgraph_state("m-wait") == {
        "required": True, "waiting": True, "fault": "taskgraph_deployed_source_unverified"}
