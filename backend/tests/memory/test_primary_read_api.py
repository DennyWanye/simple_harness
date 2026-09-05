"""Public primary API + real Host SQLite; no provider/server startup."""

import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.human_memory_service import (
    AuthenticatedHostSnapshot,
    HumanMemoryHostServiceFactory,
)
from deskpet.memory.schema import dispatch_startup_epoch
from simple_harness_memory.core.suppression import SuppressionResolution

AUTH = AuthenticatedHostSnapshot("actor-1", "primary-principal", "host:test")


class Policy:
    def __init__(self):
        self.denied = set()
        self.calls = []
        self.history_calls = []

    async def __call__(self, candidate, purpose):
        self.calls.append((candidate, purpose))
        denied = (
            candidate.subject in self.denied or candidate.evidence_id in self.denied
        )
        return SuppressionResolution(denied, ("directive",) if denied else (), 1.0)

    async def history(self, *, subject, disclosure_context, bindings):
        from deskpet.task_scope.protocol import canonical_hash
        from simple_harness_memory import (
            HistoryVisibilityItem,
            HistoryVisibilitySnapshot,
        )
        from simple_harness_memory.core.suppression import (
            OrdinaryMemoryPurpose,
            SuppressionCandidate,
        )

        self.history_calls.append((subject, disclosure_context, bindings))
        items = []
        for binding in bindings:
            envelope = getattr(binding, "envelope", None)
            resolution = await self(
                SuppressionCandidate(
                    subject,
                    evidence_id=None if envelope is None else envelope.evidence_id,
                ),
                OrdinaryMemoryPurpose.READ,
            )
            visible = (
                not resolution.denied
                and getattr(binding, "item_id", None) not in self.denied
            )
            items.append(
                HistoryVisibilityItem(
                    canonical_hash(
                        {
                            "domain": "memory.history.binding.v1",
                            "payload": binding.to_json(),
                        }
                    ),
                    visible,
                    "history_visible" if visible else "history_suppressed",
                )
            )
        return HistoryVisibilitySnapshot(
            subject,
            canonical_hash(disclosure_context.to_json()),
            1.0,
            None,
            1,
            "a" * 64,
            tuple(items),
        )


async def setup(
    tmp_path,
    policy=True,
    reader=None,
    run_binding_reader=None,
    wake=None,
    history_checker=None,
):
    path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    policy = Policy() if policy is True else policy
    factory = HumanMemoryHostServiceFactory(
        path,
        startup,
        suppression_resolver=policy,
        settled_run_reader=reader,
        run_binding_reader=run_binding_reader,
        history_visibility_checker=history_checker or getattr(policy, "history", None),
    )

    async def send(op, body=None, key="request", auth=AUTH):
        return await handle_human_memory_command(
            {
                "type": "human_memory_request",
                "request_id": key,
                "operation": op,
                "request": body or {},
            },
            factory=factory,
            auth=auth,
            scheduler_wake=wake,
        )

    primary = result(await send("primary.open"))["primary_ref"]
    return SimpleNamespace(
        path=path, factory=factory, policy=policy, send=send, primary=primary
    )


def result(response):
    assert response["payload"]["ok"], response
    return response["payload"]["result"]


def error(response, code):
    assert response["payload"]["ok"] is False, response
    assert response["payload"]["error"]["code"] == code


@pytest.mark.asyncio
async def test_queued_text_state_and_keyset_restart_are_public_and_owner_bound(
    tmp_path,
):
    f = await setup(tmp_path)
    for i in range(5):
        result(await f.send("queue.enqueue", {"text": f"message {i}"}, key=f"turn-{i}"))
    state = result(await f.send("primary.state"))
    assert state["primary_ref"] == f.primary
    assert state["queued_count"] == 5 and state["current_run"] is None
    first = result(
        await f.send("primary.messages.page", {"primary_ref": f.primary, "limit": 2})
    )
    assert [i["text"] for i in first["items"]] == ["message 3", "message 4"]
    second = result(
        await f.send(
            "primary.messages.page",
            {"primary_ref": f.primary, "limit": 2, "cursor": first["next_cursor"]},
        )
    )
    assert [i["text"] for i in second["items"]] == ["message 1", "message 2"]
    assert state["revision"] == first["revision"] == second["revision"]
    other = AuthenticatedHostSnapshot("other-owner", "other-principal", "host:other")
    error(
        await f.send("primary.messages.page", {"primary_ref": f.primary}, auth=other),
        "primary_not_found",
    )
    result(await f.send("queue.enqueue", {"text": "new"}, key="new-turn"))
    error(
        await f.send(
            "primary.messages.page",
            {"primary_ref": f.primary, "cursor": first["next_cursor"]},
        ),
        "primary_cursor_stale",
    )


@pytest.mark.asyncio
async def test_long_unicode_detail_is_complete_and_suppression_applies_to_old_ref(
    tmp_path,
):
    f = await setup(tmp_path)
    text = "汉🙂字" * 700
    result(await f.send("queue.enqueue", {"text": text}, key="long"))
    page = result(await f.send("primary.messages.page", {"primary_ref": f.primary}))
    item = page["items"][0]
    assert item["has_more"] and len(item["text"]) == 1024
    pieces, offset = [], 0
    while offset is not None:
        detail = result(
            await f.send(
                "primary.messages.detail",
                {
                    "primary_ref": f.primary,
                    "message_ref": item["message_ref"],
                    "offset": offset,
                    "limit": 700,
                },
            )
        )
        assert len(detail["text"]) <= 700
        pieces.append(detail["text"])
        offset = detail["next_offset"]
    assert "".join(pieces) == text
    evidence = next(c.evidence_id for c, _ in f.policy.calls if c.evidence_id)
    f.policy.denied.add(evidence)
    error(
        await f.send(
            "primary.messages.detail",
            {"primary_ref": f.primary, "message_ref": item["message_ref"]},
        ),
        "primary_message_unavailable",
    )
    assert (
        result(await f.send("primary.messages.page", {"primary_ref": f.primary}))[
            "items"
        ]
        == []
    )
    assert result(await f.send("primary.state"))["queued_count"] == 0


@pytest.mark.asyncio
async def test_policy_absent_failure_and_subject_suppression_never_default_allow(
    tmp_path,
):
    f = await setup(tmp_path, policy=None)
    result(await f.send("queue.enqueue", {"text": "private"}))
    error(await f.send("primary.state"), "primary_read_policy_unavailable")
    error(
        await f.send("primary.messages.page", {"primary_ref": f.primary}),
        "primary_read_policy_unavailable",
    )
    f = await setup(tmp_path / "other")
    f.policy.denied.add(AUTH.subject)
    error(await f.send("primary.state"), "primary_read_suppressed")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"expected_run_ref": "run"},
        {"expected_generation": 1},
        {"expected_run_ref": "run", "expected_generation": True},
        {"expected_run_ref": None, "expected_generation": 1},
        {"expected_run_ref": "run", "expected_generation": "1"},
    ],
)
async def test_partial_or_invalid_exact_target_never_falls_back_to_current(
    tmp_path, body
):
    f = await setup(tmp_path)
    error(
        await f.send("queue.control", {**body, "control": "stop"}),
        "primary_exact_control_invalid",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "op,fields",
    [
        ("primary.state", {"unexpected": 1}),
        ("primary.messages.page", {"limit": False}),
        ("primary.messages.page", {"limit": 51}),
        ("primary.messages.detail", {"message_ref": "bad", "offset": -1}),
    ],
)
async def test_bounded_request_types_fail_closed(tmp_path, op, fields):
    f = await setup(tmp_path)
    body = fields if op == "primary.state" else {"primary_ref": f.primary, **fields}
    response = await f.send(op, body)
    assert not response["payload"]["ok"], json.dumps(response)


async def claimed(f, *, key="first", text="real admitted source"):
    from deskpet.execution.foreground_queue import ForegroundQueueStore
    from deskpet.task_scope.store import CanonicalTaskScopeStore
    from tests.execution.test_foreground_queue import SCOPE, Clock, _claim_and_bind

    await CanonicalTaskScopeStore(f.path).create_task_scope(
        task_scope_id=SCOPE, subject=AUTH.subject, title="Primary test task"
    )
    result(await f.send("queue.enqueue", {"text": text, "scope_ref": SCOPE}, key=key))
    store = ForegroundQueueStore(f.path, clock=Clock())
    admission = await _claim_and_bind(
        store, sdk_run_id="sdk-" + key, claim_key="claim-" + key
    )
    return store, admission


async def settled(
    f,
    *,
    key="first",
    text="real admitted source",
    observation=True,
    dependencies=True,
    inherited=(),
):
    from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress
    from deskpet.execution.foreground_queue import RunState
    from deskpet.sdk_adapters.composition import SdkRunTerminalEvidence
    from tests.execution.test_foreground_queue import (
        SCOPE,
        _execution_evidence,
        _ExecutionEvidence,
    )

    store, admission = await claimed(f, key=key, text=text)
    raw = _execution_evidence(
        sdk_run_id="sdk-" + key,
        source_event_id="terminal-" + key,
        kind="run_terminal",
        source_sequence=1,
        terminal_state=RunState.COMPLETED,
    ).to_json()
    with sqlite3.connect(f.path) as db:
        evidence_id, evidence_hash = db.execute(
            (
                "SELECT evidence_id,evidence_hash FROM foreground_turns "
                "WHERE subject=? AND idempotency_key=?"
            ),
            (AUTH.subject, key),
        ).fetchone()
    raw["evidence_refs"] = [
        {"evidence_id": evidence_id, "content_hash": evidence_hash, "ordinal": 1}
    ]
    raw_hash = hashlib.sha256(("sdk-terminal:" + key).encode()).hexdigest()
    raw["public_payload"]["sdk_terminal_event_hash"] = raw_hash
    source = _ExecutionEvidence(raw)
    ingress = ExecutionEvidenceIngress(f.path)
    await ingress.ingest(task_scope_id=SCOPE, source_sequence=1, evidence=source)
    await ingress.authorize_terminal("sdk-" + key)
    if observation:
        from deskpet.execution.primary_history import evidence_pair
        from deskpet.memory.human_memory_program import HumanMemoryProgramStore

        with sqlite3.connect(f.path) as db:
            turn_id = db.execute(
                "SELECT turn_id FROM foreground_runs WHERE host_run_id=?",
                (admission.host_run_id,),
            ).fetchone()[0]
        envelope, receipt = evidence_pair(
            AUTH.subject,
            "sdk-" + key,
            {
                "schema_version": 1,
                "kind": "primary_run_terminal",
                "subject": AUTH.subject,
                "host_run_id": admission.host_run_id,
                "sdk_run_id": "sdk-" + key,
                "turn_id": turn_id,
                "generation": 1,
                "terminal_state": "COMPLETED",
                "sdk_event_id": source.event_id,
                "sdk_event_hash": raw_hash,
                "messages": [],
                "error_code": None,
                **(
                    {
                        "visibility_dependencies": {
                            "schema_version": 1,
                            "evidence": [
                                {
                                    "evidence_id": evidence_id,
                                    "envelope_hash": evidence_hash,
                                },
                                *inherited,
                            ],
                            "recall": [],
                        }
                    }
                    if dependencies
                    else {}
                ),
            },
            100.0,
        )
        await HumanMemoryProgramStore(f.path).append_evidence(envelope, receipt)
    await store.record_sdk_terminal(
        host_run_id=admission.host_run_id,
        sdk_run_id="sdk-" + key,
        owner_id="owner-1",
        generation=1,
        terminal_state=RunState.COMPLETED,
        sdk_event_id=source.event_id,
        sdk_event_hash=source.evidence_hash,
        idempotency_key="terminal-" + key,
    )
    return admission, SdkRunTerminalEvidence(
        "sdk-" + key, "completed", source.event_id, raw_hash, 100.0
    )


@pytest.mark.asyncio
async def test_terminal_history_requires_exact_sdk_receipt_and_filters_hidden_blocks(
    tmp_path,
):
    facts = {}

    def reader(run_id, *, current_text):
        return facts[run_id], (
            {"role": "user", "content": current_text},
            {"role": "system", "content": "SYSTEM_CANARY"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "data": {
                            "text": "ACTUAL PUBLIC ANSWER",
                            "provider_metadata": "PRIVATE_CANARY",
                        },
                    },
                    {"type": "reasoning", "data": {"text": "HIDDEN_CANARY"}},
                    {
                        "type": "artifact",
                        "data": {
                            "artifact_ref": "artifact:1",
                            "name": "report.md",
                            "hidden": "ARTIFACT_PRIVATE",
                        },
                    },
                ],
                "hidden": "TOP_PRIVATE",
            },
            {
                "role": "tool",
                "content": "public tool result",
                "name": "read_file",
                "call_id": "call-1",
                "provider": "PRIVATE_PROVIDER",
            },
        )

    f = await setup(tmp_path, reader=reader)
    admission, terminal = await settled(f)
    facts[terminal.run_id] = terminal
    page = result(await f.send("primary.messages.page", {"primary_ref": f.primary}))
    assert [i["role"] for i in page["items"]] == ["user", "assistant", "tool"]
    wire = json.dumps(page)
    for hidden in (
        "SYSTEM_CANARY",
        "PRIVATE_CANARY",
        "HIDDEN_CANARY",
        "ARTIFACT_PRIVATE",
        "TOP_PRIVATE",
        "PRIVATE_PROVIDER",
    ):
        assert hidden not in wire
    assert "ACTUAL PUBLIC ANSWER" in wire and "report.md" in wire
    assert {i["run_ref"] for i in page["items"]} == {admission.host_run_id}
    assert {i["delivery_key"] for i in page["items"]} == {"first"}
    from dataclasses import replace

    facts[terminal.run_id] = replace(terminal, event_hash="f" * 64)
    error(
        await f.send("primary.messages.page", {"primary_ref": f.primary}),
        "primary_history_terminal_mismatch",
    )


@pytest.mark.asyncio
async def test_exact_old_run_cannot_control_next_run_and_stale_generation_rejects(
    tmp_path,
):
    f = await setup(tmp_path)
    old, _ = await settled(f)
    queue, current = await claimed(f, key="next")
    receipt = result(
        await f.send(
            "queue.control",
            {
                "expected_run_ref": old.host_run_id,
                "expected_generation": 1,
                "control": "stop",
            },
            key="old-stop",
        )
    )
    assert (
        receipt["run_ref"] == old.host_run_id
        and receipt["outcome"] == "already_terminal"
    )
    assert (
        await queue.current_snapshot(AUTH.subject)
    ).host_run_id == current.host_run_id
    assert await queue.pending_signals(current.host_run_id) == ()
    error(
        await f.send(
            "queue.control",
            {
                "expected_run_ref": current.host_run_id,
                "expected_generation": 2,
                "control": "cancel",
            },
        ),
        "foreground_generation_stale",
    )
    body = {
        "expected_run_ref": current.host_run_id,
        "expected_generation": 1,
        "control": "pause",
    }
    first = result(await f.send("queue.control", body, key="pause"))
    assert first == result(await f.send("queue.control", body, key="pause"))
    other = AuthenticatedHostSnapshot("other", "other", "host:other")
    error(
        await f.send("queue.control", body, key="wrong-owner", auth=other),
        "foreground_run_subject_mismatch",
    )


@pytest.mark.asyncio
async def test_current_run_mapping_uses_actual_injected_binding_not_primary_id(
    tmp_path,
):
    from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1

    binding = SdkRunBindingV1.build(
        run_id="sdk-first",
        session_id="actual-execution-session",
        request_id="request",
        snapshot_id="snapshot",
        provider_id="fixture",
        provider_incarnation_id="fixture-1",
        provider_config_revision=1,
        binding_epoch=1,
        model_id="fixture",
        model_params={},
        context_window=8192,
        catalog_generation=1,
        catalog_fingerprint="a" * 64,
        budget_fingerprint="b" * 64,
    )
    f = await setup(tmp_path, run_binding_reader=lambda run_id: binding)
    _, admission = await claimed(f)
    state = result(await f.send("primary.state"))
    assert state["current_run"] == {
        "run_ref": admission.host_run_id,
        "generation": 1,
        "state": "CLAIMED",
        "sdk_run_ref": "sdk-first",
        "execution_session_ref": "actual-execution-session",
    }
    assert state["current_run"]["execution_session_ref"] != f.primary


@pytest.mark.asyncio
async def test_queue_count_has_explicit_bound_and_no_unbounded_policy_scan(tmp_path):
    f = await setup(tmp_path)
    for index in range(103):
        result(
            await f.send("queue.enqueue", {"text": str(index)}, key=f"queued-{index}")
        )
    f.policy.calls.clear()
    state = result(await f.send("primary.state"))
    assert state["queued_count"] == 100 and state["queued_count_truncated"] is True
    assert (
        sum(candidate.evidence_id is not None for candidate, _ in f.policy.calls) == 100
    )


@pytest.mark.asyncio
async def test_memory_suppression_invalidates_old_refs_without_host_write(
    tmp_path,
):
    from simple_harness_memory import MemoryPrincipal, SuppressionRequest
    from simple_harness_memory.backends.sqlite_v5 import SQLiteHumanMemoryBackend
    from tests.memory.test_primary_visibility import FILTERS, classification_policy

    principal = MemoryPrincipal("host", "household", AUTH.subject, "ui")
    backend = SQLiteHumanMemoryBackend(
        tmp_path / "memory.db",
        classification_policy=classification_policy(),
        supported_filter_policies=FILTERS,
    )
    await backend.initialize()

    async def check(*, subject, disclosure_context, bindings):
        assert subject == principal.actor_id
        return await backend.check_history_visibility(
            principal=principal,
            disclosure_context=disclosure_context,
            bindings=bindings,
        )

    try:
        f = await setup(
            tmp_path / "host", policy=backend.resolve_suppression, history_checker=check
        )
        for i in range(3):
            result(
                await f.send(
                    "queue.enqueue", {"text": f"private {i}"}, key=f"input-{i}"
                )
            )
        before = result(
            await f.send(
                "primary.messages.page", {"primary_ref": f.primary, "limit": 1}
            )
        )
        ref = before["items"][0]["message_ref"]
        with sqlite3.connect(f.path) as db:
            sources = db.execute(
                "SELECT evidence_id FROM foreground_turns WHERE subject=?",
                (AUTH.subject,),
            ).fetchall()
        for i, (source,) in enumerate(sources):
            await backend.suppress(
                SuppressionRequest(
                    f"hide-{i}", AUTH.subject, "evidence", source, "user_request", 1.0
                )
            )
        # Same Host revision, even a previously-issued cursor must consult Memory again.
        after = result(
            await f.send(
                "primary.messages.page",
                {"primary_ref": f.primary, "cursor": before["next_cursor"]},
            )
        )
        assert after["revision"] == before["revision"] and after["items"] == []
        error(
            await f.send(
                "primary.messages.detail",
                {"primary_ref": f.primary, "message_ref": ref},
            ),
            "primary_message_unavailable",
        )
        assert result(await f.send("primary.state"))["queued_count"] == 0
        await backend.suppress(
            SuppressionRequest(
                "hide-subject",
                AUTH.subject,
                "subject",
                AUTH.subject,
                "user_request",
                2.0,
            )
        )
        error(await f.send("primary.state"), "primary_read_suppressed")
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_large_assistant_detail_is_complete_and_wrong_anchor_is_rejected(
    tmp_path,
):
    facts = {}
    text = "答🙂" * 6000
    anchor = ["real admitted source"]

    def reader(run_id, *, current_text):
        return facts[run_id], (
            {"role": "user", "content": anchor[0]},
            {"role": "assistant", "content": text},
        )

    f = await setup(tmp_path, reader=reader)
    _, terminal = await settled(f)
    facts[terminal.run_id] = terminal
    page = result(await f.send("primary.messages.page", {"primary_ref": f.primary}))
    item = page["items"][-1]
    assert item["has_more"] and item["total_chars"] == len(text)
    offset, chunks = 0, []
    while offset is not None:
        detail = result(
            await f.send(
                "primary.messages.detail",
                {
                    "primary_ref": f.primary,
                    "message_ref": item["message_ref"],
                    "offset": offset,
                },
            )
        )
        assert len(detail["text"]) <= 4096
        chunks.append(detail["text"])
        offset = detail["next_offset"]
    assert "".join(chunks) == text
    anchor[0] = "different user input"
    error(
        await f.send(
            "primary.messages.detail",
            {"primary_ref": f.primary, "message_ref": item["message_ref"]},
        ),
        "primary_history_anchor_mismatch",
    )


@pytest.mark.asyncio
async def test_turn_window_bounds_policy_work_and_empty_page_can_continue(tmp_path):
    f = await setup(tmp_path)
    for i in range(15):
        result(await f.send("queue.enqueue", {"text": str(i)}, key=f"turn-{i}"))
    with sqlite3.connect(f.path) as db:
        sources = db.execute(
            (
                "SELECT evidence_id FROM foreground_turns ORDER BY "
                "enqueue_sequence DESC LIMIT 10"
            )
        ).fetchall()
    f.policy.denied.update(row[0] for row in sources)
    f.policy.calls.clear()
    first = result(
        await f.send("primary.messages.page", {"primary_ref": f.primary, "limit": 50})
    )
    assert first["items"] == [] and first["next_cursor"] is not None
    assert sum(c.evidence_id is not None for c, _ in f.policy.calls) == 10
    older = result(
        await f.send(
            "primary.messages.page",
            {"primary_ref": f.primary, "limit": 50, "cursor": first["next_cursor"]},
        )
    )
    assert [i["text"] for i in older["items"]] == ["0", "1", "2", "3", "4"]
    assert older["next_cursor"] is None


@pytest.mark.asyncio
async def test_runtime_binding_reader_is_required_and_never_guesses_session(tmp_path):
    f = await setup(tmp_path)
    await claimed(f)
    error(await f.send("primary.state"), "primary_runtime_binding_unavailable")
    # Active user text is readable without reading a terminal transcript.
    page = result(await f.send("primary.messages.page", {"primary_ref": f.primary}))
    assert [item["role"] for item in page["items"]] == ["user"]


@pytest.mark.asyncio
async def test_settled_without_public_reader_never_substitutes_user_as_assistant(
    tmp_path,
):
    f = await setup(tmp_path)
    await settled(f)
    error(
        await f.send("primary.messages.page", {"primary_ref": f.primary}),
        "primary_history_reader_unavailable",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["boolean", "exception"])
async def test_policy_wrong_type_or_exception_is_not_disclosure_authority(
    tmp_path, bad
):
    def policy(candidate, purpose):
        if bad == "exception":
            raise RuntimeError("resolver down")
        return True

    f = await setup(tmp_path, policy=policy)
    error(await f.send("primary.state"), "primary_read_policy_unavailable")


@pytest.mark.asyncio
async def test_terminal_observation_source_suppression_hides_old_assistant_detail(
    tmp_path,
):
    facts = {}
    f = await setup(
        tmp_path,
        reader=lambda run_id, **kwargs: (
            facts[run_id],
            (
                {"role": "user", "content": kwargs["current_text"]},
                {"role": "assistant", "content": "settled public answer"},
            ),
        ),
    )
    _, terminal = await settled(f, observation=True)
    facts[terminal.run_id] = terminal
    with sqlite3.connect(f.path) as db:
        observation_id = db.execute(
            "SELECT evidence_id FROM human_memory_evidence WHERE source_ref=?",
            ("primary-runtime:" + terminal.run_id,),
        ).fetchone()[0]
    first = result(await f.send("primary.messages.page", {"primary_ref": f.primary}))
    ref = first["items"][-1]["message_ref"]
    assert first["items"][-1]["role"] == "assistant"
    f.policy.denied.add(observation_id)
    second = result(await f.send("primary.messages.page", {"primary_ref": f.primary}))
    assert [item["role"] for item in second["items"]] == ["user"]
    error(
        await f.send(
            "primary.messages.detail", {"primary_ref": f.primary, "message_ref": ref}
        ),
        "primary_message_unavailable",
    )


class BrokenWake:
    async def after_enqueue(self, **kwargs):
        raise RuntimeError("PRIVATE_WAKE_EXCEPTION")

    async def after_control(self, **kwargs):
        raise RuntimeError("PRIVATE_WAKE_EXCEPTION")


@pytest.mark.asyncio
async def test_committed_enqueue_ack_survives_wake_error_and_delivery_replay(
    tmp_path, caplog
):
    f = await setup(tmp_path, wake=BrokenWake())
    first = result(
        await f.send("queue.enqueue", {"text": "one durable delivery"}, key="same")
    )
    replay = result(
        await f.send("queue.enqueue", {"text": "one durable delivery"}, key="same")
    )
    assert first == replay
    with sqlite3.connect(f.path) as db:
        assert db.execute("SELECT COUNT(*) FROM foreground_turns").fetchone()[0] == 1
    assert "human_memory_scheduler_wake_deferred" in caplog.text
    assert "PRIVATE_WAKE_EXCEPTION" not in caplog.text
    # A genuine admission conflict still fails, never becoming an ACK.
    assert not (await f.send("queue.enqueue", {"text": "changed"}, key="same"))[
        "payload"
    ]["ok"]


@pytest.mark.asyncio
async def test_committed_control_ack_survives_wake_error(tmp_path):
    f = await setup(tmp_path, wake=BrokenWake())
    queue, active = await claimed(f)
    body = {
        "expected_run_ref": active.host_run_id,
        "expected_generation": 1,
        "control": "pause",
    }
    first = result(await f.send("queue.control", body, key="same-control"))
    assert first["run_ref"] == active.host_run_id and first["outcome"] == "signalled"
    assert first == result(await f.send("queue.control", body, key="same-control"))
    assert len(await queue.pending_signals(active.host_run_id)) == 1


@pytest.mark.asyncio
async def test_committed_ack_does_not_wait_forever_for_wake(tmp_path, monkeypatch):
    import asyncio

    import deskpet.memory.human_memory_service as service_module

    monkeypatch.setattr(
        service_module, "SCHEDULER_WAKE_TIMEOUT_SECONDS", 0.01, raising=False
    )

    class SlowWake:
        async def after_enqueue(self, **kwargs):
            await asyncio.Event().wait()

    f = await setup(tmp_path, wake=SlowWake())
    ack = result(
        await asyncio.wait_for(f.send("queue.enqueue", {"text": "durable"}), 1)
    )
    assert ack["delivery_key"] == "request"


@pytest.mark.asyncio
async def test_active_source_suppression_hides_current_run_mapping(tmp_path):
    # Missing mapping reader must not be consulted for a suppressed active source.
    f = await setup(tmp_path)
    await claimed(f)
    with sqlite3.connect(f.path) as db:
        evidence_id = db.execute("SELECT evidence_id FROM foreground_turns").fetchone()[
            0
        ]
    f.policy.denied.add(evidence_id)
    assert (
        result(await f.send("primary.messages.page", {"primary_ref": f.primary}))[
            "items"
        ]
        == []
    )
    assert result(await f.send("primary.state"))["current_run"] is None


@pytest.mark.asyncio
async def test_page_rechecks_previously_selected_source_after_later_reader_await(
    tmp_path,
):
    facts = {}
    f = None

    async def reader(run_id, *, current_text):
        # Newer queued text was already selected before reading this older Run.
        with sqlite3.connect(f.path) as db:
            source = db.execute(
                "SELECT evidence_id FROM foreground_turns WHERE idempotency_key='newer'"
            ).fetchone()[0]
        f.policy.denied.add(source)
        return facts[run_id], (
            {"role": "user", "content": current_text},
            {"role": "assistant", "content": "old answer"},
        )

    f = await setup(tmp_path, reader=reader)
    _, terminal = await settled(f)
    facts[terminal.run_id] = terminal
    result(await f.send("queue.enqueue", {"text": "NEWER_SOURCE_CANARY"}, key="newer"))
    page = result(await f.send("primary.messages.page", {"primary_ref": f.primary}))
    assert "NEWER_SOURCE_CANARY" not in json.dumps(page)
