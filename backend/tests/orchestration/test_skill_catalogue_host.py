# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""NEXT-TG-1.0 §11: every native pool shares one Skill catalogue authority.

Seam tests through the control channel with the real service and real native pools
(certified fixture counter).  One local bundle is installed once and every pool shows the
same revision; the same install again is the same entry; a member pool takes no Skill
write of its own (the runtime-plane request is routed to the shared catalogue); retiring
it retires it in every pool; a restart rebuilds the same view; and only the listed Skill
verbs are open on the control channel.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from deskpet.orchestration.handlers import handle
from agent_orchestrator.deployment.native_pools import catalogue_owner_profile_id

from .test_native_plane_host import _service

MAIN = Path(__file__).parents[2] / "main.py"


def _bundle(path: Path, name: str) -> Path:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", f"---\nname: {name}\ndescription: 一个说明技能。\n---\n# {name}\n\n按步骤做。\n")
    path.write_bytes(buffer.getvalue())
    return path


async def _call(service, verb: str, payload):  # type: ignore[no-untyped-def]
    return (await handle(service, verb, payload))["payload"]


@pytest.mark.asyncio
async def test_one_install_every_pool_same_revision_and_one_retirement(orchestration_root, principal, tmp_path):
    service = _service(orchestration_root, principal)
    await service.start()
    try:
        native = service.status()["native_plane"]
        owner = catalogue_owner_profile_id()
        assert native["skill_catalogue_owner"] == owner
        roles = {p["profile_id"]: p["catalogue_role"] for p in native["profiles"]}
        assert roles[owner] == "owner" and set(roles.values()) == {"owner", "member"}

        bundle = _bundle(tmp_path / "notes.zip", "notes-skill")
        installed = await _call(service, "orchestration_skill_install_file", {"path": str(bundle)})
        assert installed["ok"] is True, installed
        [skill] = installed["data"]["catalogue"]["skills"]
        assert skill["state"] == "QUARANTINED" and set(skill["pools"]) == set(roles)
        assert all(pool["state"] == "QUARANTINED" and not pool["usable"] for pool in skill["pools"].values())
        # every pool holds exactly the owner's revision (same skill / revision / hash)
        from simple_harness.agents.arp import catalogue as cat
        for profile_id in roles:
            arp = service._orchestrator.assembled.pool(profile_id).runtime.arp
            row = cat.read_revision(arp.catalogue.connection, arp.catalogue.namespace_id, "SKILL", "notes-skill", 1)
            assert row is not None and row.pin.to_json() == skill["skill_ref"]

        again = await _call(service, "orchestration_skill_install_file", {"path": str(bundle)})
        assert again["ok"] is True and again["data"]["catalogue"]["skills"] == installed["data"]["catalogue"]["skills"]

        # a request naming a member pool still goes to the one authority
        member = next(p for p, role in roles.items() if role == "member")
        listed = await _call(service, "agent_runtime_request", {
            "profile_id": member, "schema_version": 1, "verb": "agent_skills_list", "command_id": None, "subject_id": "-",
            "expected_revision": None, "cursor": None, "limit": 10, "payload_ref": None,
            "payload": {"namespace_id": skill_namespace(service), "kind": "SKILL", "cursor": None, "limit": 10}})
        assert listed["ok"] is True and listed["data"]["error"] is None
        assert listed["data"]["items"][0]["items"][0]["definition_ref"] == skill["skill_ref"]
        wrong = await _call(service, "agent_skill_request", {"schema_version": 1, "verb": "agent_session_destroy"})
        assert wrong["ok"] is False and wrong["error_code"] == "invalid_request"

        # a suspension of a never-admitted revision is refused by name; retiring works everywhere
        refused = await _call(service, "orchestration_skill_lifecycle", {"skill_ref": skill["skill_ref"], "action": "SUSPEND", "command_id": "s1"})
        assert refused["ok"] is True and refused["data"]["response"]["error"]["code"] == "STATE_COMBINATION_INVALID"
        retired = await _call(service, "orchestration_skill_lifecycle", {"skill_ref": skill["skill_ref"], "action": "RETIRE", "command_id": "r1"})
        assert retired["ok"] is True and retired["data"]["response"]["error"] is None
        [after] = retired["data"]["catalogue"]["skills"]
        assert after["state"] == "RETIRED" and all(pool["state"] == "RETIRED" for pool in after["pools"].values())
        bad = await _call(service, "orchestration_skill_install_file", {"path": "relative.zip"})
        assert bad["ok"] is False and bad["error_code"] == "invalid_request"
    finally:
        await service.close()

    # a restart rebuilds the same view
    again_service = _service(orchestration_root, principal)
    await again_service.start()
    try:
        view = await _call(again_service, "orchestration_skill_catalogue", {})
        assert view["ok"] is True
        [kept] = view["data"]["skills"]
        assert kept["skill_ref"] == skill["skill_ref"] and all(pool["state"] == "RETIRED" for pool in kept["pools"].values())
    finally:
        await again_service.close()


def skill_namespace(service) -> str:  # type: ignore[no-untyped-def]
    return service._native.shared_catalogue.overview()["namespace_id"]


def test_only_the_listed_skill_verbs_are_open_on_the_control_channel():
    source = MAIN.read_text(encoding="utf-8")
    branch = source[source.index('elif msg_type.startswith(("mission_", "orchestration_", "taskgraph.")) or msg_type in ('):]
    head = branch[: branch.index("):")]
    assert '"agent_skill_request", "agent_skill_evaluation_mission", "agent_skill_evaluation_dispatch"' in head
    assert "agent_runtime_request" not in head and "agent_session" not in head
