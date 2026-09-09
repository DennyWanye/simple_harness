# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Incident AA: a bound context-use whose recall authority moved must re-collect.

HM-TO-A6 attempt 10, turn 18 (evidence
``.local-test-evidence/2026-09-09/native-a6-run10/primary-ui-*/``): the turn
bound ``recall-result:85f74417117792d4`` at 00:53:21.347 with a 60-second
authority lease, was authorized eleven times (00:53:21.769 … 00:54:16.242), and
the twelfth provider request of the same turn — the model was still reading
README/STATUS with ``run_shell`` — reached the fence at 00:54:26.309, **4.96
seconds after the lease expired**.  Every bound source was still at revision 1
with an unchanged content hash and the authority epoch was 9 at both ends, so
nothing about the *values* had changed; the Run was killed by a clock.

Reproduced here against the **real installed Memory SDK** and the real Host
components, with the Host's own semantic clock advanced past the lease:

* the SDK fence really refuses the bound result at that moment (the incident);
* the Host re-collects the same recall under its own idempotency purpose,
  re-binds, and the same fence then issues a receipt (the fix);
* the disclosed bytes never move — a bound source that comes back changed or
  does not come back at all fails closed with a stable code;
* the re-collection is bounded, audited, idempotent and replayable.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
import pytest_asyncio

import simple_harness as h
import simple_harness_memory as m
from deskpet.memory import recall_authority as recall_authority_module
from deskpet.memory.human_memory_v7 import project_recall_fragments
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.recall_authority import (
    CONTEXT_USE_RECOLLECT_PURPOSE, CONTEXT_USE_RECOLLECTED,
    RecallContextUseAuthorityStale, RecallContextUseSourceSuperseded,
)
from deskpet.memory.context_use_recollect_schema import (
    initialize_context_use_recollect_state_db,
)
from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
from deskpet.sdk_adapters.typed_context_use import (
    AdmittedRecallContext, ProductTypedContextUseAuthority,
)

RUN = "product-sdk-incident-aa"
EFFECT = "effect-incident-aa-turn-18"
QUERY = "quartznebula"
#: The Host recall context lease (``RecallContext.expires_at = now + 60``).
LEASE = 60.0


class _Clock:
    """The Host semantic clock, advanced explicitly (never process-global time)."""

    def __init__(self, value: float) -> None:
        self.value = float(value)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> float:
        self.value += float(seconds)
        return self.value


def _disclosure(run_id: str, subject: str):
    """The exact Host-authored disclosure identity ``typed_recall`` would build."""

    return h.DisclosureContext(
        run_id, subject, h.DeliveryRecipient.USER_SELF, subject,
        h.IntendedAudience.USER_SELF, h.DisclosurePurpose.TASK_EXECUTION,
        h.DisclosureSource.AUTHENTICATED_HOST, h.DisclosureTrust.TRUSTED_AUTHORITY,
        h.DisclosureGeneration.CURRENT, "host:validated-control-channel:v1",
        (h.DisclosureReasonCode.MINIMUM_NECESSARY,),
    )


def _recollections(state_db):
    with sqlite3.connect(state_db) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            "SELECT * FROM context_use_recollections ORDER BY generation")]


def _use_request(fragments, *, subject, run_id, turn_id, attempt_id, now):
    """The request the Harness builds for one intent, from these exact fragments."""

    intent = h.RecallContextUseIntentV1(tuple(fragments), ((1, "0" * 64),))
    return intent.request(SimpleNamespace(
        subject=subject, run_id=run_id, turn_id=turn_id,
        provider_attempt_id=attempt_id, requested_at=now,
    ))


class _World:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    async def authorize(self, fragments, *, attempt_id):
        """Run the real Memory use fence against these exact bindings."""

        manager = await self.memory.manager()
        return await manager.authorize_recall_context_use(
            principal=self.memory.principal(),
            request=_use_request(fragments, subject=self.subject, run_id=RUN,
                                 turn_id=self.admitted.turn_id, attempt_id=attempt_id,
                                 now=self.clock()),
            now=self.clock(),
        )


@pytest_asyncio.fixture
async def world(tmp_path):
    """A real bound typed recall: seeded memories, real carrier, real fragments."""

    from tests.sdk_adapters.test_typed_context_use_primary import seed_two

    clock = _Clock(1_700_000_000.0)
    state_db = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(state_db)
    await initialize_context_use_recollect_state_db(state_db)
    memory = compose_human_memory_runtime(state_db, tmp_path / "memory.db",
                                          adapter_factory=lambda _: None, clock=clock)
    try:
        subject = memory.principal().actor_id
        ids, payloads = await seed_two(state_db, memory)
        admitted = AdmittedRecallContext(
            RUN, subject, "turn-1",
            h.EvidenceRef("chat-turn:incident-aa", "a" * 64, 1),
            _disclosure(RUN, subject), "provider-request-parent",
        )
        authority = object.__new__(ProductTypedContextUseAuthority)
        authority._memory = memory
        authority.clock = clock
        authority.subject = subject
        authority._ledger = ContextRouteLedgerStore(state_db, clock=clock)
        authority._fault_sink = None
        lanes = await memory.typed_recall(
            query=QUERY, run_id=RUN, turn_ordinal=1, memory_types=("semantic",),
            include_short_horizon=False, admitted_context=admitted,
        )
        projected = project_recall_fragments(lanes)
        carrier = await authority.build_carrier(
            execution=lanes, projected=projected, admitted=admitted, effect_id=EFFECT,
            recall_plan=dict(query=QUERY, memory_types=("semantic",),
                             include_short_horizon=False, turn_ordinal=1),
        )
        fragments = tuple(h.ContextFragmentV2.from_json(item) for item in carrier["fragments"])
        plan = dict(carrier["recollect"], admitted=admitted)
        yield _World(
            clock=clock, state_db=state_db, memory=memory, subject=subject, ids=ids,
            payloads=payloads, admitted=admitted, authority=authority, carrier=carrier,
            fragments=fragments, plan=plan, bound=lanes.result,
            occurrences=((EFFECT, fragments, (1, "0" * 64), plan),),
        )
    finally:
        await memory.close()


@pytest.mark.asyncio
async def test_bound_lease_is_real_and_the_fence_really_refuses_it(world):
    """The incident itself: nothing changed but the clock, and the fence refuses."""

    assert len(world.fragments) == 2
    assert world.carrier["recollect"]["authority_expires_at"] == pytest.approx(
        world.clock() + LEASE)
    # Inside the lease the bound binding authorizes normally: the fence is not
    # what is wrong, the Run simply outlived a 60-second lease.
    receipt = await world.authorize(world.fragments, attempt_id="attempt-inside-lease")
    assert receipt.result_id == world.bound.result_id
    world.clock.advance(LEASE + 5.0)  # T18: 4.96s past expiry, 12th invocation
    with pytest.raises(m.MemoryValidationError) as raised:
        await world.authorize(world.fragments, attempt_id="attempt-past-lease")
    assert str(raised.value) == "RECALL_AUTHORITY_STALE"
    # …and through the Host authority that is what killed the Run: a definite
    # driver failure at ``dispatch._authorize_context_use``.  The fence itself
    # is unchanged and still fails closed; what the fix adds is the bounded
    # re-collect one step earlier, so a live turn never reaches it.
    request = _use_request(world.fragments, subject=world.subject, run_id=RUN,
                           turn_id=world.admitted.turn_id, attempt_id="attempt-host-fence",
                           now=world.clock())
    with pytest.raises(RecallContextUseAuthorityStale) as host:
        await world.authority.authorize_recall_context_use(request)
    assert host.value.code == "recall_context_use_authority_stale"


@pytest.mark.asyncio
async def test_expired_lease_is_recollected_rebound_and_authorized(world):
    """Green: the same turn survives, bound to a recall Memory authorizes now."""

    world.clock.advance(LEASE + 5.0)
    current = await world.authority._current_occurrences(world.occurrences, run_id=RUN)
    (_effect, rebound, _message, _plan), = current
    # The disclosed bytes never move; only the authority binding does.
    for before, after in zip(world.fragments, rebound, strict=True):
        assert after.fragment_id == before.fragment_id
        assert after.public_payload == before.public_payload
        assert after.public_payload_hash == before.public_payload_hash
        assert after.source_ref == before.source_ref
        assert after.source_revision == before.source_revision
        assert after.recall_binding.result_id != before.recall_binding.result_id
        assert after.recall_binding.public_payload_hash == before.public_payload_hash
    receipt = await world.authorize(rebound, attempt_id="attempt-after-recollect")
    assert receipt.result_id == rebound[0].recall_binding.result_id
    assert receipt.authorized_at < receipt.expires_at
    assert receipt.expires_at > world.clock()
    # One immutable Host re-collection receipt, keyed to the bound result.
    row, = _recollections(world.state_db)
    assert row["generation"] == 1 and row["reason_code"] == CONTEXT_USE_RECOLLECTED
    assert row["bound_result_id"] == world.bound.result_id
    assert row["result_id"] == receipt.result_id != world.bound.result_id
    assert row["expires_at"] > world.clock()
    # A separate durable SDK request, on its own idempotency purpose.
    with sqlite3.connect(world.memory._db_path) as db:
        keys = [key for (key,) in db.execute(
            "SELECT idempotency_key FROM typed_recall_requests ORDER BY created_at")]
    assert keys == [f"context-route:{RUN}:1",
                    f"{CONTEXT_USE_RECOLLECT_PURPOSE}:{RUN}:1:{EFFECT}:1"]


@pytest.mark.asyncio
async def test_recollection_is_reused_while_its_own_lease_holds(world):
    """A second provider request inside the new lease re-collects nothing."""

    world.clock.advance(LEASE + 5.0)
    first = await world.authority._current_occurrences(world.occurrences, run_id=RUN)
    world.clock.advance(5.0)
    second = await world.authority._current_occurrences(world.occurrences, run_id=RUN)
    assert [f.recall_binding.to_json() for f in second[0][1]] == [
        f.recall_binding.to_json() for f in first[0][1]]
    assert len(_recollections(world.state_db)) == 1


@pytest.mark.asyncio
async def test_untouched_binding_is_never_recollected(world):
    """No lease pressure, no re-collection: the unaffected path is byte-identical."""

    current = await world.authority._current_occurrences(world.occurrences, run_id=RUN)
    assert current == world.occurrences
    assert _recollections(world.state_db) == []


@pytest.mark.asyncio
async def test_superseded_source_still_fails_closed_with_a_stable_code(world):
    """Negative: a genuinely invalidated source is never re-bound."""

    manager = await world.memory.manager()
    await manager.suppress(principal=world.memory.principal(), request=m.SuppressionRequest(
        "incident-aa-forget", world.subject, m.SuppressionScopeKind.MEMORY,
        world.ids[0], "user_forget", world.clock(), purpose=None))
    world.clock.advance(LEASE + 5.0)
    with pytest.raises(RecallContextUseSourceSuperseded) as raised:
        await world.authority._current_occurrences(world.occurrences, run_id=RUN)
    assert raised.value.code == "recall_context_use_source_superseded"
    # Fail closed means no receipt at all: nothing was re-authorized.
    assert _recollections(world.state_db) == []
    # And the bound binding is still refused by the real fence.
    with pytest.raises(m.MemoryValidationError):
        await world.authorize(world.fragments, attempt_id="attempt-superseded")


@pytest.mark.asyncio
async def test_recollection_budget_is_bounded(world, monkeypatch):
    """A pathological turn cannot re-collect forever; it settles on a stable code."""

    monkeypatch.setattr(recall_authority_module, "MAX_CONTEXT_USE_RECOLLECTS", 1)
    world.clock.advance(LEASE + 5.0)
    await world.authority._current_occurrences(world.occurrences, run_id=RUN)
    world.clock.advance(LEASE + 5.0)
    with pytest.raises(RecallContextUseAuthorityStale) as raised:
        await world.authority._current_occurrences(world.occurrences, run_id=RUN)
    assert raised.value.code == "recall_context_use_authority_stale"
    assert len(_recollections(world.state_db)) == 1


@pytest.mark.asyncio
async def test_replay_uses_the_receipt_the_grant_was_issued_against(world):
    """Determinism: the consumed-use read re-derives, never re-collects."""

    world.clock.advance(LEASE + 5.0)
    (_effect, rebound, _message, _plan), = await world.authority._current_occurrences(
        world.occurrences, run_id=RUN)
    granted = frozenset({(rebound[0].recall_binding.result_id,
                          rebound[0].recall_binding.result_hash)})
    replayed = await world.authority._replayed_occurrences(
        world.occurrences, run_id=RUN, granted=granted)
    assert [f.recall_binding.to_json() for f in replayed[0][1]] == [
        f.recall_binding.to_json() for f in rebound]
    # A grant issued against the original binding replays the original binding.
    original = frozenset({(world.bound.result_id, world.bound.result_hash)})
    kept = await world.authority._replayed_occurrences(
        world.occurrences, run_id=RUN, granted=original)
    assert kept == world.occurrences
    # An identity no Host receipt covers is never invented.
    with pytest.raises(ValueError, match="typed_use_recollection_receipt_missing"):
        await world.authority._replayed_occurrences(
            world.occurrences, run_id=RUN,
            granted=frozenset({("recall-result:unknown", "f" * 64)}))
    assert len(_recollections(world.state_db)) == 1
