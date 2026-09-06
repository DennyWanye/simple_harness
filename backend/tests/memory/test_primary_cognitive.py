"""Real Host S1/public Memory leaf proof. Main callback/WS integration is separate."""

import asyncio
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest
import simple_harness_memory as m
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import (
    build_foreground_turn_evidence,
    build_host_typed_evidence,
)
from deskpet.memory.primary_cognitive import (
    CognitiveControlError,
    PrimaryCognitiveControls,
)
from deskpet.memory.primary_cognitive_evidence import CognitiveActionEvidenceStore
from tests.memory.test_primary_read_api import AUTH, setup
from tests.memory.test_primary_visibility import (
    FILTERS,
    classification_policy,
    materialize,
    real_recall,
)


class Actions(CognitiveActionEvidenceStore):
    """Real main-owned callback, plus a request authorization fence for the leaf."""

    def __init__(self, f):
        super().__init__(f.path, auth=AUTH)
        self.f = f
        self.enabled = True

    async def authorize(self, *, primary_ref):
        if not self.enabled or primary_ref != self.f.primary:
            raise PermissionError("not_current_owner")

    async def find(self, **kwargs):
        return await self.find_action(**kwargs)

    async def admit(self, **kwargs):
        return await self.admit_action(**kwargs)


async def prepared(tmp_path):
    f = await setup(tmp_path)
    actions = Actions(f)
    principal = m.MemoryPrincipal("host", "household", AUTH.subject, "cognitive-ui")
    kwargs = {
        "classification_policy": classification_policy(),
        "supported_filter_policies": FILTERS,
        "evidence_authority": HostEvidenceAuthority(f.path),
        "clock": lambda: 4_000_000_000.0,
    }
    manager = await m.build_human_memory_v7(tmp_path / "memory.db", **kwargs)
    env, receipt = build_foreground_turn_evidence(
        subject=AUTH.subject,
        authority_ref=AUTH.authority_ref,
        delivery_key="actual-user",
        text="我偏好简洁回答",
    )
    await HumanMemoryProgramStore(f.path).append_evidence(env, receipt)
    await manager.ingest_committed_evidence(env, receipt)
    memory_id = await materialize(manager, principal, env, receipt)

    def leaf(current=manager):
        return PrimaryCognitiveControls(
            manager=current,
            principal=principal,
            authorize_primary=actions.authorize,
            find_action=actions.find,
            admit_action=actions.admit,
        )

    item = (await leaf().list(primary_ref=f.primary))["items"][0]
    request = {
        "primary_ref": f.primary,
        "action_id": "explicit-action",
        "memory_id": memory_id,
        "expected_revision": item["revision"],
        "expected_content_hash": item["content_hash"],
    }
    return SimpleNamespace(
        f=f,
        actions=actions,
        manager=manager,
        kwargs=kwargs,
        leaf=leaf,
        request=request,
        env=env,
    )


@pytest.mark.asyncio
async def test_public_forget_real_s1_zero_time_reopen_and_exact_replay(
    tmp_path, monkeypatch
):
    h = await prepared(tmp_path)
    authority = HostEvidenceAuthority(h.f.path)
    original = await authority.read_admitted(h.env.evidence_id)
    try:
        with monkeypatch.context() as clock:
            clock.setattr("deskpet.memory.human_memory_program.time.time", lambda: 0.0)
            first = await h.leaf().forget(**h.request)
        assert first["status"] == "applied" and first["view"]["items"] == []
        stored = await authority.read_analysis_item(first["evidence_ref"])
        assert stored.occurred_at == 0.0
        await h.manager.close()
        reopened = await m.build_human_memory_v7(tmp_path / "memory.db", **h.kwargs)
        try:
            replay = await h.leaf(reopened).forget(**h.request)
            assert replay == first
            assert await authority.read_admitted(h.env.evidence_id) == original
            with sqlite3.connect(h.f.path) as db:
                assert (
                    db.execute(
                        "SELECT COUNT(*) FROM human_memory_evidence WHERE source_ref LIKE 'host-cognitive-action/%'"
                    ).fetchone()[0]
                    == 1
                )
        finally:
            await reopened.close()
    finally:
        await h.manager.close()


@pytest.mark.asyncio
async def test_unknown_after_real_sdk_commit_no_false_success_retry_same_receipt(
    tmp_path,
):
    h = await prepared(tmp_path)
    requests = []
    decisions = []

    class LostAck:
        get_twin_graph_view = h.manager.get_twin_graph_view

        async def suppress(self, **kwargs):
            requests.append(kwargs["request"])
            decisions.append(await h.manager.suppress(**kwargs))
            raise TimeoutError("ACK unknown")

    class Retry:
        get_twin_graph_view = h.manager.get_twin_graph_view

        async def suppress(self, **kwargs):
            requests.append(kwargs["request"])
            return await h.manager.suppress(**kwargs)

    try:
        with pytest.raises(TimeoutError):
            await h.leaf(LostAck()).forget(**h.request)
        retry = await h.leaf(Retry()).forget(**h.request)
        assert retry["status"] == "applied" and retry["view"]["items"] == []
        assert requests[0].requested_at > 0 and requests[0].purpose is None
        assert requests[0].request_id == f"primary-forget:{retry['evidence_ref']}"
        assert requests[0].request_hash == requests[1].request_hash
        assert retry["decision_hash"] == decisions[0].decision_hash
    finally:
        await h.manager.close()


@pytest.mark.asyncio
async def test_concurrent_same_action_converges_conflicting_retry_rejected(tmp_path):
    h = await prepared(tmp_path)
    try:
        a, b = await asyncio.gather(
            h.leaf().forget(**h.request), h.leaf().forget(**h.request)
        )
        assert (
            a["directive_ref"] == b["directive_ref"]
            and a["decision_hash"] == b["decision_hash"]
        )
        with pytest.raises(ValueError, match="primary_memory_action_conflict"):
            await h.leaf().forget(**{**h.request, "memory_id": "other-canonical-id"})
    finally:
        await h.manager.close()


@pytest.mark.asyncio
async def test_concurrent_admission_after_find_none_before_view_can_replay(tmp_path):
    h = await prepared(tmp_path)

    class Progressed:
        suppress = h.manager.suppress

        async def get_twin_graph_view(self, **kwargs):
            # This caller already read no action; another caller now durably forgets.
            await h.leaf().forget(**h.request)
            return await h.manager.get_twin_graph_view(**kwargs)

    try:
        result = await h.leaf(Progressed()).forget(**h.request)
        assert result["status"] == "applied"
    finally:
        await h.manager.close()


@pytest.mark.asyncio
async def test_applied_suppression_display_failure_does_not_turn_into_unknown(tmp_path):
    h = await prepared(tmp_path)

    class DisplayFailure:
        applied = False

        async def suppress(self, **kwargs):
            result = await h.manager.suppress(**kwargs)
            self.applied = True
            return result

        async def get_twin_graph_view(self, **kwargs):
            if self.applied:
                raise RuntimeError("display unavailable")
            return await h.manager.get_twin_graph_view(**kwargs)

    try:
        result = await h.leaf(DisplayFailure()).forget(**h.request)
        assert (
            result["status"] == "applied"
            and result["view"] is None
            and result["refresh_required"] is True
        )
        assert (await h.leaf().list(primary_ref=h.f.primary))["items"] == []
    finally:
        await h.manager.close()


@pytest.mark.asyncio
async def test_fault_after_host_commit_before_sdk_replays_original_request(tmp_path):
    h = await prepared(tmp_path)
    recorded = []

    class BeforeSDK:
        get_twin_graph_view = h.manager.get_twin_graph_view

        async def suppress(self, **kwargs):
            recorded.append(kwargs["request"])
            raise RuntimeError("fault before SDK write")

    class Retry:
        get_twin_graph_view = h.manager.get_twin_graph_view

        async def suppress(self, **kwargs):
            recorded.append(kwargs["request"])
            return await h.manager.suppress(**kwargs)

    try:
        with pytest.raises(RuntimeError, match="before SDK write"):
            await h.leaf(BeforeSDK()).forget(**h.request)
        assert len((await h.leaf().list(primary_ref=h.f.primary))["items"]) == 1
        assert (await h.leaf(Retry()).forget(**h.request))["status"] == "applied"
        assert recorded[0].request_hash == recorded[1].request_hash
        with sqlite3.connect(h.f.path) as db:
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM human_memory_evidence WHERE source_ref LIKE 'host-cognitive-action/%'"
                ).fetchone()[0]
                == 1
            )
    finally:
        await h.manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["after-view", "after-admission"])
async def test_authorization_fence_preserves_admission_without_unauthorized_sdk_write(
    tmp_path, stage
):
    h = await prepared(tmp_path)
    calls = []

    class Manager:
        async def get_twin_graph_view(self, **kwargs):
            view = await h.manager.get_twin_graph_view(**kwargs)
            if stage == "after-view":
                h.actions.enabled = False
            return view

        async def suppress(self, **kwargs):
            calls.append(kwargs)
            return await h.manager.suppress(**kwargs)

    leaf = h.leaf(Manager())

    async def admitted_then_revoked(**kwargs):
        receipt = await h.actions.admit(**kwargs)
        h.actions.enabled = False
        return receipt

    if stage == "after-admission":
        leaf._append = admitted_then_revoked
    try:
        with pytest.raises(PermissionError):
            await leaf.forget(**h.request)
        assert calls == []
        with sqlite3.connect(h.f.path) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM human_memory_evidence WHERE source_ref LIKE 'host-cognitive-action/%'"
            ).fetchone()[0]
        assert count == (1 if stage == "after-admission" else 0)
        h.actions.enabled = True
        assert (await h.leaf().forget(**h.request))["status"] == "applied"
    finally:
        await h.manager.close()


@pytest.mark.asyncio
async def test_next_public_typed_recall_after_forget_cannot_return_old_memory(tmp_path):
    h = await prepared(tmp_path)
    captured = {}

    class Capture:
        async def execute_typed_recall(self, **kwargs):
            kwargs["context"] = replace(kwargs["context"], expires_at=4_000_001_000.0)
            kwargs["plan"] = replace(
                kwargs["plan"], context_hash=kwargs["context"].context_hash
            )
            captured.update(kwargs)
            return await h.manager.execute_typed_recall(**kwargs)

    try:
        await real_recall(Capture(), h.leaf()._principal, h.env)
        await h.leaf().forget(**h.request)
        original = captured["context"]
        context = replace(
            original,
            run_id="after-forget",
            disclosure_context=replace(
                original.disclosure_context, run_id="after-forget"
            ),
        )
        plan = replace(
            captured["plan"],
            plan_id="after-forget-plan",
            run_id=context.run_id,
            context_hash=context.context_hash,
            disclosure_context=context.disclosure_context,
            idempotency_key="after-forget-key",
        )
        result = await h.manager.execute_typed_recall(
            principal=captured["principal"], context=context, plan=plan
        )
        assert result.result.items == ()
    finally:
        await h.manager.close()


@pytest.mark.asyncio
async def test_generic_primary_append_cannot_authorize_replay(tmp_path):
    h = await prepared(tmp_path)
    body = {
        k: h.request[k]
        for k in ("memory_id", "expected_revision", "expected_content_hash")
    }
    env, receipt = build_host_typed_evidence(
        subject=AUTH.subject,
        authority_ref=AUTH.authority_ref,
        payload=body,
        idempotency_key=h.request["action_id"],
        source_ref="host-primary:" + h.request["action_id"],
    )
    await HumanMemoryProgramStore(h.f.path).append_evidence(env, receipt)
    try:
        assert (
            await h.actions.find(payload=body, idempotency_key=h.request["action_id"])
            is None
        )
        with pytest.raises(CognitiveControlError, match="cognitive_target_stale"):
            await h.leaf().forget(**{**h.request, "expected_revision": 9})
        assert len((await h.leaf().list(primary_ref=h.f.primary))["items"]) == 1
    finally:
        await h.manager.close()


@pytest.mark.asyncio
async def test_receipt_hash_tamper_and_revoked_owner_fail_before_suppression(tmp_path):
    h = await prepared(tmp_path)
    try:

        async def forged(**_):
            return SimpleNamespace(
                evidence_id="fake", committed_at=0, payload_hash="0" * 64
            )

        leaf = h.leaf()
        leaf._read = forged
        with pytest.raises(CognitiveControlError, match="action_binding"):
            await leaf.forget(**h.request)
        h.actions.enabled = False
        with pytest.raises(PermissionError):
            await h.leaf().forget(**h.request)
        h.actions.enabled = True
        assert len((await h.leaf().list(primary_ref=h.f.primary))["items"]) == 1
    finally:
        await h.manager.close()


@pytest.mark.asyncio
async def test_display_output_bounds_and_late_auth_no_fallback():
    node = SimpleNamespace(
        memory_id="m",
        revision=1,
        label="文" * 700,
        status="active",
        can_forget=True,
        content_hash="a" * 64,
    )
    manager = SimpleNamespace()
    enabled = True

    async def authorize(**_):
        if not enabled:
            raise PermissionError("revoked")

    async def graph(**_):
        return SimpleNamespace(
            subject="s",
            nodes=[
                SimpleNamespace(**{**vars(node), "memory_id": f"m{i:02}"})
                for i in range(55)
            ],
        )

    async def unused(**_):
        pytest.fail("action callback on list")

    manager.get_twin_graph_view = graph
    leaf = PrimaryCognitiveControls(
        manager=manager,
        principal=SimpleNamespace(actor_id="s"),
        authorize_primary=authorize,
        find_action=unused,
        admit_action=unused,
    )
    page = await leaf.list(primary_ref="p", limit=50)
    assert len(page["items"]) == 50 and len(page["items"][0]["label"]) == 512
    assert (
        len((await leaf.list(primary_ref="p", cursor=page["next_cursor"]))["items"])
        == 5
    )
    with pytest.raises(CognitiveControlError):
        await leaf.list(primary_ref="p", limit=True)

    async def revoking_graph(**kwargs):
        nonlocal enabled
        enabled = False
        return await graph(**kwargs)

    manager.get_twin_graph_view = revoking_graph
    with pytest.raises(PermissionError):
        await leaf.list(primary_ref="p")
