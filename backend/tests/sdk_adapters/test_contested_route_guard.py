# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Incident V: a contested memory reached the model because the turn never asked.

Oracle source: HM-TO-A6 attempt 9 (evidence ``.local-test-evidence/2026-09-09/
native-a6-run9/primary-ui-8whts2lo``). Head
``proofreading_script_python_version`` was contested at 06:10:35
(``cognitive_conflict_groups`` 1 row, incumbent r2 "Python 3.13" / challenger r3
"3.12", ``cognitive_conflict_resolutions`` 0 rows). 66 s later T22 「那你现在按
哪个版本执行这套校对流程？」 produced ONE ``context_route`` call, effect
``effect-2abc513fe928b18113a7de4e66a4c47e85203376749d25fb309ddd514cb5aaf1``
(``execution_effects.prepared_at`` 06:11:41), with

    {"route": "direct_standalone",
     "query": "用户问现在按哪个 Python 版本执行秋分资料整理的校对流程，
               依据本请求中刚给出的更正（统一用 3.13，不是 3.12）直接回答。"}

and the reply was 「按 Python 3.13 执行」 with no request for confirmation
(A6-8 / NC-4 FAIL).

Root cause proven against that DB with the installed SDK 0.6.34: the incident is
NOT the SDK short-circuit. Replayed offline at the state T22 saw (the T23 UI
forget directive ``suppression-directive-1dba5ee…``, ``effective_at``
1788905616.85, removed — it postdates T22 by 115 s), that exact query through
``typed_recall`` still returns ``outcome=needs_user_confirmation``, ``items=()``
and one atomic confirmation group, and ``project_contested_confirmation``
renders the full notice. The Host simply never asked: the event-O gate lived
only inside ``context_route._memory_standalone``, and ``direct_standalone``
("answer the turn as it stands") commits a receipt with no memory read at all.

Contract anchors:
  * acceptance HM-S3 「含糊时不选边，依赖该值的任务要求确认」 — the duty is
    attached to *depending on the value*, not to choosing one route.
  * S3 §5.2 group atomicity is unchanged: the probe reuses the SDK's own
    confirmation lane, so an unrelated turn still discloses nothing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from simple_harness.contracts import RunId
from simple_harness.execution.context_authority import ContextRouteReceipt

from deskpet.memory.human_memory_v7 import (
    DEFAULT_RECALL_PURPOSE,
    recall_idempotency_key,
)
from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.sdk_adapters.context_authority import (
    ContextRouteLedgerStore,
    canonical_sha256,
)
from deskpet.sdk_adapters.context_route import (
    CONTESTED_PROBE_ATTRIBUTION_PURPOSE,
    CONTESTED_PROBE_PURPOSE,
    ContextRouteToolService,
)

RUN = "run-contested-guard"

# The exact T22 tool argument, byte for byte from ``execution_effects``.
T22_QUERY = (
    "用户问现在按哪个 Python 版本执行秋分资料整理的校对流程，"
    "依据本请求中刚给出的更正（统一用 3.13，不是 3.12）直接回答。"
)
GROUP_ID = (
    "cognitive-conflict-group-"
    "cd9b2bebaceee1ed64a214e21a47ba533c53e407967bb95a3fd6a3a2751fe7b5"
)
MEMORY_ID = (
    "cognitive-memory-"
    "84b96e0b1d01d51904b05b4c9b8cfa8475d461fbdb7999db60e0caa17ee5bf12"
)


def _tool_context(effect: str = "effect-1", raw: str = "raw-1", turn: int = 1):
    return SimpleNamespace(
        run_id=RunId(RUN),
        effect_id=SimpleNamespace(value=effect),
        task_execution_envelope=SimpleNamespace(raw_call_id=raw, turn_ordinal=turn),
    )


# The S4 fakes are the ones the five-route contract is already tested against;
# this file only adds the contested guard, so it must not fork them.
from tests.sdk_adapters.test_context_route_tool import (  # noqa: E402
    _FakeBindingAppend,
    _FakeBindingStore,
    _FakeDisclosureReader,
    _FakeService,
)


# -- the real SDK confirmation shape, rebuilt from the evidence rows ----------


def _member(ordinal: int, revision: int, value: str, *, privacy: str = "personal"):
    payload = {
        "subject_entity": "user:self",
        "predicate": "proofreading_script_python_version",
        "object_value": value,
        "qualifiers": ["在做资料校对时"],
    }
    return SimpleNamespace(
        member=SimpleNamespace(
            item_id=f"recall-confirmation:d:1:{ordinal}",
            ordinal=ordinal,
            source_ref=MEMORY_ID,
            source_revision=revision,
            memory_type=SimpleNamespace(value="semantic"),
            public_payload_hash=f"{ordinal:064d}",
        ),
        effective_privacy_class=privacy,
        public_payload=payload,
    )


def _lanes(*groups, result_id: str):
    """The production ``RecallLanes`` carrier, so the tool sees the real shape."""

    from deskpet.memory.human_memory_v7 import RecallLanes

    return RecallLanes(
        execution=SimpleNamespace(
            result=SimpleNamespace(
                items=(),
                confirmation_groups=tuple(groups),
                result_id=result_id,
                result_hash="e" * 64,
                truncated=False,
            ),
            degradation_codes=(),
        ),
        short_horizon=None,
        short_horizon_requested=False,
    )


def _contested_execution():
    """``needs_user_confirmation``: no items, one atomic group (S3 §5.3)."""

    group = SimpleNamespace(
        group=SimpleNamespace(conflict_group_id=GROUP_ID),
        members=(_member(1, 2, "Python 3.13"), _member(2, 3, "3.12")),
    )
    return _lanes(group, result_id="recall-result:evidence")


def _clear_execution():
    return _lanes(result_id="recall-result:clear")


@pytest_asyncio.fixture()
async def state_db(tmp_path: Path) -> Path:
    path = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(path)
    return path


def _tool(
    state_db: Path,
    recall,
    *,
    effect: str = "effect-1",
    raw: str = "raw-1",
    turn: int = 1,
    turn_text: str | None = None,
) -> ContextRouteToolService:
    service = _FakeService()
    binding_append = _FakeBindingAppend()
    service.binding_append = binding_append
    binding_store = _FakeBindingStore()
    for scope, revision in (("scope-new-1", 1), ("scope-a", 2)):
        binding_store.receipts[scope] = SimpleNamespace(
            binding_set_revision=revision,
            receipt_id=f"bind-{scope}",
            receipt_hash="b" * 64,
        )
    return ContextRouteToolService(
        service_factory_getter=lambda: SimpleNamespace(bind=lambda auth, **kw: service),
        binding_store_factory=lambda: binding_store,
        binding_append_getter=lambda: binding_append,
        ledger=ContextRouteLedgerStore(state_db),
        tool_context_getter=lambda: _tool_context(effect=effect, raw=raw, turn=turn),
        recall_executor=recall,
        scope_disclosure_reader=_FakeDisclosureReader(),
        **({} if turn_text is None
           else {"current_turn_text_reader": lambda run_id: turn_text}),
    )


def _executor(execution):
    async def recall(**kwargs):
        recall.calls.append(kwargs)
        return execution

    recall.calls = []
    return recall


def _detail(state_db: Path, effect_id: str = "effect-1") -> dict:
    with sqlite3.connect(state_db) as db:
        row = db.execute(
            "SELECT detail_json FROM context_route_tool_invocations "
            "WHERE sdk_run_id=? AND effect_id=?",
            (RUN, effect_id),
        ).fetchone()
    return json.loads(row[0])


# -- the incident itself -------------------------------------------------------


@pytest.mark.asyncio
async def test_t22_direct_standalone_still_gets_the_contested_notice(
    state_db: Path,
) -> None:
    tool = _tool(state_db, _executor(_contested_execution()))
    result = await tool.handle_context_route(
        {"route": "direct_standalone", "query": T22_QUERY}
    )
    notice = result["conflict_notice"]
    assert notice["reason"] == "recall_value_contested_requires_user_confirmation"
    assert notice["conflict_status"] == "contested"
    assert notice["next"] == "ask_user_to_confirm"
    group, = notice["groups"]
    assert group["conflict_group_id"] == GROUP_ID
    assert [
        (candidate["role"], candidate["revision"], candidate["value"]["object_value"])
        for candidate in group["candidates"]
    ] == [("incumbent", 2, "Python 3.13"), ("challenger", 3, "3.12")]


@pytest.mark.asyncio
async def test_the_guard_records_the_same_reason_code_without_the_values(
    state_db: Path,
) -> None:
    tool = _tool(state_db, _executor(_contested_execution()))
    await tool.handle_context_route({"route": "direct_standalone", "query": T22_QUERY})
    conflict = _detail(state_db)["recall_conflict"]
    assert conflict["reason"] == "recall_value_contested_requires_user_confirmation"
    assert conflict["groups"] == [
        {"conflict_group_id": GROUP_ID, "memory_type": "semantic", "revisions": [2, 3]}
    ]
    # Attribution, never a second copy of the user's contested values.
    assert "3.13" not in json.dumps(conflict, ensure_ascii=False)


@pytest.mark.asyncio
async def test_the_guard_delivers_no_recalled_content_on_a_non_memory_route(
    state_db: Path,
) -> None:
    """direct_standalone stays "no Memory query" as far as the model can see."""

    tool = _tool(state_db, _executor(_contested_execution()))
    result = await tool.handle_context_route(
        {"route": "direct_standalone", "query": T22_QUERY}
    )
    assert "fragments" not in result and "degradation_codes" not in result
    receipt = ContextRouteReceipt.from_json(result["context_route_receipt"])
    assert receipt.recall_refs == ()
    assert receipt.task_scope_id is None
    assert "conflict_notice" not in result["context_route_receipt"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proposal",
    [
        {"route": "direct_standalone", "query": T22_QUERY},
        {"route": "create_new", "title": "校对", "goal": T22_QUERY, "query": T22_QUERY},
        {"route": "resume_existing", "task_scope_id": "scope-a", "query": T22_QUERY},
        {"route": "continue_active", "query": T22_QUERY},
    ],
)
async def test_every_executing_route_is_guarded_not_only_memory_standalone(
    state_db: Path, proposal: dict
) -> None:
    if proposal["route"] == "continue_active":
        # The route is defined by an existing active scope; seed one the way
        # the five-route contract test does, with its own effect id.
        seeded = await _tool(
            state_db, _executor(_clear_execution()), effect="effect-seed", raw="raw-seed",
        ).handle_context_route({"route": "resume_existing", "task_scope_id": "scope-a"})
        assert "error" not in seeded, seeded
    tool = _tool(state_db, _executor(_contested_execution()))
    result = await tool.handle_context_route(proposal)
    assert "error" not in result, result
    assert result["conflict_notice"]["conflict_status"] == "contested"


# -- the clear path stays byte-identical --------------------------------------


@pytest.mark.asyncio
async def test_an_unrelated_turn_discloses_nothing_and_records_nothing(
    state_db: Path,
) -> None:
    recall = _executor(_clear_execution())
    tool = _tool(state_db, recall)
    result = await tool.handle_context_route(
        {"route": "direct_standalone", "query": "今天天气不错，随便聊聊"}
    )
    assert "conflict_notice" not in result
    detail = _detail(state_db)
    assert "recall_conflict" not in detail
    # "Asked and nothing came back" and "never asked" are different facts, and
    # the AC is about the asking: a clear probe says so rather than staying
    # silent (which is what an unavailable probe would look like).
    assert detail["contested_probe"] == "clear"
    assert "public_result_hash" not in detail
    # Relevance is the SDK's own slot-level admission, never a Host heuristic:
    # the Host asks with the model's own words and all four requestable types.
    call, = recall.calls
    assert call["query"] == "今天天气不错，随便聊聊"
    assert set(call["memory_types"]) == {
        "semantic", "episode", "procedure", "prospective"
    }
    assert call["include_short_horizon"] is False


@pytest.mark.asyncio
async def test_memory_standalone_still_probes_exactly_once(state_db: Path) -> None:
    """The event-O lane already asked; the guard must not recall a second time."""

    recall = _executor(_contested_execution())
    tool = _tool(state_db, recall)
    result = await tool.handle_context_route(
        {"route": "memory_standalone", "query": T22_QUERY, "memory_types": ["semantic"]}
    )
    assert len(recall.calls) == 1
    assert recall.calls[0]["memory_types"] == ("semantic",)
    assert result["conflict_notice"]["conflict_status"] == "contested"
    assert result["fragments"] == []


# -- the guard is advisory: it can never turn a valid route into a failure -----


@pytest.mark.asyncio
async def test_an_unavailable_probe_still_commits_the_route_and_says_so(
    state_db: Path,
) -> None:
    async def broken(**kwargs):
        raise TimeoutError("recall deadline")

    tool = _tool(state_db, broken)
    result = await tool.handle_context_route(
        {"route": "direct_standalone", "query": T22_QUERY}
    )
    assert "error" not in result
    assert "conflict_notice" not in result
    assert _detail(state_db)["contested_probe"] == "unavailable"


@pytest.mark.asyncio
async def test_a_composition_without_a_recall_executor_is_recorded_not_silent(
    state_db: Path,
) -> None:
    tool = _tool(state_db, None)
    result = await tool.handle_context_route(
        {"route": "direct_standalone", "query": T22_QUERY}
    )
    assert "error" not in result
    assert _detail(state_db)["contested_probe"] == "unavailable"


@pytest.mark.asyncio
async def test_a_proposal_with_no_query_has_no_basis_and_records_that(
    state_db: Path,
) -> None:
    recall = _executor(_contested_execution())
    tool = _tool(state_db, recall)
    result = await tool.handle_context_route({"route": "direct_standalone"})
    assert "error" not in result
    assert "conflict_notice" not in result
    assert recall.calls == []
    assert _detail(state_db)["contested_probe"] == "no_query"


@pytest.mark.asyncio
async def test_a_malformed_lane_never_fails_the_route(state_db: Path) -> None:
    tool = _tool(state_db, _executor(SimpleNamespace()))
    result = await tool.handle_context_route(
        {"route": "direct_standalone", "query": T22_QUERY}
    )
    assert "error" not in result
    assert _detail(state_db)["contested_probe"] == "unavailable"


def test_persona_binds_the_notice_to_every_route_not_only_memory_standalone() -> None:
    from deskpet.execution.primary_context import PERSONA

    assert "A conflict_notice on any context_route result" in PERSONA
    # T22 answered from the correction it had just been given in the same turn.
    assert "including a correction the user just gave" in PERSONA


# -- review MUST-FIX 1: the guard closes the string, not the class ------------
#
# The SDK admits a conflict group lexically only when a query term hits the
# group's ``contested_slot_text``. For this incident that text is
# ``{"object_value":["Python 3.13","3.12"]}`` + ``{"predicate":
# "proofreading_script_python_version"}`` — no CJK — and the memory has no
# vector generation, so the real T22 user sentence 「那你现在按哪个版本执行这套
# 校对流程？」 admits NOTHING on 0.6.34 (measured: ``confirmation_groups 0``).
# The replay only fired because the model's paraphrase quoted 「3.13，不是
# 3.12」. Neither surface is sound alone, so the Host sends both.

USER_TURN = "那你现在按哪个版本执行这套校对流程？"


def _admits(*terms: str):
    """A recall lane that answers like the SDK's slot-level lexical admission.

    Only a query mentioning one of ``terms`` reaches the conflict group; every
    other query comes back clear. This is the property the Host cannot decide
    for itself and must not paper over with a heuristic.
    """

    async def recall(**kwargs):
        recall.calls.append(kwargs)
        query = kwargs["query"]
        return (_contested_execution() if any(term in query for term in terms)
                else _clear_execution())

    recall.calls = []
    return recall


@pytest.mark.asyncio
async def test_the_probe_sends_both_the_model_paraphrase_and_the_user_turn(
    state_db: Path,
) -> None:
    recall = _admits("3.13")
    tool = _tool(state_db, recall, turn_text=USER_TURN)
    await tool.handle_context_route({"route": "direct_standalone", "query": T22_QUERY})
    probe = recall.calls[0]
    assert T22_QUERY in probe["query"] and USER_TURN in probe["query"]
    # Deterministic concatenation: model paraphrase first, admitted turn second.
    assert probe["query"] == f"{T22_QUERY}\n{USER_TURN}"
    assert _detail(state_db)["contested_probe_query_sources"] == [
        "model_query", "user_turn"
    ]


@pytest.mark.asyncio
async def test_a_plainly_worded_turn_is_still_covered_by_the_paraphrase(
    state_db: Path,
) -> None:
    """The T22 shape: only the model's wording reaches the slot (F-V-2 open)."""

    recall = _admits("3.13")
    tool = _tool(state_db, recall, turn_text=USER_TURN)
    result = await tool.handle_context_route(
        {"route": "direct_standalone", "query": T22_QUERY}
    )
    assert result["conflict_notice"]["conflict_status"] == "contested"
    # The attribution probe asks the user's sentence ALONE: it does not admit,
    # so the Host is standing on the paraphrase and the audit says exactly that.
    assert recall.calls[1]["query"] == USER_TURN
    assert _detail(state_db)["contested_probe_admitted"] == "model_query"


@pytest.mark.asyncio
async def test_the_user_turn_alone_can_carry_the_disclosure(state_db: Path) -> None:
    """The mirror case F-V-1 would have broken: a paraphrase that says nothing.

    F-V-1 (replace the model's query with the user turn) was NOT landed — here
    it is the user turn that admits, and in the test above it is the paraphrase;
    dropping either surface loses one of these two real cases.
    """

    recall = _admits("校对流程")
    tool = _tool(state_db, recall, turn_text=USER_TURN)
    result = await tool.handle_context_route(
        {"route": "direct_standalone", "query": "回答用户刚才的问题。"}
    )
    assert result["conflict_notice"]["conflict_status"] == "contested"
    assert _detail(state_db)["contested_probe_admitted"] == "user_turn"


@pytest.mark.asyncio
async def test_an_unreadable_turn_costs_one_surface_never_the_route(
    state_db: Path,
) -> None:
    def broken(run_id):
        raise RuntimeError("state.db unavailable")

    recall = _admits("3.13")
    tool = ContextRouteToolService(
        service_factory_getter=lambda: SimpleNamespace(
            bind=lambda auth, **kw: _FakeService()
        ),
        binding_store_factory=lambda: _FakeBindingStore(),
        binding_append_getter=lambda: None,
        ledger=ContextRouteLedgerStore(state_db),
        tool_context_getter=_tool_context,
        recall_executor=recall,
        current_turn_text_reader=broken,
    )
    result = await tool.handle_context_route(
        {"route": "direct_standalone", "query": T22_QUERY}
    )
    assert "error" not in result
    assert result["conflict_notice"]["conflict_status"] == "contested"
    assert _detail(state_db)["contested_probe_query_sources"] == ["model_query"]


@pytest.mark.asyncio
async def test_a_turn_the_model_quoted_verbatim_is_sent_once(state_db: Path) -> None:
    recall = _admits("校对流程")
    tool = _tool(state_db, recall, turn_text=USER_TURN)
    await tool.handle_context_route(
        {"route": "direct_standalone", "query": USER_TURN}
    )
    assert recall.calls[0]["query"] == USER_TURN
    assert _detail(state_db)["contested_probe_query_sources"] == ["model_query"]


# -- review MUST-FIX 2: one provider response, two context_route calls --------


class _RecallIdempotencyConflict(RuntimeError):
    """Stands in for the SDK's ``MemoryIdempotencyConflict``.

    ``sqlite_v5`` stores one durable request per ``RecallPlan.idempotency_key``
    and rejects a second, different request under that key
    (``test_model_recall_selection.py::
    test_changed_selection_cannot_reuse_same_plan_result``).
    """


class _IdempotentRecall:
    """A recall lane that enforces the SDK's durable-request rule."""

    def __init__(self, execution=None) -> None:
        self._execution = execution
        self.calls: list[dict] = []
        self.keys: dict[str, tuple] = {}

    def key_of(self, call: dict) -> str:
        return recall_idempotency_key(
            call.get("idempotency_purpose", DEFAULT_RECALL_PURPOSE),
            call["run_id"], call["turn_ordinal"], call.get("idempotency_scope"),
        )

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        request = (kwargs["query"], tuple(kwargs["memory_types"]),
                   bool(kwargs["include_short_horizon"]))
        key = self.key_of(kwargs)
        if self.keys.setdefault(key, request) != request:
            raise _RecallIdempotencyConflict(key)
        return self._execution if self._execution is not None else _contested_execution()


@pytest.mark.asyncio
async def test_the_probe_never_spends_the_models_own_recall_key(
    state_db: Path,
) -> None:
    """Probe on one call, ``memory_standalone`` on the next, same turn ordinal.

    ``turn_ordinal`` is per provider *response*, so a whole tool batch shares
    it. Keyed on the turn alone, the probe would consume
    ``context-route:{run}:{turn}`` and the model's real recall would come back
    ``context_route_adjudication_failed``.
    """

    recall = _IdempotentRecall()
    probing = _tool(state_db, recall, effect="effect-a", raw="raw-a", turn=7)
    first = await probing.handle_context_route(
        {"route": "direct_standalone", "query": T22_QUERY}
    )
    assert "error" not in first, first
    real = _tool(state_db, recall, effect="effect-b", raw="raw-b", turn=7)
    second = await real.handle_context_route(
        {"route": "memory_standalone", "query": "校对脚本 Python 版本",
         "memory_types": ["semantic"]}
    )
    assert "error" not in second, second
    assert [recall.key_of(call) for call in recall.calls] == [
        f"{CONTESTED_PROBE_PURPOSE}:{RUN}:7:effect-a",
        f"{DEFAULT_RECALL_PURPOSE}:{RUN}:7:effect-b",
    ]


@pytest.mark.asyncio
async def test_two_non_memory_routes_in_one_turn_both_reach_memory(
    state_db: Path,
) -> None:
    """Neither probe is swallowed by the other's durable request."""

    recall = _IdempotentRecall()
    for effect, query in (("effect-a", T22_QUERY), ("effect-b", USER_TURN)):
        tool = _tool(state_db, recall, effect=effect, raw=f"raw-{effect}", turn=7)
        result = await tool.handle_context_route(
            {"route": "direct_standalone", "query": query}
        )
        assert "error" not in result, result
        assert result["conflict_notice"]["conflict_status"] == "contested"
        assert _detail(state_db, effect)["contested_probe"] == "contested"
    assert [recall.key_of(call) for call in recall.calls] == [
        f"{CONTESTED_PROBE_PURPOSE}:{RUN}:7:effect-a",
        f"{CONTESTED_PROBE_PURPOSE}:{RUN}:7:effect-b",
    ]


@pytest.mark.asyncio
async def test_the_attribution_probe_has_its_own_key_too(state_db: Path) -> None:
    recall = _IdempotentRecall()
    tool = _tool(state_db, recall, effect="effect-a", turn=7, turn_text=USER_TURN)
    result = await tool.handle_context_route(
        {"route": "direct_standalone", "query": T22_QUERY}
    )
    assert "error" not in result, result
    assert [recall.key_of(call) for call in recall.calls] == [
        f"{CONTESTED_PROBE_PURPOSE}:{RUN}:7:effect-a",
        f"{CONTESTED_PROBE_ATTRIBUTION_PURPOSE}:{RUN}:7:effect-a",
    ]


# -- review MUST-FIX 3: a contest found on a proposal that then fails ---------


@pytest.mark.asyncio
async def test_a_rejected_route_still_delivers_the_contest_it_discovered(
    state_db: Path,
) -> None:
    """Asked Memory, was told "contested", and told the model nothing = T22."""

    tool = _tool(state_db, _executor(_contested_execution()))
    result = await tool.handle_context_route(
        {"route": "continue_active", "query": T22_QUERY}
    )
    assert result["ok"] is False
    # The stable error contract is untouched; the notice rides the error body.
    assert result["error"]["code"] == "context_route_no_active_task_scope"
    assert result["error"]["conflict_notice"]["conflict_status"] == "contested"
    group, = result["error"]["conflict_notice"]["groups"]
    assert group["conflict_group_id"] == GROUP_ID
    detail = _detail(state_db)
    assert detail["code"] == "context_route_no_active_task_scope"
    assert detail["contested_probe"] == "contested"
    assert detail["recall_conflict"]["groups"][0]["revisions"] == [2, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("proposal", "code"),
    [
        ({"route": "continue_active", "task_scope_id": "scope-x", "query": T22_QUERY},
         "context_route_no_active_task_scope"),
        ({"route": "resume_existing", "query": T22_QUERY},
         "context_route_exact_task_scope_required"),
        ({"route": "create_new", "query": T22_QUERY},
         "context_route_title_required"),
    ],
)
async def test_every_pre_dispatch_rejection_keeps_the_notice(
    state_db: Path, proposal: dict, code: str
) -> None:
    tool = _tool(state_db, _executor(_contested_execution()))
    result = await tool.handle_context_route(proposal)
    assert result["error"]["code"] == code
    assert result["error"]["conflict_notice"]["conflict_status"] == "contested"


# -- review MUST-FIX 4: what the model received must be hashed ----------------


@pytest.mark.asyncio
async def test_a_non_memory_route_hashes_the_result_that_carried_the_notice(
    state_db: Path,
) -> None:
    """No typed carrier is built here, so before event V nothing covered it."""

    tool = _tool(state_db, _executor(_contested_execution()))
    result = await tool.handle_context_route(
        {"route": "direct_standalone", "query": T22_QUERY}
    )
    detail = _detail(state_db)
    assert "typed_carrier" not in detail
    assert detail["public_result_hash"] == canonical_sha256(result)


@pytest.mark.asyncio
async def test_a_rejection_body_carrying_a_notice_is_hashed_too(
    state_db: Path,
) -> None:
    tool = _tool(state_db, _executor(_contested_execution()))
    result = await tool.handle_context_route(
        {"route": "continue_active", "query": T22_QUERY}
    )
    assert _detail(state_db)["public_result_hash"] == canonical_sha256(result)


@pytest.mark.asyncio
async def test_a_rejection_without_a_notice_records_no_hash(state_db: Path) -> None:
    """The clear path keeps the pre-event-V rejection detail."""

    tool = _tool(state_db, _executor(_clear_execution()))
    result = await tool.handle_context_route(
        {"route": "continue_active", "query": "今天天气不错，随便聊聊"}
    )
    assert result["error"]["code"] == "context_route_no_active_task_scope"
    assert "conflict_notice" not in result["error"]
    detail = _detail(state_db)
    assert "public_result_hash" not in detail and "recall_conflict" not in detail
    assert detail["contested_probe"] == "clear"


# -- cheap controls the review asked for --------------------------------------


@pytest.mark.asyncio
async def test_a_degraded_probe_says_so_instead_of_passing_as_clear(
    state_db: Path,
) -> None:
    """A silently degraded lane must not be indistinguishable from "clear"."""

    from deskpet.memory.human_memory_v7 import RecallLanes

    lanes = RecallLanes(
        execution=SimpleNamespace(
            result=SimpleNamespace(items=(), confirmation_groups=(),
                                   result_id="r", result_hash="e" * 64,
                                   truncated=False),
            degradation_codes=(SimpleNamespace(value="short_horizon_unavailable"),),
        ),
        short_horizon=None,
        short_horizon_requested=False,
    )
    tool = _tool(state_db, _executor(lanes))
    result = await tool.handle_context_route(
        {"route": "direct_standalone", "query": T22_QUERY}
    )
    detail = _detail(state_db)
    assert detail["contested_probe"] == "clear"
    assert detail["contested_probe_degradation_codes"] == ["short_horizon_unavailable"]
    # Audit-only: the model still receives no recall surface of any kind.
    assert set(result) == {"context_route_receipt"}


@pytest.mark.asyncio
async def test_a_cancelled_probe_leaves_no_created_task_scope(state_db: Path) -> None:
    """Why the probe runs before dispatch, not inside ``_commit_receipt``.

    ``create_new`` opens the scope and appends the binding *before* the route
    decision is durable, so a probe placed after dispatch could be cancelled
    with a scope already created and no route decision recorded.
    """

    import asyncio

    service = _FakeService()
    append = _FakeBindingAppend()
    service.binding_append = append

    async def cancelled(**kwargs):
        raise asyncio.CancelledError

    tool = ContextRouteToolService(
        service_factory_getter=lambda: SimpleNamespace(bind=lambda auth, **kw: service),
        binding_store_factory=lambda: _FakeBindingStore(),
        binding_append_getter=lambda: append,
        ledger=ContextRouteLedgerStore(state_db),
        tool_context_getter=_tool_context,
        recall_executor=cancelled,
    )
    with pytest.raises(asyncio.CancelledError):
        await tool.handle_context_route(
            {"route": "create_new", "title": "校对", "goal": T22_QUERY,
             "query": T22_QUERY}
        )
    assert service.opened == [] and append.calls == []
    with sqlite3.connect(state_db) as db:
        assert db.execute(
            "SELECT count(*) FROM context_route_decisions WHERE sdk_run_id=?", (RUN,)
        ).fetchone()[0] == 0


def test_the_probe_keeps_the_single_audited_recall_caller() -> None:
    """``quality/audit_coverage.py`` correlates typed recall by ``caller``.

    ``memory_attempts.CALLERS`` is a closed set and
    ``check_typed_recall_foreground`` matches ``caller=foreground_recall`` with
    ``idempotency_key NOT LIKE 'analysis-candidates-%'`` — which the probe's
    ``contested-probe:…`` keys already satisfy. Splitting the probe into its own
    caller would need a third registered check, so it deliberately does not.
    """

    import inspect as _inspect

    from deskpet.memory import human_memory_v7
    from deskpet.operation_audit.memory_attempts import CALLERS

    assert 'caller="foreground_recall"' in _inspect.getsource(
        human_memory_v7.HumanMemoryV7Runtime.typed_recall
    )
    assert "foreground_recall" in CALLERS
    assert not recall_idempotency_key(
        CONTESTED_PROBE_PURPOSE, RUN, 1, "effect-1"
    ).startswith("analysis-candidates-")
