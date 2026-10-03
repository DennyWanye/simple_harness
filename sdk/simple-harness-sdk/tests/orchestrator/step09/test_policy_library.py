# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""The policy library (plan D9-1' / D9-3' / D9-4'): a library is seeded once with the
resolved, whitelisted, content-addressed parameters of its deployment; every Mission is
bound to that version; a later configuration change is recorded as drift and replaces
nothing.  Nothing writes the library after the seed — there is no proposal, evaluation,
promotion or rollback (removed 2026-10-02)."""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest
from fixtures_provider import RoleScriptedProvider

import agent_orchestrator.orchestrator.event_handler as event_handler
from agent_orchestrator.__main__ import main
from agent_orchestrator.api.policies import PolicyApi, PolicyRequestError
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.governance.promotion import (
    DEPLOYMENT_TIMELINE,
    PROMOTABLE,
    PolicyError,
    diff_params,
    registry_consistency,
    resolve_params,
    version_id,
)
from agent_orchestrator.orchestrator.commit_service import CommitService
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.storage.store import Store


def _library(tmp_path, **config):
    store = Store.open(Path(tmp_path) / "orchestrator.db")
    commit = CommitService(store)
    cfg = OrchestratorConfig(evidence_root=Path(tmp_path) / "e", **config)
    seed = commit.seed_policy(resolve_params(cfg), detail={"config": "unit"})
    return store, commit, cfg, seed


# ------------------------------------------------------------------ D9-1'
def test_params_are_resolved_whitelisted_and_content_addressed(tmp_path):
    cfg = OrchestratorConfig(evidence_root=Path(tmp_path), max_concurrency=2)
    full = resolve_params(cfg)
    slots = full["exploration_slots"]
    assert set(full) == set(PROMOTABLE) and full["mission_concurrency"] == 2
    assert version_id(full) == version_id(resolve_params(cfg, {"exploration_slots": slots}))
    assert version_id(full) != version_id(resolve_params(cfg, {"exploration_slots": slots + 1}))
    for core in (
        {"deployment_policy": {}},
        {"task_max_tokens": 1},
        {"knowledge_sharing": False},
        {"budgets": {}},
        {"code_test": False},
    ):
        with pytest.raises(PolicyError, match="not promotable"):
            resolve_params(cfg, core)


# ------------------------------------------------------------------ D9-3'
def test_a_library_is_seeded_once_and_the_timeline_folds_to_the_tables(tmp_path):
    store, commit, cfg, seed = _library(tmp_path, max_concurrency=1)
    assert seed["status"] == "ACTIVE" and seed["version_id"] == version_id(resolve_params(cfg))
    # another configuration does not replace the ACTIVE version
    other = resolve_params(OrchestratorConfig(evidence_root=Path(tmp_path) / "e", max_concurrency=2))
    assert commit.seed_policy(other)["version_id"] == seed["version_id"]
    assert [v["version_id"] for v in store.list_policy_versions()] == [seed["version_id"]]
    assert [a["action"] for a in store.list_policy_activations()] == ["seed"]
    assert [e.type for e in store.iter_events(DEPLOYMENT_TIMELINE)] == ["PolicySeeded"]
    assert registry_consistency(store) == []
    store.close()


def test_a_changed_configuration_is_recorded_as_drift_and_replaces_nothing(tmp_path):
    store, commit, _cfg, seed = _library(tmp_path, max_concurrency=1)
    other = resolve_params(OrchestratorConfig(evidence_root=Path(tmp_path) / "e", max_concurrency=2))
    differences = diff_params(seed["params"], other)
    assert differences == [{"key": "mission_concurrency", "a": 1, "b": 2}]
    commit.record_policy_drift(config_hash="h-2", differences=differences)
    [_, drift] = list(store.iter_events(DEPLOYMENT_TIMELINE))
    assert drift.type == "PolicyConfigDrift"
    assert drift.payload["active_version_id"] == seed["version_id"]
    assert drift.payload["differences"] == differences
    assert store.active_policy()["version_id"] == seed["version_id"]
    assert registry_consistency(store) == []  # drift is said, never applied
    store.close()


# ------------------------------------------------------------------ D9-4'
def test_no_decision_point_reads_a_whitelisted_value_from_the_configuration():
    """Plan D9-4' (review P1-1): the decision points go through the Mission's bound
    policy — structurally, no whitelisted item is read from ``self._config`` any more."""

    source = Path(event_handler.__file__).read_text(encoding="utf-8")
    pattern = re.compile(
        r"self\._config\.(candidates_per_task|exploration_slots|aging_window_seconds)\b"
    )
    assert pattern.findall(source) == []
    assert "self._model_router.route(" not in source  # routing goes through the Mission's router
    for constant in ("PLANNER.prompt_version", "CRITIC.prompt_version"):
        assert constant not in source
    # Every prompt selection goes through the Mission-bound template.
    template_role = "role = self._template(role_for_task(task), mission.id)"
    assert source.count(template_role) == 1
    assert source.count("role_for_task(") == source.count(template_role)
    # The shared domain selector must still receive this Mission's frozen policy.
    selector = inspect.getsource(Orchestrator._template)
    assert "return template_for_domain(" in selector
    assert 'self.policy_for(mission_id)["prompt_versions"]' in selector


def test_review_p2_6_a_profile_nobody_labelled_is_not_taken_for_fixtures():
    from agent_orchestrator.orchestrator.event_handler import _provider_kind
    from agent_orchestrator.runtime.model_router import RuntimeProfile

    class SomeVendorProvider:  # not a fixture class, and nobody set provider_kind
        async def invoke(self, request, *, cancel):  # pragma: no cover - never called
            raise NotImplementedError

    fixture = RoleScriptedProvider({})
    unlabelled = {"default": RuntimeProfile("default", SomeVendorProvider(), "vendor-model")}
    assert _provider_kind(None, unlabelled, "default") == "unknown"
    mixed = {**unlabelled, "small": RuntimeProfile("small", fixture, "m")}
    assert _provider_kind(None, mixed, "small") == "unknown"  # one non-fixture profile is enough
    assert (
        _provider_kind(None, {"small": RuntimeProfile("small", fixture, "m")}, "small")
        == "fixtures"
    )


# ------------------------------------------------------------------ the reading door
def test_the_policy_api_and_the_cli_only_read(tmp_path, capsys):
    store, commit, _cfg, seed = _library(tmp_path)
    api = PolicyApi(commit, Principal("alice"))
    public = {name for name in vars(PolicyApi) if not name.startswith("_")}
    assert public == {"list", "show", "status"}
    assert api.list() == {
        "active_version_id": seed["version_id"],
        "versions": [{"version_id": seed["version_id"], "status": "ACTIVE", "source": "seed"}],
    }
    assert api.show(seed["version_id"])["params"] == seed["params"]
    with pytest.raises(PolicyRequestError, match="no policy version"):
        api.show("no-such-thing")
    assert api.status()["active_version_id"] == seed["version_id"]
    events = len(list(store.iter_events(DEPLOYMENT_TIMELINE)))
    store.close()

    root = str(tmp_path)
    assert main(["policy", "list", "--evidence-dir", root]) == 0
    assert json.loads(capsys.readouterr().out)["active_version_id"] == seed["version_id"]
    assert main(["policy", "status", "--evidence-dir", root]) == 0
    assert json.loads(capsys.readouterr().out)["consistency"] == []
    assert main(["policy", "show", seed["version_id"], "--evidence-dir", root]) == 0
    capsys.readouterr()
    assert main(["policy", "show", "no-such-thing", "--evidence-dir", root]) == 1
    capsys.readouterr()
    for verb in ("propose", "approve", "reject", "promote", "rollback", "evaluate"):
        with pytest.raises(SystemExit):  # not a command
            main(["policy", verb, "--evidence-dir", root])
        capsys.readouterr()
    reopened = Store.open_readonly(Path(tmp_path) / "orchestrator.db")
    assert len(list(reopened.iter_events(DEPLOYMENT_TIMELINE))) == events  # reading wrote nothing
    reopened.close()


def test_review_p2_4_a_missing_library_is_an_error_and_is_never_created(tmp_path, capsys):
    nowhere = Path(tmp_path) / "nowhere"
    for verb in ("list", "status"):
        assert main(["policy", verb, "--evidence-dir", str(nowhere)]) == 2
        assert "no orchestrator library" in json.loads(capsys.readouterr().out)["error"]
    assert not (nowhere / "orchestrator.db").exists()  # no empty library created by accident
