"""F-E2: the same-Run bound for CONTROL tool results.

Incident: HM-TO-A6 attempt 8 (``deepseek-v4-flash``, window pinned to 32000 →
``effective_input_budget`` 26752).  Provider turn 10 planned 27015 tokens and
failed closed with ``sdk_context_budget_exceeded`` — 263 tokens over — after the
whole ordered degradation had already run: every pageable settled body was
force-paged, history was down to the single open group, and that open group
alone was 19244 tokens.  In the last request that actually exists (turn 9,
22 messages) the CONTROL carriers were 33 022 of the 41 078 tool-result bytes.

These tests reproduce that shape against the real estimator, the real frozen
budget and the real projector, and assert that the control bound closes the gap
while every attestation-safety rule holds.
"""
import json
import sqlite3
from types import SimpleNamespace

import pytest
from simple_harness import CallId, thaw_json
from simple_harness.contracts.messages import Message, MessageRole

from deskpet.execution.current_tool_pages import (
    CONTROL_MARKER, CONTROL_TOOLS, STUBBABLE_CONTROL_TOOLS, CurrentToolProjector,
    MARKER, PrimaryContextPageUnavailable, _control_tool_tokens,
    control_result_allowance, control_stub, control_stub_content, verify_control_stubs,
)
from deskpet.sdk_adapters.context_partitions import (
    ProviderTokenCalibration, effective_input_budget, text_tokens,
)
from deskpet.task_scope.protocol import canonical_json, redact_credential_shapes

RUN_ID = "product-sdk-" + "3f" * 32
HOST_RUN_ID = "9cae01fc-0000-4000-8000-000000000001"
SUBJECT = "subject-f-e2"
CURRENT_TEXT = "主清单 A 里 ANCHOR-ALPHA 那一条的完整取值是什么？照原文给我，不要概括。"
FLASH_RATIO = 1.65
WINDOW = 32000


def public_body(value):
    """The exact public tool body the Host projects for a settled result."""
    return redact_credential_shapes(canonical_json(dict(
        outcome="succeeded", value=thaw_json(value),
        error_code=None, public_message=None)))[0]


def sized_body(tag, target_bytes):
    """A public body of (almost) exactly ``target_bytes``, ASCII only.

    ASCII keeps ``text_tokens`` equal to ceil(bytes/4), so every assertion below
    compares the real Host estimator against the real frozen budget.
    """
    body = public_body({"tag": tag, "filler": ""})
    pad = max(0, target_bytes - len(body.encode()))
    return public_body({"tag": tag, "filler": "x" * pad})


class FakeEffect:
    def __init__(self, *, effect_id, tool_name, raw_call_id, value, state="succeeded"):
        self.effect_id = SimpleNamespace(value=effect_id)
        self.run_id = SimpleNamespace(value=RUN_ID)
        self.version = 3
        self.state = SimpleNamespace(value=state)
        self.terminal = True
        self.tool_name = tool_name
        self.raw_call_id = raw_call_id
        self.result = SimpleNamespace(outcome=SimpleNamespace(value=state),
                                      value=value, error_code=None, public_message=None)
        self.evidence_ref = "evidence-" + effect_id[-8:]


class FakeStack:
    """Public effect/audit facts, exactly the surface the projector reads."""

    def __init__(self, effects):
        self.effects = effects          # effect_id -> FakeEffect
        self.sources = []               # causal sources, in transcript order
        self.page_facts_calls = []

    def read_primary_dependency_facts(self, run_id, *args):
        return {"input": admission_facts()[0]}, ()

    def read_primary_effect_page_facts(self, run_id, effect_id):
        self.page_facts_calls.append((run_id, effect_id))
        effect = self.effects.get(effect_id)
        if effect is None or run_id != RUN_ID:
            raise PrimaryContextPageUnavailable("primary_effect_page_effect_missing")
        head = SimpleNamespace(result_hash="a1" * 32, provider_invocation_id="inv-" + effect_id[-8:])
        parent = SimpleNamespace(invocation_id="inv-" + effect_id[-8:], response_json={"id": effect_id})
        return effect, head, parent, SimpleNamespace(messages=())

    async def read_primary_tool_causal_sources(self, **kwargs):
        return list(self.sources)


# --- the incident's own message shape (native evidence, provider turn 9) ---
# (role, tool name, content bytes) — byte counts are the measured ones.
INCIDENT_TAIL = [
    ("assistant", None, 0),
    ("tool", "task_scope_search", 12389),
    ("tool", "context_route", 4265),
    ("assistant", None, 176),
    ("tool", "context_route", 331),
    ("assistant", None, 0),
    ("tool", "context_route", 6977),
    ("assistant", None, 123),
    ("tool", "tool_search", 2027),
    ("tool", "tool_search", 1993),
    ("assistant", None, 0),
    ("tool", "tool_search", 2009),
    ("assistant", None, 78),
    ("tool", "context_page_in", 2287),
    ("tool", "context_page_in", 2269),
    ("assistant", None, 69),
    ("tool", "context_page_in", 2256),
    ("tool", "context_page_in", 2248),
    ("assistant", None, 81),
    ("tool", "tool_search", 2027),
    # Provider turn 10's own batch: the one the Run never got to send.
    ("assistant", None, 0),
    ("tool", "context_page_in", 2280),
    ("tool", "tool_search", 2020),
]


def build_incident(system_chars):
    """The turn-10 request shape plus the fake public facts behind it."""
    messages = [Message(MessageRole.SYSTEM, "S" * system_chars),
                Message(MessageRole.USER, CURRENT_TEXT)]
    effects, sources, turn = {}, [], 0
    for position, (role, name, size) in enumerate(INCIDENT_TAIL):
        if role == "assistant":
            turn += 1
            messages.append(Message(MessageRole.ASSISTANT, "a" * size))
            continue
        effect_id = "effect-%02d%s" % (position, "c" * 60)
        raw_call_id = "call_00_%02d%s" % (position, "d" * 20)
        value = {"tag": effect_id, "filler": ""}
        content = sized_body(effect_id, size)
        # The effect's own public body must be byte-identical to the message:
        # ``_project`` checks exactly that before it touches anything.
        pad = max(0, size - len(public_body(value).encode()))
        value = {"tag": effect_id, "filler": "x" * pad}
        assert public_body(value) == content
        effects[effect_id] = FakeEffect(effect_id=effect_id, tool_name=name,
                                        raw_call_id=raw_call_id, value=value)
        messages.append(Message(MessageRole.TOOL, content, name=name,
                                call_id=CallId(raw_call_id)))
        sources.append(dict(effect_id=effect_id, provider_turn_ordinal=turn,
                            state="succeeded", tool_name=name))
    # ``item_ordinal`` is the 1-based position in the non-system suffix that
    # starts at the admitted current user message.
    ordinal_of = {}
    suffix = [i for i in range(1, len(messages))]
    for index, message in enumerate(messages):
        if index in suffix:
            ordinal_of[index] = suffix.index(index) + 1
    tool_indices = [i for i, m in enumerate(messages) if m.role.value == "tool"]
    for source, index in zip(sources, tool_indices, strict=True):
        source["item_ordinal"] = ordinal_of[index]
    return tuple(messages), effects, sources


def make_projector(tmp_path, stack):
    path = str(tmp_path / "state.db")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE foreground_runs (host_run_id TEXT PRIMARY KEY, subject TEXT)")
        db.execute("CREATE TABLE foreground_run_sdk_bindings (host_run_id TEXT, sdk_run_id TEXT)")
        db.execute("INSERT INTO foreground_runs VALUES (?,?)", (HOST_RUN_ID, SUBJECT))
        db.execute("INSERT INTO foreground_run_sdk_bindings VALUES (?,?)", (HOST_RUN_ID, RUN_ID))
    return CurrentToolProjector(path, lambda: stack)


def admission_facts():
    metadata = {"context_window": WINDOW, "model_id": "deepseek-v4-flash",
                "root_run_id": HOST_RUN_ID, "visibility_dependencies": {}}
    return {"input": {"text": CURRENT_TEXT}, "context_metadata": metadata,
            "messages": [{"role": "user", "content": CURRENT_TEXT}],
            "turn": {"text": CURRENT_TEXT}}, metadata


@pytest.fixture
def flash_calibration(monkeypatch):
    """Pin the ``deepseek-v4-flash`` estimator ratio the incident ran at."""
    from deskpet.sdk_adapters import context_partitions
    calibration = ProviderTokenCalibration(base_ratio=FLASH_RATIO, max_ratio=FLASH_RATIO)
    monkeypatch.setattr(context_partitions, "calibration_for_model", lambda _id: calibration)
    return calibration


# --------------------------------------------------------------------------
# 1. The elision notice itself: deterministic, small, and refused where it must
#    be refused.
# --------------------------------------------------------------------------

def test_control_stub_is_deterministic_small_and_refuses_route_carriers():
    value = {"tag": "t", "filler": "x" * 12000}
    effect = FakeEffect(effect_id="effect-" + "c" * 60, tool_name="task_scope_search",
                        raw_call_id="call_00_" + "d" * 20, value=value)
    head = SimpleNamespace(result_hash="a1" * 32)
    facts = (effect, head, SimpleNamespace(), SimpleNamespace())

    descriptor, content = control_stub_content(facts)
    again, _ = control_stub_content(facts)
    assert descriptor == again and control_stub(descriptor) == control_stub(again)
    body = control_stub(descriptor)
    # Fixed shape, fixed cost: no excerpt of the elided body travels with it,
    # so the notice cannot grow with the result it replaces.
    assert len(body.encode()) < 700 and text_tokens(body) < 200
    assert descriptor["content_bytes"] == len(content.encode())
    # Content addresses travel; nothing derived from the body's *content* does.
    assert descriptor["content_hash"] in body
    assert "filler" not in body and "x" * 32 not in body
    assert json.loads(body)["kind"] == CONTROL_MARKER
    assert json.loads(body)["refetch"].startswith("elided;")

    # A route carrier is refused outright: its body is re-read out of the
    # outgoing request by the typed context-use authority.
    route = FakeEffect(effect_id="effect-route", tool_name="context_route",
                       raw_call_id="call_route", value=value)
    with pytest.raises(PrimaryContextPageUnavailable) as excinfo:
        control_stub_content((route, head, SimpleNamespace(), SimpleNamespace()))
    assert "control_not_stubbable" in str(excinfo.value)
    assert "context_route" in CONTROL_TOOLS and "context_route" not in STUBBABLE_CONTROL_TOOLS

    # So is a non-succeeded settled effect: it has no public body to stand for.
    failed = FakeEffect(effect_id="effect-failed", tool_name="task_scope_update",
                        raw_call_id="call_failed", value=value, state="failed")
    with pytest.raises(PrimaryContextPageUnavailable):
        control_stub_content((failed, head, SimpleNamespace(), SimpleNamespace()))


def test_verify_control_stubs_refuses_a_forged_or_foreign_notice():
    effect_id = "effect-" + "c" * 60
    raw_call_id = "call_00_" + "d" * 20
    effect = FakeEffect(effect_id=effect_id, tool_name="task_scope_search",
                        raw_call_id=raw_call_id, value={"tag": "t", "filler": "x" * 4000})
    stack = FakeStack({effect_id: effect})
    descriptor, _ = control_stub_content(stack.read_primary_effect_page_facts(RUN_ID, effect_id))
    good = Message(MessageRole.TOOL, control_stub(descriptor), name="task_scope_search",
                   call_id=CallId(raw_call_id), metadata={"source": CONTROL_MARKER})
    assert verify_control_stubs(stack, RUN_ID, (good,)) == 1

    tampered = json.loads(good.content)
    tampered["source"]["content_bytes"] = 1
    forged = Message(MessageRole.TOOL, canonical_json(tampered), name="task_scope_search",
                     call_id=CallId(raw_call_id), metadata={"source": CONTROL_MARKER})
    with pytest.raises(PrimaryContextPageUnavailable):
        verify_control_stubs(stack, RUN_ID, (forged,))

    foreign = json.loads(good.content)
    foreign["source"]["run_id"] = "product-sdk-" + "ff" * 32
    with pytest.raises(PrimaryContextPageUnavailable):
        verify_control_stubs(stack, RUN_ID, (Message(MessageRole.TOOL,
            canonical_json(foreign), name="task_scope_search",
            call_id=CallId(raw_call_id), metadata={"source": CONTROL_MARKER}),))


# --------------------------------------------------------------------------
# 2. End-to-end style: the incident's own request shape, the real estimator,
#    the real frozen budget.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_incident_shape_fits_once_control_results_are_bounded(tmp_path, flash_calibration):
    """Turn 10 of HM-TO-A6 attempt 8: 27015 planned vs 26752 effective.

    The system prefix is sized so the unbounded plan misses the frozen budget by
    exactly the incident's 263 tokens; everything else is the measured shape.
    """
    from deskpet.sdk_adapters.context_authority import _current_tool_page_facts, _plan_turn_messages

    effective = effective_input_budget(WINDOW)
    assert effective == 26752

    def plan(messages, **kwargs):
        return _plan_turn_messages(messages, WINDOW, tools=(), exact_tool_sources=True,
                                   model_id="deepseek-v4-flash", provider_turn_ordinal=10,
                                   raise_on_overflow=False, allow_full_group_trim=True, **kwargs)

    # Size the protected prefix so the unbounded plan is over by exactly 263.
    probe, _, _ = build_incident(4)
    _, facts = plan(probe)
    overshoot = 263
    needed = effective + overshoot - facts["planned_input_tokens"]
    system_chars = 4 + needed * 4 * 1000 // int(FLASH_RATIO * 1000)
    messages, effects, sources = build_incident(system_chars)
    _, before_facts = plan(messages)
    assert before_facts["planned_input_tokens"] > effective
    assert before_facts["budget_headroom"] < 0
    assert before_facts["causal_groups"] == 1, "the incident had one un-trimmable open group"

    stack = FakeStack(effects)
    stack.sources = sources
    admission, metadata = admission_facts()
    projector = make_projector(tmp_path, stack)

    control_bytes = sum(len(m.content.encode()) for m in messages
                        if m.role.value == "tool" and m.name in CONTROL_TOOLS)
    # 33 022 B measured on turn 9 + turn 10's own 2 280 B context_page_in.
    assert control_bytes == 35_302, control_bytes
    allowance = control_result_allowance(metadata, provider_turn_ordinal=10)
    assert _control_tool_tokens(messages) > allowance, "the bound must actually fire"

    request = SimpleNamespace(run_id=SimpleNamespace(value=RUN_ID), provider_turn_ordinal=10)
    projected = await projector(request, messages)
    assert projected is not None
    after, after_facts = plan(tuple(projected))

    # 1) The request now fits, and it fits *because* control bodies were elided.
    assert after_facts["budget_headroom"] >= 0, after_facts
    assert after_facts["planned_input_tokens"] <= effective

    # 2) The receipt attributes it (A6-3 / A6-4), and only the elidable
    #    families lost their bodies.
    receipt = _current_tool_page_facts(after)
    assert receipt["control_results_stubbed"] >= 1
    assert receipt["control_result_tokens"] < _control_tool_tokens(messages)
    stubbed = [m for m in projected
               if m.metadata and m.metadata.get("source") == CONTROL_MARKER]
    assert {m.name for m in stubbed} <= STUBBABLE_CONTROL_TOOLS
    assert all(m.name in CONTROL_TOOLS for m in stubbed)

    # 3) Every route carrier still travels with its complete bytes: its body is
    #    re-read out of the request by the typed context-use authority.
    routes_before = [m for m in messages if m.name == "context_route"]
    routes_after = [m for m in projected if m.name == "context_route"]
    assert [m.content for m in routes_before] == [m.content for m in routes_after]

    # 4) The causal chain is intact: same roles, names and call ids, no message
    #    added, dropped or reordered.
    assert len(projected) == len(messages)
    assert [(m.role.value, m.name, m.call_id) for m in projected] \
        == [(m.role.value, m.name, m.call_id) for m in messages]

    # 5) Every stub re-derives against public authority before the request may
    #    leave (the gate ``check_runtime_dependencies`` runs).
    assert verify_control_stubs(stack, RUN_ID, projected) == len(stubbed)

    # 6) An elision notice is NOT a page reference: it must never enter the
    #    admission map ``verify_request`` returns, or eliding an executable
    #    attestation would make its body context_page_in-readable.
    from deskpet.execution.current_tool_pages import verify_request
    admitted = verify_request(stack, RUN_ID, projected)
    stubbed_effect_ids = {json.loads(m.content)["source"]["effect_id"] for m in stubbed}
    assert stubbed_effect_ids
    assert not any(descriptor["effect_id"] in stubbed_effect_ids
                   for descriptor, _ in admitted.values())
    assert all(descriptor["tool_name"] not in CONTROL_TOOLS
               for descriptor, _ in admitted.values())

    # 7) Deterministic: the same Run state projects to the same bytes.
    again = await projector(request, messages)
    assert [m.content for m in again] == [m.content for m in projected]


@pytest.mark.asyncio
async def test_newest_provider_turn_control_results_stay_verbatim(tmp_path, flash_calibration):
    """Even under ``force_all``, the batch the model is acting on keeps its bytes."""
    messages, effects, sources = build_incident(4)
    stack = FakeStack(effects)
    stack.sources = sources
    admission, metadata = admission_facts()
    projector = make_projector(tmp_path, stack)
    request = SimpleNamespace(run_id=SimpleNamespace(value=RUN_ID), provider_turn_ordinal=10)

    newest = max(source["provider_turn_ordinal"] for source in sources)
    newest_ids = {s["effect_id"] for s in sources
                  if s["provider_turn_ordinal"] == newest and s["tool_name"] in CONTROL_TOOLS}
    assert newest_ids, "the incident's last batch really does contain a control result"

    projected = await projector(request, messages, force_all=True)
    kept = {m.call_id.value for m in projected
            if m.role.value == "tool" and m.name in CONTROL_TOOLS
            and not (m.metadata and m.metadata.get("source") == CONTROL_MARKER)}
    for effect_id in newest_ids:
        assert effects[effect_id].raw_call_id in kept, effect_id

    # Older control carriers did give way, so this is not a vacuous assertion.
    assert any(m.metadata and m.metadata.get("source") == CONTROL_MARKER for m in projected)


@pytest.mark.asyncio
async def test_the_protected_turn_comes_from_all_sources_not_the_elidable_ones(
        tmp_path, flash_calibration):
    """Independent-review MUST-FIX (2026-09-09).

    The common HM shape is one big ``task_scope_search`` at turn 1 plus routes
    and non-control work afterwards.  If the protected provider turn were taken
    from the *elidable* control sources only, that single search would always be
    "the newest control result" and could never be elided — the bound would be a
    no-op forever, in precisely the shape the incident is about.  The protected
    turn must come from every causal source.
    """
    admission, metadata = admission_facts()
    big = {"tag": "search", "filler": "x" * 12000}
    route = {"tag": "route", "filler": "x" * 6000}
    recent = {"tag": "recent", "filler": "x" * 2500}
    rows = [("task_scope_search", big, 1), ("context_route", route, 1),
            ("tool_search", recent, 2)]
    messages = [Message(MessageRole.SYSTEM, "S" * 40), Message(MessageRole.USER, CURRENT_TEXT)]
    effects, sources = {}, []
    for position, (name, value, turn) in enumerate(rows):
        effect_id = "effect-%d" % position
        raw_call_id = "call_%d" % position
        effects[effect_id] = FakeEffect(effect_id=effect_id, tool_name=name,
                                        raw_call_id=raw_call_id, value=value)
        messages.append(Message(MessageRole.TOOL, public_body(value), name=name,
                                call_id=CallId(raw_call_id)))
        sources.append(dict(effect_id=effect_id, provider_turn_ordinal=turn,
                            state="succeeded", tool_name=name,
                            item_ordinal=len(messages) - 1))
    messages = tuple(messages)
    stack = FakeStack(effects)
    stack.sources = sources
    projector = make_projector(tmp_path, stack)
    request = SimpleNamespace(run_id=SimpleNamespace(value=RUN_ID), provider_turn_ordinal=3)

    # Only ONE elidable control result exists, and it is the oldest thing here.
    elidable = [s for s in sources if s["tool_name"] in STUBBABLE_CONTROL_TOOLS]
    assert len(elidable) == 1 and elidable[0]["provider_turn_ordinal"] == 1
    assert _control_tool_tokens(messages) > control_result_allowance(metadata, provider_turn_ordinal=3)

    projected = await projector(request, messages)
    stubbed = [m for m in projected
               if m.metadata and m.metadata.get("source") == CONTROL_MARKER]
    assert len(stubbed) == 1 and stubbed[0].name == "task_scope_search"
    # …and the route carrier, which shares that stale turn ordinal, is untouched.
    assert [m.content for m in projected if m.name == "context_route"] \
        == [m.content for m in messages if m.name == "context_route"]


@pytest.mark.asyncio
async def test_a_run_below_the_control_allowance_keeps_every_byte(tmp_path, flash_calibration):
    """Zero request drift on turns that never approach the bound."""
    admission, metadata = admission_facts()
    small = public_body({"tag": "small", "filler": "x" * 40})
    effect_id = "effect-small"
    effects = {effect_id: FakeEffect(effect_id=effect_id, tool_name="task_scope_search",
                                     raw_call_id="call_small", value={"tag": "small", "filler": "x" * 40})}
    messages = (Message(MessageRole.SYSTEM, "S" * 40),
                Message(MessageRole.USER, CURRENT_TEXT),
                Message(MessageRole.ASSISTANT, ""),
                Message(MessageRole.TOOL, small, name="task_scope_search",
                        call_id=CallId("call_small")))
    stack = FakeStack(effects)
    stack.sources = [dict(effect_id=effect_id, provider_turn_ordinal=1, state="succeeded",
                          tool_name="task_scope_search", item_ordinal=3)]
    projector = make_projector(tmp_path, stack)
    request = SimpleNamespace(run_id=SimpleNamespace(value=RUN_ID), provider_turn_ordinal=1)

    assert _control_tool_tokens(messages) <= control_result_allowance(metadata, provider_turn_ordinal=1)
    projected = await projector(request, messages)
    # Nothing to page and nothing over any bound: the projector declines, which
    # is what keeps this turn's request bytes exactly what they were before.
    assert projected is None or tuple(projected) == messages
    assert not stack.page_facts_calls
