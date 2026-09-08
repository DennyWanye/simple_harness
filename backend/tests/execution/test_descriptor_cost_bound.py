"""F-E3: the paged descriptor's own cost.

Incident: HM-TO-A6 attempt 9 (``deepseek-v4-flash``, window pinned to 32000 →
``effective_input_budget`` 26752).  Four Runs failed closed with
``sdk_context_budget_exceeded`` (planned 26862 / 27007 / 27932 / 27982) *while
their open group was already almost entirely paged*.  Measured on the last
request each of them actually sent
(``.local-test-evidence/2026-09-09/native-a6-run9/primary-ui-8whts2lo/
userdata/data/simple-harness-sdk/execution-v6.sqlite3``):

    run           paged msgs   descriptor B   original B   descriptor tokens
    fd4b0849e7      13            26 060        72 035          6 550
    539ca5f03b      14            28 076        79 175          7 073
    5c893a4459      16            32 098        98 817          8 036
    4996b84db6       6            12 014        30 737          3 065

Every descriptor was 1 960-2 030 B (~500 token) whatever the body was, because
it carried a full 1 KiB ``_excerpt`` plus ten descriptor fields — of which
``excerpt`` 1 086 B, ``source.run_id`` 88 B, ``provider_invocation_id`` 92 B,
``provider_response_hash`` 92 B, ``result_hash`` 81 B, ``content_hash`` 82 B
(a duplicate of ``source_hash``), ``effect_version`` 19 B.  Paging a 2 098 B
``tool_describe`` body into a 2 011 B descriptor saved 4 %; paging a 1.7 KB one
was refused outright and the Run simply carried the body.

These tests pin the bounded shape against the real estimator and the real
frozen budget: the descriptor's size bound, the "do not page unless it is
smaller by a margin" rule, determinism/admission identity, and the incident's
own request shape.
"""
import json
from types import SimpleNamespace

import pytest
from simple_harness import CallId
from simple_harness.contracts.messages import Message, MessageRole

from deskpet.execution.current_tool_pages import (
    CONTROL_TOOLS, MARKER, PAGE_SAVING_DIVISOR, PAGE_WORTH_MIN_BYTES, PREFIX,
    PrimaryContextPageUnavailable, SUMMARY_EXCERPT_BYTES, SUMMARY_MIN_BYTES,
    _settled_tool_tokens, current_tool_allowance, reference, source_content, summary,
    verify_request, worth_paging,
)
from deskpet.execution.primary_context_pages import PAGE_BYTES
from deskpet.sdk_adapters.context_partitions import effective_input_budget, text_tokens
from deskpet.task_scope.protocol import canonical_hash

from tests.execution.test_control_result_bound import (
    CURRENT_TEXT, FLASH_RATIO, HOST_RUN_ID, RUN_ID, WINDOW,
    FakeEffect, FakeStack, admission_facts, flash_calibration, make_projector,  # noqa: F401
    public_body, sized_body,
)

# The target the E memo set for this followup: ~110 token per descriptor instead
# of ~456-500.  ``DESCRIPTOR_TOKEN_CEILING`` is the bound this suite enforces —
# a descriptor that costs more than this has stopped being a saving and the
# whole mechanism degrades back into Incident E.
DESCRIPTOR_TOKEN_CEILING = 150
# Measured floor of the same-shape descriptor on main (1 960 B / 490 token).
MAIN_DESCRIPTOR_BYTES = 1960


def _facts(*, effect_id="effect-" + "c" * 60, tool_name="tool_search", body_bytes=6598):
    """One settled non-control effect and the public facts behind it."""
    filler = max(0, body_bytes - len(public_body({"tag": effect_id, "filler": ""}).encode()))
    value = {"tag": effect_id, "filler": "x" * filler}
    effect = FakeEffect(effect_id=effect_id, tool_name=tool_name,
                        raw_call_id="call_00_" + "d" * 24, value=value)
    stack = FakeStack({effect_id: effect})
    return stack, stack.read_primary_effect_page_facts(RUN_ID, effect_id)


# --------------------------------------------------------------------------
# 1. The descriptor is small, and it is small by construction rather than by
#    accident of this particular body.
# --------------------------------------------------------------------------

def test_bounded_descriptor_costs_about_a_hundred_tokens_whatever_the_body_is():
    sizes = []
    for body_bytes in (1_400, 2_098, 3_014, 6_598, 10_851, 98_817):
        _, facts = _facts(body_bytes=body_bytes)
        descriptor, content = source_content(facts, min_bytes=0)
        assert len(content.encode()) == descriptor["content_bytes"]
        body = summary(descriptor, content)
        sizes.append((len(body.encode()), text_tokens(body)))
        wire = json.loads(body)

        # (a) The excerpt is hard-capped and is still a prefix of the exact body.
        assert len(wire["excerpt"].encode()) <= SUMMARY_EXCERPT_BYTES
        assert content.encode().startswith(wire["excerpt"].encode())

        # (b) Everything ``context_page_in``'s own tool description tells the
        #     model to copy is still published, verbatim.
        assert wire["page_tool"] == "context_page_in"
        assert wire["reference_id"] == reference(descriptor)
        assert wire["source_hash"] == descriptor["content_hash"]

        # (c) …plus what makes the placeholder legible: which tool, how big, how
        #     many pages it would take to read it all back.
        assert wire["source"]["tool_name"] == descriptor["tool_name"]
        assert wire["source"]["content_bytes"] == descriptor["content_bytes"]
        assert wire["pages"] == -(-descriptor["content_bytes"] // PAGE_BYTES)
        assert wire["source"]["effect_id"] == descriptor["effect_id"]

    # The cost is flat in the body size (that was always true) *and* small (that
    # is the fix): on main the same six descriptors were >= 1 960 B / ~490 token.
    assert max(size for size, _ in sizes) <= 2 * min(size for size, _ in sizes)
    assert max(tokens for _, tokens in sizes) <= DESCRIPTOR_TOKEN_CEILING, sizes
    assert max(size for size, _ in sizes) < MAIN_DESCRIPTOR_BYTES // 3, sizes


def test_no_body_below_the_paging_floor_can_ever_be_paged():
    """``PAGE_WORTH_MIN_BYTES`` is only honest while this holds.

    Review N2: proving it on one measured summary would only pin *that*
    descriptor.  The property that actually holds is structural — the excerpt is
    ``min(SUMMARY_EXCERPT_BYTES, body)`` and so is never empty for a small body —
    so exercise it across the id lengths a descriptor can really have.
    """
    assert PAGE_WORTH_MIN_BYTES == SUMMARY_MIN_BYTES * PAGE_SAVING_DIVISOR
    for id_chars in (20, 32, 71):
        for body_bytes in (200, 400, 600, PAGE_WORTH_MIN_BYTES - 1):
            _, facts = _facts(effect_id="e" * id_chars, body_bytes=body_bytes)
            descriptor, content = source_content(facts, min_bytes=0)
            body = summary(descriptor, content)
            assert not worth_paging(len(body.encode()), len(content.encode())), (
                id_chars, body_bytes, len(body.encode()))
    # And the constant is a real lower bound on the production shape.
    _, facts = _facts(body_bytes=6_598)
    descriptor, content = source_content(facts, min_bytes=0)
    body = summary(descriptor, content)
    fixed = len(body.encode()) - len(json.dumps(json.loads(body)["excerpt"],
                                                ensure_ascii=False).encode())
    assert fixed >= SUMMARY_MIN_BYTES, fixed


def test_the_pre_f_e3_wire_shape_is_still_verifiable(tmp_path):
    """Review M1: an upgrade must not close a Run that already paged a body.

    ``admitted_current_page`` re-verifies the *persisted* parent request, and
    ``check_runtime_dependencies`` re-runs that for every prior
    ``context_page_in`` effect on every later turn.  A stored summary in the
    old shape has to keep verifying, or a Run in flight across the upgrade
    fails closed on a formatting change and can never be resumed.
    """
    from deskpet.execution.current_tool_pages import legacy_summary

    stack, facts = _facts(body_bytes=6_598)
    descriptor, content = source_content(facts, min_bytes=0)
    old = legacy_summary(descriptor, content)
    assert old != summary(descriptor, content)
    # Same admission identity: the digest inside reference_id is the same.
    assert json.loads(old)["reference_id"] == json.loads(summary(descriptor, content))["reference_id"]

    message = Message(MessageRole.TOOL, old, name=descriptor["tool_name"],
                      call_id=CallId(descriptor["raw_call_id"]), metadata={"source": MARKER})
    found = verify_request(stack, RUN_ID, (message,))
    assert found == {canonical_hash(descriptor): (descriptor, content)}

    # …and it is still *verified*, not waved through: main's own foreign-source
    # check keeps its position and its error code for the old shape.
    foreign = json.loads(old)
    foreign["source"]["run_id"] = "product-sdk-" + "ff" * 32
    bad = Message(MessageRole.TOOL, json.dumps(foreign, ensure_ascii=False),
                  name=descriptor["tool_name"], call_id=CallId(descriptor["raw_call_id"]),
                  metadata={"source": MARKER})
    with pytest.raises(PrimaryContextPageUnavailable) as exc:
        verify_request(stack, RUN_ID, (bad,))
    assert str(exc.value) == "primary_effect_page_foreign_source"

    tampered = json.loads(old)
    tampered["excerpt"] = tampered["excerpt"][:-5]
    worse = Message(MessageRole.TOOL, json.dumps(tampered, ensure_ascii=False),
                    name=descriptor["tool_name"], call_id=CallId(descriptor["raw_call_id"]),
                    metadata={"source": MARKER})
    with pytest.raises(PrimaryContextPageUnavailable):
        verify_request(stack, RUN_ID, (worse,))


@pytest.mark.asyncio
async def test_paging_never_increases_the_token_count_it_is_supposed_to_reduce(
        tmp_path, flash_calibration):
    """Review N1: the margin is a byte rule, the bound it feeds is a token one.

    ``text_tokens`` charges four *characters* per token outside CJK, so a body
    of 4-byte codepoints is cheap in tokens and expensive in bytes: 1 615 B of
    emoji is 119 token, while its 556 B summary is 131.  The byte margin says
    "page it" and paging it would make ``carried`` grow.  ``replace`` must
    refuse outright, in both the bounded and the ``force_all`` pass.
    """
    body = public_body({"tag": "t", "filler": "\U0001F600" * 380})
    _, facts = _facts(body_bytes=64)
    descriptor, _ = source_content(facts, min_bytes=0)
    descriptor = dict(descriptor, content_bytes=len(body.encode()), content_hash="ab" * 32)
    wire = summary(descriptor, body)
    assert worth_paging(len(wire.encode()), len(body.encode())), "the byte rule alone accepts it"
    assert text_tokens(wire) > text_tokens(body), "…and paging it would cost tokens"

    # End to end: a Run made entirely of such bodies never grows when projected.
    messages = [Message(MessageRole.SYSTEM, "S" * 64), Message(MessageRole.USER, CURRENT_TEXT)]
    effects, sources = {}, []
    for position in range(12):
        effect_id = "effect-%02d%s" % (position, "g" * 60)
        raw_call_id = "call_00_%02d%s" % (position, "h" * 20)
        # Short key, so the body stays in the pathological size band (1 615 B /
        # 119 token up to 1 659 B / 123 token, against a 556 B / 131 token wire).
        value = {"f": "\U0001F600" * (380 + position)}
        content = public_body(value)
        effects[effect_id] = FakeEffect(effect_id=effect_id, tool_name="tool_search",
                                        raw_call_id=raw_call_id, value=value)
        messages.append(Message(MessageRole.ASSISTANT, ""))
        messages.append(Message(MessageRole.TOOL, content, name="tool_search",
                                call_id=CallId(raw_call_id)))
        sources.append(dict(effect_id=effect_id, provider_turn_ordinal=position + 1,
                            state="succeeded", tool_name="tool_search",
                            item_ordinal=len(messages) - 1))
    messages = tuple(messages)
    stack = FakeStack(effects)
    stack.sources = sources
    projector = make_projector(tmp_path, stack)
    request = SimpleNamespace(run_id=SimpleNamespace(value=RUN_ID), provider_turn_ordinal=12)
    for force_all in (False, True):
        projected = await projector(request, messages, force_all=force_all)
        if projected is None:
            continue
        assert _settled_tool_tokens(projected) <= _settled_tool_tokens(messages)
        assert not [m for m in projected if m.metadata and m.metadata.get("source") == MARKER]


# --------------------------------------------------------------------------
# 2. Do not page what paging does not shrink.
# --------------------------------------------------------------------------

def test_margin_rule_refuses_a_body_that_paging_barely_shrinks():
    # The two real losses from attempt 9: a 2 098 B tool_describe -> 2 011 B and
    # a 2 338 B tool_search -> 2 010 B.  Both were "smaller", neither was worth
    # the readable content it cost.
    assert not worth_paging(2_011, 2_098)
    assert not worth_paging(2_010, 2_338)
    # Exactly at the margin is accepted; one byte short of it is not.
    assert worth_paging(600, 1_200)
    assert not worth_paging(600, 1_199)
    # ``force_all`` gives the margin up: once the Host has already decided this
    # turn does not fit, any shrink beats closing the Run.
    assert worth_paging(2_011, 2_098, margin=False)
    assert not worth_paging(2_098, 2_098, margin=False)


@pytest.mark.asyncio
async def test_small_settled_bodies_stay_verbatim_and_the_receipt_says_so(
        tmp_path, flash_calibration):
    """A body the descriptor cannot beat by a margin keeps its bytes."""
    from deskpet.sdk_adapters.context_authority import _current_tool_page_facts

    messages = [Message(MessageRole.SYSTEM, "S" * 64), Message(MessageRole.USER, CURRENT_TEXT)]
    effects, sources = {}, []
    # 12 turns of tiny results plus one big one, so the same-Run bound is over
    # its allowance and really tries to page everything it may.
    rows = [("tool_search", 700)] * 12 + [("tool_search", 40_000)] + [("tool_search", 700)]
    for position, (name, size) in enumerate(rows):
        effect_id = "effect-%02d%s" % (position, "e" * 60)
        raw_call_id = "call_00_%02d%s" % (position, "f" * 20)
        content = sized_body(effect_id, size)
        filler = max(0, size - len(public_body({"tag": effect_id, "filler": ""}).encode()))
        value = {"tag": effect_id, "filler": "x" * filler}
        assert public_body(value) == content
        effects[effect_id] = FakeEffect(effect_id=effect_id, tool_name=name,
                                        raw_call_id=raw_call_id, value=value)
        messages.append(Message(MessageRole.ASSISTANT, ""))
        messages.append(Message(MessageRole.TOOL, content, name=name, call_id=CallId(raw_call_id)))
        sources.append(dict(effect_id=effect_id, provider_turn_ordinal=position + 1,
                            state="succeeded", tool_name=name,
                            item_ordinal=len(messages) - 1))
    messages = tuple(messages)

    stack = FakeStack(effects)
    stack.sources = sources
    projector = make_projector(tmp_path, stack)
    request = SimpleNamespace(run_id=SimpleNamespace(value=RUN_ID), provider_turn_ordinal=13)
    _, metadata = admission_facts()
    assert _settled_tool_tokens(messages) > current_tool_allowance(metadata, provider_turn_ordinal=13)

    projected = await projector(request, messages)
    assert projected is not None
    paged = [m for m in projected if m.metadata and m.metadata.get("source") == MARKER]
    verbatim = [m for m in projected
                if m.role.value == "tool" and not (m.metadata and m.metadata.get("source") == MARKER)]

    # Only the 40 KB body was worth paging.  Every 700 B body kept its bytes even
    # though the bound was over its allowance and walked right past them: its own
    # summary is not smaller than it is by the margin, so paging it would trade
    # readable content for nothing.
    assert [m.content for m in paged] and len(paged) == 1
    assert json.loads(paged[0].content)["source"]["content_bytes"] == 40_000
    assert len(verbatim) == len(rows) - 1
    assert all(len(m.content.encode()) < PAGE_WORTH_MIN_BYTES for m in verbatim)

    receipt = _current_tool_page_facts(projected)
    assert receipt["current_tool_pages"] == 1
    assert receipt["results_kept_verbatim_small"] == len(verbatim)
    assert receipt["descriptor_bytes_saved"] == 40_000 - len(paged[0].content.encode())
    assert receipt["descriptor_bytes_saved"] > 0

    # …and the margin really is the reason: under ``force_all`` — the Host has
    # already decided this turn does not fit — the same bodies do give way,
    # because "smallest form" then beats "readable".
    forced = await projector(request, messages, force_all=True)
    forced_paged = [m for m in forced if m.metadata and m.metadata.get("source") == MARKER]
    assert len(forced_paged) > len(paged)
    assert all(len(m.content.encode())
               < json.loads(m.content)["source"]["content_bytes"] for m in forced_paged)


# --------------------------------------------------------------------------
# 3. Determinism and admission identity are exactly what they were.
# --------------------------------------------------------------------------

def test_summary_is_a_deterministic_function_of_the_body():
    _, facts = _facts(body_bytes=6_598)
    descriptor, content = source_content(facts, min_bytes=0)
    assert summary(descriptor, content) == summary(dict(descriptor), content)
    # The excerpt is a byte prefix, so an ASCII/CJK boundary cannot make it
    # non-deterministic: the same body always yields the same bytes.
    cjk = public_body({"tag": "t", "filler": "边界" * 4000})
    cjk_descriptor = dict(descriptor, content_hash="ab" * 32, content_bytes=len(cjk.encode()))
    once, twice = summary(cjk_descriptor, cjk), summary(cjk_descriptor, cjk)
    assert once == twice
    assert len(json.loads(once)["excerpt"].encode()) <= SUMMARY_EXCERPT_BYTES


def test_reference_identity_still_binds_every_field_the_wire_no_longer_prints():
    """The dropped fields are re-derived, not un-checked."""
    _, facts = _facts(body_bytes=6_598)
    descriptor, content = source_content(facts, min_bytes=0)
    wire = json.loads(summary(descriptor, content))
    assert wire["reference_id"] == PREFIX + canonical_hash(descriptor) + ":0"
    for field in ("run_id", "effect_version", "result_hash",
                  "provider_invocation_id", "provider_response_hash"):
        assert field in descriptor and field not in wire["source"]
        moved = dict(descriptor)
        moved[field] = "tampered" if isinstance(moved[field], str) else 99
        # A different value for a field the wire never prints still produces a
        # different reference_id, so ``verify_request``'s byte comparison sees it.
        assert summary(moved, content) != summary(descriptor, content)
        assert reference(moved) != reference(descriptor)


def test_verify_request_accepts_the_bounded_form_and_rejects_a_tampered_one():
    stack, facts = _facts(body_bytes=6_598)
    descriptor, content = source_content(facts, min_bytes=0)
    message = Message(MessageRole.TOOL, summary(descriptor, content),
                      name=descriptor["tool_name"], call_id=CallId(descriptor["raw_call_id"]),
                      metadata={"source": MARKER})
    found = verify_request(stack, RUN_ID, (message,))
    assert set(found) == {canonical_hash(descriptor)}
    assert found[canonical_hash(descriptor)] == (descriptor, content)

    tampered = json.loads(message.content)
    tampered["source"]["content_bytes"] = 1
    bad = Message(MessageRole.TOOL, json.dumps(tampered, ensure_ascii=False),
                  name=descriptor["tool_name"], call_id=CallId(descriptor["raw_call_id"]),
                  metadata={"source": MARKER})
    with pytest.raises(PrimaryContextPageUnavailable):
        verify_request(stack, RUN_ID, (bad,))

    foreign = json.loads(message.content)
    foreign["source"]["effect_id"] = "effect-" + "9" * 60
    other = Message(MessageRole.TOOL, json.dumps(foreign, ensure_ascii=False),
                    name=descriptor["tool_name"], call_id=CallId(descriptor["raw_call_id"]),
                    metadata={"source": MARKER})
    with pytest.raises(PrimaryContextPageUnavailable):
        verify_request(stack, RUN_ID, (other,))


# --------------------------------------------------------------------------
# 4. The incident's own request shape.
# --------------------------------------------------------------------------

# 16 ``tool_search`` results, 7 ``context_page_in`` carriers and one 4.8 KB
# ``context_route`` — the shape of Run ``5c893a4459`` (T13), whose model issued
# 16 searches and 7 page-ins in one Run while hunting for a file tool.  The
# search bodies are the small ones the old descriptor could not beat: at 1 700 B
# a ~1 990 B summary is *larger* than the body, so on main not one of them can
# be paged and the whole same-Run bound is inert against this shape.
SEARCH_BODY_BYTES = 1_700
# Six of the seven page-in carriers are already at F-E2's floor — in the real
# failing request they had *already* been elided (receipt: 6 notices, 3 921 B),
# so the control lever was spent and nothing more could come from it.  The
# seventh is the newest turn's, which F-E2 never elides.
SPENT_PAGE_IN_BYTES = 640
NEWEST_PAGE_IN_BYTES = 2_270
ROUTE_BODY_BYTES = 4_800
INCIDENT_OVERSHOOT = 1_230  # Run fd4b0849e7: planned 27 982 vs effective 26 752


def build_search_incident(system_chars):
    """The attempt-9 shape: many small searches in a single open group.

    Reproduces the state the Run was actually *in* when it failed, not a fresh
    turn: history is one open group, the control carriers have already been
    elided down to their notices, and what is left is sixteen small
    ``tool_search`` bodies plus one un-elidable ``context_route``.
    """
    messages = [Message(MessageRole.SYSTEM, "S" * system_chars),
                Message(MessageRole.USER, CURRENT_TEXT)]
    effects, sources, turn = {}, [], 0
    rows = [("context_route", ROUTE_BODY_BYTES)]
    spent = 0
    for index in range(16):
        rows.append(("tool_search", SEARCH_BODY_BYTES))
        if index % 3 == 2 and spent < 6:
            rows.append(("context_page_in", SPENT_PAGE_IN_BYTES))
            spent += 1
    while spent < 6:
        rows.append(("context_page_in", SPENT_PAGE_IN_BYTES))
        spent += 1
    rows.append(("context_page_in", NEWEST_PAGE_IN_BYTES))
    for position, (name, size) in enumerate(rows):
        turn += 1
        messages.append(Message(MessageRole.ASSISTANT, ""))
        effect_id = "effect-%02d%s" % (position, "a" * 60)
        raw_call_id = "call_00_%02d%s" % (position, "b" * 20)
        content = sized_body(effect_id, size)
        filler = max(0, size - len(public_body({"tag": effect_id, "filler": ""}).encode()))
        value = {"tag": effect_id, "filler": "x" * filler}
        assert public_body(value) == content
        effects[effect_id] = FakeEffect(effect_id=effect_id, tool_name=name,
                                        raw_call_id=raw_call_id, value=value)
        messages.append(Message(MessageRole.TOOL, content, name=name, call_id=CallId(raw_call_id)))
        sources.append(dict(effect_id=effect_id, provider_turn_ordinal=turn, state="succeeded",
                            tool_name=name, item_ordinal=len(messages) - 1))
    return tuple(messages), effects, sources


@pytest.mark.asyncio
async def test_incident_shape_of_sixteen_small_searches_fits_once_descriptors_are_bounded(
        tmp_path, flash_calibration):
    """Attempt 9: the open group is paged and the Run still does not fit.

    On main every ``tool_search`` body here is smaller than its own ~1 990 B
    descriptor, so ``replace`` refuses all sixteen, ``force_all`` changes
    nothing, history is already one open group, and ``_plan_turn_messages``
    fails closed — exactly ``sdk_context_budget_exceeded planned=27982
    effective=26752 … open_group=19358 full_trim=True``.  With the bounded
    descriptor the same sixteen bodies page for ~570 B each and the turn fits.
    """
    from deskpet.sdk_adapters.context_authority import _current_tool_page_facts, _plan_turn_messages

    effective = effective_input_budget(WINDOW)
    assert effective == 26752

    def plan(messages):
        return _plan_turn_messages(messages, WINDOW, tools=(), exact_tool_sources=True,
                                   model_id="deepseek-v4-flash", provider_turn_ordinal=24,
                                   raise_on_overflow=False, allow_full_group_trim=True)

    # Size the protected prefix from the *raw* messages, so this number is
    # identical on main and here — the only thing that differs between the two
    # trees is what the projector can do about it.
    probe, _, _ = build_search_incident(4)
    _, probe_facts = plan(probe)
    needed = effective + INCIDENT_OVERSHOOT - probe_facts["planned_input_tokens"]
    system_chars = 4 + needed * 4 * 1000 // int(FLASH_RATIO * 1000)
    messages, effects, sources = build_search_incident(system_chars)

    _, before = plan(messages)
    assert before["causal_groups"] == 1, "the incident had one un-trimmable open group"
    assert before["planned_input_tokens"] > effective
    assert before["budget_headroom"] < 0

    searches = [m for m in messages if m.name == "tool_search"]
    assert len(searches) == 16
    assert all(len(m.content.encode()) == SEARCH_BODY_BYTES for m in searches)

    stack = FakeStack(effects)
    stack.sources = sources
    projector = make_projector(tmp_path, stack)
    request = SimpleNamespace(run_id=SimpleNamespace(value=RUN_ID), provider_turn_ordinal=24)

    # The Host's ordered degradation, in order: bounded pass, then force_all.
    projected = await projector(request, messages)
    assert projected is not None
    _, bounded = plan(tuple(projected))
    if bounded["budget_headroom"] < 0:
        projected = await projector(request, messages, force_all=True)
    after, facts = plan(tuple(projected))

    # 1) It fits — and every one of those 1 700 B bodies really did page, which
    #    is the thing main cannot do.
    assert facts["budget_headroom"] >= 0, facts
    assert facts["planned_input_tokens"] <= effective
    paged = [m for m in projected if m.metadata and m.metadata.get("source") == MARKER]
    # Every paged body is one of the 1 700 B searches — the exact size class the
    # old ~1 990 B descriptor could not shrink at all.  The bound stops as soon
    # as the Run is back inside its share, so this is "enough of them", not
    # "all of them": paging a body the model can still read costs it nothing.
    assert len(paged) >= 8, len(paged)
    assert {json.loads(m.content)["source"]["content_bytes"] for m in paged} == {SEARCH_BODY_BYTES}
    assert all(len(m.content.encode()) * PAGE_SAVING_DIVISOR
               <= json.loads(m.content)["source"]["content_bytes"] for m in paged)
    assert max(text_tokens(m.content) for m in paged) <= DESCRIPTOR_TOKEN_CEILING

    # 2) On main these same descriptors would have been larger than the bodies
    #    they replace, so paging could not have helped at all.
    assert MAIN_DESCRIPTOR_BYTES > SEARCH_BODY_BYTES

    # 3) Structure untouched: nothing added, dropped or reordered, and no route
    #    carrier lost a byte (F-E2's contract, which this must compose with).
    assert len(projected) == len(messages)
    assert [(m.role.value, m.name, m.call_id) for m in projected] \
        == [(m.role.value, m.name, m.call_id) for m in messages]
    assert [m.content for m in messages if m.name == "context_route"] \
        == [m.content for m in projected if m.name == "context_route"]

    # 4) Every retained reference re-derives against public authority, and no
    #    control carrier ever entered the page-admission map.
    admitted = verify_request(stack, RUN_ID, projected)
    assert len(admitted) == len(paged)
    assert all(descriptor["tool_name"] not in CONTROL_TOOLS for descriptor, _ in admitted.values())

    # 5) The receipt attributes the saving.
    receipt = _current_tool_page_facts(after)
    assert receipt["current_tool_pages"] == len(paged)
    assert receipt["descriptor_bytes_saved"] >= len(paged) * (SEARCH_BODY_BYTES - 640)

    # 6) Deterministic: same Run state, same bytes.
    again = await projector(request, messages)
    assert [m.content for m in again] == [m.content for m in await projector(request, messages)]
