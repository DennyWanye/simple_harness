# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""NEXT-TG-1.0 §11: the Skill admission evaluation can be walked from the settings page.

Real service, real native pools (certified fixture counter).  "Evaluate" starts the trial
on the catalogue owner (every pool follows), creates the original evaluation Mission with
the evaluation's own key and links the evaluation to that Mission's root task at once, so
the Mission's Worker may use the Skill in TRIAL from its first call.  Repeating it does
not start a second one; admission is refused until the Mission's root acceptance carries a
USABLE certificate; a cancelled evaluation can be started again.  The success path from a
passed evaluation to admission runs on the real model (no certificate is fabricated here).
"""

from __future__ import annotations

import pytest

from deskpet.orchestration.handlers import handle
from simple_harness.agents.arp.assurance_acceptance import evaluation_mission_key
from simple_harness.agents.arp.pins import Pin

from .test_native_plane_host import _service
from .test_skill_catalogue_host import _bundle


async def _call(service, verb: str, payload):  # type: ignore[no-untyped-def]
    return (await handle(service, verb, payload))["payload"]


@pytest.mark.asyncio
async def test_evaluate_starts_the_trial_mission_and_link_and_admission_waits_for_its_acceptance(orchestration_root, principal, tmp_path):
    service = _service(orchestration_root, principal)
    await service.start()
    try:
        installed = await _call(service, "orchestration_skill_install_file", {"path": str(_bundle(tmp_path / "notes.zip", "notes-skill"))})
        [skill] = installed["data"]["catalogue"]["skills"]
        assert skill["state"] == "QUARANTINED" and skill["evaluation"] is None

        started = await _call(service, "orchestration_skill_evaluate", {"skill_ref": skill["skill_ref"], "command_id": "eval-1"})
        assert started["ok"] is True, started
        evaluation = started["data"]["evaluation"]
        mission_id = evaluation["mission_id"]
        assert evaluation["passed"] is False and evaluation["expired"] is False
        [row] = started["data"]["catalogue"]["skills"]
        assert row["state"] == "TRIAL" and all(pool["state"] == "TRIAL" for pool in row["pools"].values())
        assert row["evaluation"]["mission_id"] == mission_id

        # the original evaluation Mission: the evaluation's own key, a content report
        owner = service._orchestrator.assembled.pool(service._native.catalogue_owner_id).runtime
        from simple_harness.agents.arp import catalogue as cat
        revision = cat.resolve_pin(owner.arp.catalogue.connection, owner.arp.catalogue.namespace_id, Pin.from_json(skill["skill_ref"]))
        binding = owner.arp.lifecycle.binding_for(revision)
        mission = service._orchestrator.store.get_mission(mission_id)
        assert mission.idempotency_key == evaluation_mission_key(Pin.from_json(binding["evaluation_ref"]))
        assert "notes-skill" in mission.goal and "skill-trial.md" in mission.goal
        assert "file:skill-trial.md" in mission.success_criteria
        # linked at once to the root task, so its Worker may use the Skill in TRIAL
        dispatch = owner.arp.lifecycle.dispatch_for(Pin.from_json(binding["evaluation_ref"]))
        assert dispatch["mission_id"] == mission_id and dispatch["task_id"] == "desktop-root-" + mission_id
        assert service._native.shared_catalogue.trial_mission_for(Pin.from_json(skill["skill_ref"])) == mission_id

        # repeating does not start a second evaluation
        again = await _call(service, "orchestration_skill_evaluate", {"skill_ref": skill["skill_ref"], "command_id": "eval-2"})
        assert again["ok"] is True and again["data"]["evaluation"]["mission_id"] == mission_id
        assert sum(1 for m in service._orchestrator.store.list_missions() if m.idempotency_key.startswith("skill-eval:")) == 1

        # no acceptance yet: admission is refused by name
        refused = await _call(service, "orchestration_skill_admit", {"skill_ref": skill["skill_ref"], "command_id": "admit-1"})
        assert refused["ok"] is False and "还没有通过" in refused["error"]

        # a cancelled evaluation can be started again: a fresh trial, a new Mission
        service.cancel_mission(mission_id)
        restarted = await _call(service, "orchestration_skill_evaluate", {"skill_ref": skill["skill_ref"], "command_id": "eval-3"})
        assert restarted["ok"] is True, restarted
        assert restarted["data"]["evaluation"]["mission_id"] not in (None, mission_id)
        assert restarted["data"]["catalogue"]["skills"][0]["state"] == "TRIAL"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_evaluate_refuses_unknown_and_malformed_skill_refs(orchestration_root, principal, tmp_path):
    service = _service(orchestration_root, principal)
    await service.start()
    try:
        refused = await _call(service, "orchestration_skill_evaluate", {"skill_ref": {"kind": "skill", "id": "missing", "revision": 1, "content_hash": "0" * 64}, "command_id": "e"})
        assert refused["ok"] is False and refused["error_code"] == "not_found"
        empty = await _call(service, "orchestration_skill_evaluate", {"skill_ref": None, "command_id": "e"})
        assert empty["ok"] is False and empty["error_code"] == "invalid_request"
    finally:
        await service.close()
