"""S6 Task 2 backend: task_scope.list, open_exact binding_summary, public live_probe rejection.

Public HUMAN channel over the real routed factory; no UI/native claims.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.human_memory_service import (
    AppendBindingRequest,
    AuthenticatedHostSnapshot,
    HumanMemoryHostServiceFactory,
    OpenTaskScopeRequest,
)
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_route import local_owner_auth

CANDIDATE_FIELDS = {"scope_ref", "source_ref", "source_hash", "title", "goal", "project", "status", "snippet", "rank"}


def _auth(subject: str) -> AuthenticatedHostSnapshot:
    return AuthenticatedHostSnapshot(subject, f"principal-{subject}", f"host:{subject}")


async def _call(factory, auth, operation, request, *, binding_append=None):
    response = await handle_human_memory_command(
        {"type": "human_memory_request", "request_id": f"{operation}:{json.dumps(request, sort_keys=True)}",
         "operation": operation, "request": request},
        factory=factory, auth=auth, binding_append=binding_append,
    )
    return response["payload"]


async def _create(factory, auth, key, title, goal):
    payload = await _call(factory, auth, "task_scope.create", {"fixture_key": key, "title": title, "goal": goal})
    assert payload["ok"], payload
    return payload["result"]["scope_ref"]


@pytest.mark.asyncio
async def test_task_scope_list_is_bounded_ordered_owned_and_archive_free(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    factory = HumanMemoryHostServiceFactory(path, await dispatch_startup_epoch(path, approved_fresh_lane=True))
    alice, bob = _auth("alice"), _auth("bob")
    first = await _create(factory, alice, "k1", "整理照片库", "把 2025 照片按月归档")
    second = await _create(factory, alice, "k2", "整理视频库", "视频归档")
    third = await _create(factory, alice, "k3", "写周报", "周五前完成")
    foreign = await _create(factory, bob, "k1", "Bob 的任务", "secret goal")
    # A later mutation makes the oldest scope the most recent one.
    mutated = await _call(factory, alice, "task_scope.mutate",
                          {"scope_ref": first, "mutation": {"kind": "status", "value": "active"}})
    assert mutated["ok"], mutated
    await factory.bind(alice).rebuild_derived(first)

    listed = await _call(factory, alice, "task_scope.list", {})
    assert listed["ok"], listed
    result = listed["result"]
    assert [item["scope_ref"] for item in result["items"]] == [first, third, second]
    assert result["next_cursor"] is None and len(result["receipt_hash"]) == 64
    assert foreign not in {item["scope_ref"] for item in result["items"]}
    assert len(json.dumps(listed, ensure_ascii=False).encode()) <= 32 * 1024
    for item in result["items"]:
        assert CANDIDATE_FIELDS <= set(item)
        assert item["snippet"] == "" and item["rank"] == 0.0
        assert isinstance(item["updated_at"], float) and item["binding_summary"] is None
        assert not {"resume_package", "read_views", "operations", "events", "content"} & set(item)
    active = result["items"][0]
    assert active["title"] == "整理照片库" and active["goal"] == "把 2025 照片按月归档" and active["status"] == "active"
    updated = [item["updated_at"] for item in result["items"]]
    assert updated == sorted(updated, reverse=True)

    # Bob only sees his own scope and never Alice's titles or goals.
    bobs = await _call(factory, bob, "task_scope.list", {})
    assert bobs["ok"] and [item["scope_ref"] for item in bobs["result"]["items"]] == [foreign]

    # Bounded keyset paging: a page of two, then the remaining one via cursor.
    page = await _call(factory, alice, "task_scope.list", {"limit": 2})
    assert page["ok"] and [i["scope_ref"] for i in page["result"]["items"]] == [first, third]
    assert page["result"]["next_cursor"]
    rest = await _call(factory, alice, "task_scope.list", {"limit": 2, "cursor": page["result"]["next_cursor"]})
    assert rest["ok"] and [i["scope_ref"] for i in rest["result"]["items"]] == [second]
    assert rest["result"]["next_cursor"] is None
    # A cursor minted for Alice is not a listing key for Bob.
    stolen = await _call(factory, bob, "task_scope.list", {"cursor": page["result"]["next_cursor"]})
    assert not stolen["ok"] and stolen["error"]["code"] == "human_memory_evidence_cursor_invalid"
    for bad in ({"limit": 33}, {"limit": 0}, {"limit": "5"}, {"query": "x"}, {"subject": "bob"}):
        rejected = await _call(factory, alice, "task_scope.list", bad)
        assert not rejected["ok"], bad
        assert rejected["error"]["code"] in {"human_memory_request_invalid", "human_memory_public_authority_field_rejected"}

    # The listed source_hash is exactly what a following exact open pins.
    opened = await _call(factory, alice, "task_scope.open_exact",
                         {"scope_ref": active["scope_ref"], "expected_source_hash": active["source_hash"]})
    assert opened["ok"], opened
    assert opened["result"]["source_ref"] == active["source_ref"]
    assert opened["result"]["binding_summary"] is None
    assert opened["result"]["drift_report"] is None and opened["result"]["drift_probe"] == "host_unavailable"


@pytest.mark.asyncio
async def test_public_open_exact_rejects_client_live_probe_but_trusted_path_keeps_it(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    factory = HumanMemoryHostServiceFactory(path, await dispatch_startup_epoch(path, approved_fresh_lane=True))
    alice = _auth("alice")
    scope = await _create(factory, alice, "k1", "整理照片库", "归档")
    for probe in ({"head": "abc"}, {}, None):
        rejected = await _call(factory, alice, "task_scope.open_exact", {"scope_ref": scope, "live_probe": probe})
        assert not rejected["ok"] and rejected["error"]["code"] == "human_memory_request_invalid", probe
    unknown = await _call(factory, alice, "task_scope.open_exact", {"scope_ref": scope, "probe": {"head": "abc"}})
    assert not unknown["ok"] and unknown["error"]["code"] == "human_memory_request_invalid"
    plain = await _call(factory, alice, "task_scope.open_exact", {"scope_ref": scope})
    assert plain["ok"] and plain["result"]["drift_report"] is None
    assert plain["result"]["drift_probe"] == "host_unavailable"
    # Trusted Run path (s4_value_adapter) still supplies its own probe.
    trusted = await factory.bind(alice).open_task_scope(OpenTaskScopeRequest(scope, {"head": "abc"}, None))
    assert trusted["drift_probe"] == "trusted_run"
    assert trusted["drift_report"] is not None and trusted["drift_report"]["drifted"] is True
    assert trusted["drift_report"]["changed_fields"] == ["head"]


@pytest.mark.asyncio
async def test_open_exact_and_list_expose_read_only_binding_summary(tmp_path: Path) -> None:
    from tests.execution.test_primary_create_new_runtime import fixture

    state, factory, service, configured, authority = await fixture(tmp_path, "auto")
    auth = local_owner_auth()
    scope = (await _call(factory, auth, "task_scope.create",
                         {"fixture_key": "bound", "title": "Bound work", "goal": "edit the root"}))["result"]["scope_ref"]
    root = configured / "workspace"
    root.mkdir()
    bound = await factory.bind(auth, binding_append=authority).append_binding(
        AppendBindingRequest(scope, str(root), "root-1"))
    assert bound["status"] == "bound"

    opened = await _call(factory, auth, "task_scope.open_exact", {"scope_ref": scope})
    assert opened["ok"], opened
    result = opened["result"]
    summary = result["binding_summary"]
    assert summary["mode"] == "auto" and summary["state"] == "active" and summary["revision"] == 1
    assert summary["receipt_hash"] == result["resume_package"]["binding_receipt_hash"]
    assert len(summary["roots"]) == 1
    (only,) = summary["roots"]
    assert only["root_path"] == str(root.resolve()) and only["mode"] == "auto" and only["state"] == "active"
    assert only["revision"] == 1 and only["receipt_hash"] == summary["receipt_hash"]
    assert len(only["root_digest"]) == 64
    # Existing fields are untouched; the summary is a sibling, not part of the package hash.
    assert {"scope_ref", "receipt_ref", "source_ref", "source_hash", "resume_package", "resume_sha256",
            "receipt_hash", "drift_report"} <= set(result)
    assert "binding_summary" not in result["resume_package"]
    assert result["drift_report"] is None and result["drift_probe"] == "host_unavailable"

    listed = await _call(factory, auth, "task_scope.list", {})
    assert listed["ok"], listed
    item = next(i for i in listed["result"]["items"] if i["scope_ref"] == scope)
    assert item["binding_summary"] == summary

    # The Host's own re-stat reports a vanished root without granting anything.
    root.rename(configured / "moved-away")
    reopened = await _call(factory, auth, "task_scope.open_exact", {"scope_ref": scope})
    assert reopened["ok"], reopened
    assert reopened["result"]["binding_summary"]["state"] == "missing"
    assert reopened["result"]["binding_summary"]["roots"][0]["state"] == "missing"
    assert reopened["result"]["resume_sha256"] == result["resume_sha256"]
    # Another identity never sees this binding.
    other = await _call(factory, _auth("stranger"), "task_scope.open_exact", {"scope_ref": scope})
    assert not other["ok"] and other["error"]["code"] == "human_memory_permission_denied"
