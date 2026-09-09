"""Current Run pages: settled public effects, never fabricated terminal S1."""
from __future__ import annotations

import json
from collections.abc import Mapping
from types import MappingProxyType

import aiosqlite

from deskpet.execution.primary_context_pages import (
    PAGE_BYTES, PrimaryContextPageUnavailable, _excerpt, _sha,
)
from deskpet.memory.primary_tool_causality import PrimaryToolCausalityUnavailable
from deskpet.task_scope.protocol import canonical_hash, canonical_json, redact_credential_shapes
from deskpet.sdk_adapters.causal_groups import DEFAULT_LARGE_RESULT_BYTES

PREFIX = "primary-effect-page:v1:"
MARKER = "primary_settled_effect_v1"
# These values contain executable context/recall attestations. Retain their
# complete public carriers under the original budget; never turn them into an
# empty typed attestation or a generic content summary.
CONTROL_TOOLS = frozenset({"context_route", "context_page_in", "task_scope_search", "task_scope_update"})
# HM-AC-6 same-Run accumulation bound. History trimming only bounds *history*:
# the current Run's own settled tool results all live in the single open causal
# group, which is never trimmed, so 25 provider turns of small results grow the
# request without limit (native HM-TO-A6 evidence: 29 same-Run tool messages,
# 68 KiB, every one under the 16 KiB per-result threshold). Once this Run's own
# non-control tool bodies exceed this share of effective_input_budget, the
# OLDEST settled bodies travel as the same content-addressed
# primary_settled_effect_v1 summary + context_page_in reference the >16 KiB rule
# already produces, so the model can page any of them back byte-exactly.
CURRENT_TOOL_BUDGET_DIVISOR = 4

# --- F-E3: the paged descriptor's own cost -------------------------------
# Incident E left the summary itself unbounded in the only direction that
# mattered: its cost is *fixed* (~1 990 B / ~500 token) whatever the body is,
# because it carried a full 1 KiB ``_excerpt`` plus ten descriptor fields.
# Native HM-TO-A6 attempt 9 (`.local-test-evidence/2026-09-09/native-a6-run9/`,
# deepseek-v4-flash, window 32000 -> effective_input_budget 26752) shows the
# consequence directly — four Runs died with ``sdk_context_budget_exceeded``
# while their open group was already almost entirely paged:
#
#   run         planned   paged msgs   descriptor B   original B   desc tokens
#   fd4b0849e7   27 982      13          26 060        72 035        6 550
#   539ca5f03b   27 932      14          28 076        79 175        7 073
#   5c893a4459   27 007      16          32 098        98 817        8 036
#   4996b84db6   26 862       6          12 014        30 737        3 065
#
# 16 descriptors alone are 8 036 token of a 26 752 budget.  The model (flash)
# legitimately issues 13-16 ``tool_search`` calls per Run while hunting for file
# tools, so "13+ paged results" is the normal shape, not an exotic one.
#
# Measured on the same 47 real descriptors, the 1 KiB excerpt buys almost
# nothing: truncated to 128 B it still gives 33 of the 45 distinct bodies a
# distinct prefix; the full 1 024 B reaches only 34.  896 extra bytes per
# message for 1 body in 45.  The first 73 B are the constant
# ``{"error_code":null,"outcome":"succeeded","public_message":null,"value":{"``
# wrapper, so 128 B leaves ~55 B of real payload — in the observed shape that is
# ``{"count":7,"matches":[{"capability_id":"builtin:memory_re``, i.e. enough to
# say which search this was.  The rest is what ``context_page_in`` is for.
SUMMARY_EXCERPT_BYTES = 128
# A summary must be at most this fraction of the body it replaces, or the body
# keeps its bytes.  Evidence: attempt 9 paged a 2 098 B ``tool_describe`` into a
# 2 011 B descriptor (4 % saved) and a 2 338 B ``tool_search`` into 2 010 B
# (14 %) — real losses of readable content for no budget gain.
PAGE_SAVING_DIVISOR = 2
# The *smallest* a bounded summary can be: the fixed part (kind + reference_id
# + source_hash + page_tool + effect_id + tool_name + content_bytes + pages —
# ~415 B for production id lengths, ~339 B + the two ids in general) plus the
# excerpt, which is ``min(SUMMARY_EXCERPT_BYTES, body)`` and therefore never
# empty for a body this small.  Review N2 (2026-09-09): the lower bound does not
# rest on the ~415 B measurement, which a shorter ``effect_id`` would break — it
# rests on that "excerpt is never empty" term, and
# ``test_no_body_below_the_paging_floor_can_ever_be_paged`` proves the property
# directly, across id lengths, instead of pinning one measurement.
SUMMARY_MIN_BYTES = 400
# Below this many bytes ``worth_paging`` can never be satisfied, so a verbatim
# non-control body this small never lost anything to the bound.  Purely
# descriptive in receipts: the rule itself always compares the real summary
# against the real body, and on a ``force_all`` turn the margin is dropped
# entirely, so this number says "below the paging floor", NOT "the margin rule
# is why this one is verbatim" (review S1).
PAGE_WORTH_MIN_BYTES = SUMMARY_MIN_BYTES * PAGE_SAVING_DIVISOR

# --- 事件 AF：offset 必须自解释 -------------------------------------------
# HM-TO-A6 短旅程 run12b 第 13 轮（证据 `.local-test-evidence/2026-09-09/
# native-a6-run12b/primary-ui-9iyw1map/`）：模型对同一个 47 KB 的 read_file 结果
# 发了 16 次 ``context_page_in``，9 成 7 败，7 次全是 ``primary_page_offset_invalid``。
# 没有一次越界，也没有一次是引用失效：正文是中文，48 272 B 里只有 21 227 个
# （44 %）字节位置落在 UTF-8 码点边界上，而模型按 1024 / 8192 的等距步长猜 offset，
# 于是 2048 / 3072 / 20480 / 24576 / 32768 全部落在码点中间被拒。
#
# 拒绝只回 "Requested primary page is unavailable."——没有页大小、没有页起点、
# 没有下一页，模型只能继续猜，直到 react 上限吃掉整个 Run。成功的那 9 次其实都
# 回了 ``next_reference_id``（真实边界 2046 / 9216 / 17407 / 13310 / 29695 /
# 41984），但描述符只印了 ``pages``，从没说过"页起点不是 1024 的倍数"。
#
# 事件 B 的口径：拒绝必须自带可执行的下一步。因此 offset 拒绝改为携带页大小、
# 页数、页起点清单和本 Run 的下一个未读 offset；描述符也直接印出页大小与页数。
# 受理口径一字未改——任何落在码点边界且在正文内的 offset 仍然被接受，只有这样
# 已记录的成功页（其中 8192 / 16384 / 40960 都不在规范页链上）才能原样重放。
PAGE_SIZE = PAGE_BYTES
# 页起点清单的上界：前 N 个 + 最后一个。48 页的正文列 17 个数字约 100 B，
# 远在 ``_MAX_HANDLER_PUBLIC_MESSAGE``（2048）之内。
MAX_LISTED_OFFSETS = 16
# 描述符里顺带印全量页起点的上界（页数 ≤ 此值时才印）。8 个 offset 约 45 B，
# 是 F-E3 字节预算里付得起的；再多就只印 page_size + page_count，让模型按
# 页大小自己推，或在被拒时从拒绝详情里拿完整清单。
DESCRIPTOR_OFFSET_LIMIT = 8
# 拒绝详情里为了算"本 Run 已准入过的最高页"最多回溯多少个 context_page_in 效果。
# 只走失败路径，且每个都要重算一次公共审计事实，所以给一个小而确定的上界。
MAX_SCANNED_PAGE_EFFECTS = 8
# 事件 AF 之前 ``not_admitted`` 是"引用不在本次请求里"的唯一出口，语义上却是
# 两件事：引用形如页引用但本请求没有它（被逐出/从未分页/正文这轮走了原文）。
# 新的稳定码把它讲清楚并给出下一步。跨升级的重放兼容见 ``REJECTION_CODE_ALIASES``。
REFERENCE_UNAVAILABLE_CODE = "primary_page_reference_unavailable"
# 升级前已记录的拒绝码 → 升级后重算出的新码。``primary_dependencies`` 重放旧
# Run 时按这张表比对，理由与 ``legacy_summary`` 完全一致（review M1）：一次纯粹
# 的措辞升级不得把还在飞的 Run 判成 ``primary_page_rejection_mismatch``。
# 只允许"新码 → 它取代的那个旧码"这一个方向，且表是冻结的。
REJECTION_CODE_ALIASES = MappingProxyType({
    REFERENCE_UNAVAILABLE_CODE: "primary_effect_page_not_admitted",
})


def rejection_code_matches(recorded, derived):
    """已记录的拒绝码与重算出的拒绝码是否是同一次拒绝（事件 AF 升级兼容）。"""
    return recorded == derived or REJECTION_CODE_ALIASES.get(derived) == recorded


def page_starts(content):
    """本正文按 ``context_page_in`` 规范翻页时的全部页起点（精确，非估算）。

    从 0 开始反复调用 ``_excerpt`` 并按**实际返回的字节数**前进——和
    ``admitted_current_page`` 计算 ``next_reference_id`` 用的是同一个式子，所以
    这张清单就是"从头一页页翻下去会经过的 offset"，模型照着走一定成功。

    注意它**不是**全部合法 offset：任何落在码点边界上的 offset 都合法（这一点
    没有改，也不能改，否则已记录的页读不回来）。它是合法 offset 的一个确定子集，
    覆盖整份正文且不重不漏，因此拿来当"下一步"是安全的。
    """
    raw = content.encode("utf-8")
    starts, offset = [], 0
    while offset < len(raw):
        starts.append(offset)
        step = len(raw[offset:offset + PAGE_SIZE].decode("utf-8", errors="ignore").encode("utf-8"))
        if step <= 0:  # 防御：起点在码点边界上时不可能发生，但绝不允许死循环
            break
        offset += step
    return starts


def _listed_offsets(starts):
    """页起点清单的有界投影：前 ``MAX_LISTED_OFFSETS`` 个 + 最后一个。"""
    if len(starts) <= MAX_LISTED_OFFSETS + 1:
        return list(starts)
    return list(starts[:MAX_LISTED_OFFSETS]) + [starts[-1]]


# --- F-E2: the same-Run bound for CONTROL results ------------------------
CONTROL_MARKER = "primary_control_result_elided_v1"
# Which control families may lose their body, and which may not.
#
# ``context_route`` MUST NOT: its result body is *re-read out of the outgoing
# request* by ``sdk_adapters.typed_context_use.TypedContextUseAuthority.
# _occurrences``, which scans ``role=tool`` / ``name == "context_route"``
# messages, parses ``value["context_route_receipt"]`` and requires the parsed
# body to equal the effect's public result before it will emit any typed recall
# intent.  Eliding one would silently drop that turn's typed context-use
# attestation instead of failing closed, so route carriers keep their bytes and
# only shrink the way every other message does — by history trimming, which
# never reaches the open group.  It was verified by grep over the whole backend
# that no other consumer reads a tool result *body* out of a provider request by
# tool name; the durable primary transcript, the causal-source reader and the
# v3 scope-source checks all read the SDK Context / ledger, never the request.
STUBBABLE_CONTROL_TOOLS = CONTROL_TOOLS - frozenset({"context_route"})
# Deterministic, frozen, payload-free: how the model re-obtains an equivalent
# and *current* attestation for the family whose body was elided.  Not derived
# from the elided body, so it can never leak it back in.
CONTROL_REFETCH_HINTS = MappingProxyType({
    # Deliberately does not claim the matching primary_settled_effect_v1 summary
    # is still in this request: whether the source body travels paged is decided
    # by a different bound on a different turn, and the Host cannot promise it.
    "context_page_in": "elided; re-page it with a reference_id and source_hash "
                       "from a primary_settled_effect_v1 summary",
    "task_scope_search": "elided; call task_scope_search again with the same arguments",
    "task_scope_update": "elided; call task_scope_update again for a current scope receipt",
})
# HM-TO-A6 attempt 8 (native evidence, provider turn 9 of
# ``product-sdk-28e8fd37…``): 12 same-Run tool results, 41 078 B, of which
# CONTROL carriers are 33 022 B (80.4 %) — task_scope_search 12 389 B (one
# result, resident and unchanged from turn 2 to turn 9), context_route 11 573 B
# (×3) and context_page_in 9 060 B (×4).  The non-control lever was already
# exhausted: all four ``tool_search`` results travelled as
# primary_settled_effect_v1 page summaries (receipt ``current_tool_pages`` = 4,
# ``current_tool_tokens`` = 2016), history was down to the single open group
# (``groups_trimmed_for_budget`` = 2 since turn 4), and turn 10 still planned
# 27 015 against an effective budget of 26 752.  Control results therefore need
# their own share of the budget, measured with the same ratio-scaled integer
# arithmetic as ``current_tool_allowance`` so the two bounds cannot disagree.
CONTROL_RESULT_BUDGET_DIVISOR = 4


def _require(condition, code):
    if not condition:
        raise PrimaryContextPageUnavailable("primary_effect_page_" + code)


def read_effect_facts(uow, run_id, effect_id):
    """Public bounded audit/records establish the actual parent, not an ID recipe."""
    from simple_harness import EffectId, RunId, thaw_json
    from simple_harness.execution.audit import audit_hash, audit_reference
    from simple_harness.execution.provider_invocations import (
        provider_response_from_json, provider_request_from_json, provider_request_fingerprint,
    )
    effect = uow.read_effect(EffectId(effect_id))
    _require(effect is not None and effect.run_id.value == run_id, "effect_missing")
    snapshot = uow.read_run_operation_audit(RunId(run_id), limit=4096)
    _require(snapshot.run_id == run_id and not snapshot.truncated, "audit_incomplete")
    heads = [op for op in snapshot.operations if op.record_type == "head"]
    matches = [op for op in heads if op.kind == "effect"
               and op.effect_id == audit_reference("effect", effect_id)]
    _require(len(matches) == 1, "effect_head_ambiguous")
    head = matches[0]
    _require(head.source_version == effect.version and head.state == effect.state.value
             and head.request_hash == effect.request_hash
             and head.call_id == audit_reference("call", effect.call_id.value)
             and head.call_ordinal == effect.call_ordinal, "effect_head_mismatch")
    parents = [op for op in heads if op.kind == "provider"
               and op.provider_invocation_id == head.provider_invocation_id]
    _require(len(parents) == 1, "parent_ambiguous")
    parent = parents[0]
    found, after = {}, 0
    for _ in range(32):
        receipts = uow.list_provider_projection_receipts(after_sequence=after, limit=256)
        for receipt in receipts:
            _require(receipt.sequence > after, "projection_order")
            after = receipt.sequence
            if (receipt.run_id != run_id or
                    audit_reference("provider", receipt.invocation_id) != head.provider_invocation_id):
                continue
            _require(audit_hash(thaw_json(receipt.payload)) == receipt.payload_hash, "projection_hash")
            invocation = uow.read_provider_invocation(receipt.invocation_id)
            _require(invocation is not None and invocation.run_id.value == run_id, "parent_missing")
            if receipt.invocation_version == invocation.version:
                found[invocation.invocation_id] = invocation
        if len(receipts) < 256:
            break
    else:
        raise PrimaryContextPageUnavailable("primary_effect_page_projection_limit")
    _require(len(found) == 1, "parent_unverifiable")
    invocation = next(iter(found.values()))
    _require(invocation.state.value == parent.state == "succeeded"
             and invocation.version == parent.source_version
             and invocation.response_json is not None and invocation.request_json is not None
             and parent.request_id == audit_reference("request", invocation.request_id.value), "parent_not_settled")
    response_json = thaw_json(invocation.response_json)
    _require(audit_hash(response_json) == parent.result_hash, "parent_result_hash")
    response = provider_response_from_json(response_json)
    _require(0 <= effect.call_ordinal < len(response.tool_calls), "parent_call_missing")
    call = response.tool_calls[effect.call_ordinal]
    _require(call.call_id.value == effect.raw_call_id and call.name == effect.tool_name
             and thaw_json(call.arguments) == thaw_json(effect.arguments), "parent_call_mismatch")
    request = provider_request_from_json(invocation.request_id, thaw_json(invocation.request_json))
    _require(provider_request_fingerprint(request) == invocation.request_fingerprint, "parent_request_hash")
    if effect.result is not None:
        result = effect.result
        raw = dict(call_id=result.call_id.value, outcome=result.outcome.value,
                   value=thaw_json(result.value), error_code=result.error_code,
                   public_message=result.public_message, retryable=result.retryable)
        _require(result.call_id == effect.call_id and audit_hash(raw) == head.result_hash
                 and audit_hash(effect.evidence_ref) == head.evidence_ref_hash, "result_hash")
    return effect, head, invocation, request


def source_content(facts, *, min_bytes=DEFAULT_LARGE_RESULT_BYTES):
    """Rebuild one settled effect's exact public tool body plus its descriptor.

    ``min_bytes`` is the *policy* gate that selects which settled results the
    >16 KiB rule may page; it is not an integrity check. Integrity is the
    descriptor: it is derived from public audit/effect facts only, and the
    retained summary must equal ``summary(descriptor, content)`` byte for byte.
    The same-Run accumulation bound pages smaller settled bodies, and
    ``verify_request`` re-derives whatever the request actually retained, so
    both pass ``min_bytes=0``.
    """
    from simple_harness import thaw_json
    from simple_harness.execution.audit import audit_hash
    effect, head, parent, _ = facts
    _require(effect.terminal and effect.state.value == "succeeded" and effect.result is not None
             and effect.result.outcome.value == "succeeded" and effect.evidence_ref
             and effect.tool_name not in CONTROL_TOOLS, "source_not_pageable")
    result = effect.result
    # Exactly the SDK's public tool message fields, with the same existing Host
    # transcript redaction. Private/internal result fields never enter a page.
    content = redact_credential_shapes(canonical_json(dict(outcome=result.outcome.value,
        value=thaw_json(result.value), error_code=result.error_code,
        public_message=result.public_message)))[0]
    _require(len(content.encode()) > int(min_bytes), "source_not_large")
    descriptor = dict(run_id=effect.run_id.value, effect_id=effect.effect_id.value,
        effect_version=effect.version, result_hash=head.result_hash,
        provider_invocation_id=parent.invocation_id, provider_response_hash=audit_hash(thaw_json(parent.response_json)),
        tool_name=effect.tool_name, raw_call_id=effect.raw_call_id,
        content_hash=_sha(content), content_bytes=len(content.encode()))
    return descriptor, content


def reference(descriptor, offset=0):
    return PREFIX + canonical_hash(descriptor) + ":" + str(offset)


def _summary_excerpt(content):
    """The wire excerpt: a hard-capped, deterministic prefix of the exact body.

    Deliberately *not* ``_excerpt`` (that is the 1 KiB **page** unit used by
    ``admitted_current_page`` and must not shrink — a page is what the model
    reads back, and its size is part of the paging contract).  This is the much
    smaller "which result was this" hint that rides in the retained summary.
    Same shape as ``_excerpt``: a byte prefix, truncated at a UTF-8 boundary by
    ``errors="ignore"``, so it stays a pure deterministic function of
    ``content`` and ``summary(descriptor, content)`` remains byte-reproducible
    by ``verify_request``.
    """
    return content.encode("utf-8")[:SUMMARY_EXCERPT_BYTES].decode("utf-8", errors="ignore")


def summary(descriptor, content):
    """The retained wire form of one paged settled body (F-E3 bounded shape).

    A *projection* of ``descriptor``, not ``descriptor`` itself.  The full
    descriptor stays exactly what it was — it is still the preimage of
    ``reference()`` and still what ``admitted_current_page`` returns as
    ``source`` — so admission identity, the durable page result and its replay
    check are untouched.  What shrinks is only what travels in the request.

    Every dropped field is *re-derived*, never trusted: ``verify_request``
    rebuilds the whole descriptor from public audit/effect facts and compares
    ``message.content`` to this function's output byte for byte, and the
    comparison covers ``reference_id`` = ``canonical_hash(full descriptor)``.
    So ``effect_version`` / ``result_hash`` / ``provider_invocation_id`` /
    ``provider_response_hash`` / ``run_id`` are still bound just as tightly as
    when they were printed — a mismatch in any of them changes the digest
    inside ``reference_id`` and fails ``summary_mismatch``.

    What is kept is what has a *reader*: ``reference_id`` + ``source_hash`` +
    ``page_tool`` are the three fields ``context_page_in``'s own tool
    description tells the model to copy verbatim; ``effect_id`` is the lookup
    key ``verify_request`` needs; ``tool_name`` / ``content_bytes`` /
    ``page_count`` / ``excerpt`` are what makes the placeholder legible to the
    model.  ``content_hash`` is not repeated inside ``source`` — ``source_hash``
    is the same value and is the name the tool schema uses.

    事件 AF 追加的三个字段回答的是"该拿什么 offset"，而不是"这是什么"：
    ``page_size`` 说清 offset 的单位是**字节**（不是页序号、不是行号），
    ``page_count`` 是精确页数（走 :func:`page_starts`，不再是 ceil 估算），
    ``valid_offsets`` 只在页数 ≤ ``DESCRIPTOR_OFFSET_LIMIT`` 时出现——小正文里
    它就是全部页起点，模型一次就能读完，不必先撞一次拒绝。大正文不印清单，
    页起点由拒绝详情或每页的 ``next_reference_id`` 给。
    """
    starts = page_starts(content)
    wire = dict(kind=MARKER,
        source=dict(effect_id=descriptor["effect_id"], tool_name=descriptor["tool_name"],
                    content_bytes=descriptor["content_bytes"]),
        excerpt=_summary_excerpt(content),
        # 读完整份正文需要的 ``context_page_in`` 次数，精确值。
        page_count=len(starts), page_size=PAGE_SIZE,
        reference_id=reference(descriptor), source_hash=descriptor["content_hash"],
        page_tool="context_page_in")
    if len(starts) <= DESCRIPTOR_OFFSET_LIMIT and starts != list(range(0, len(content.encode("utf-8")), PAGE_SIZE)):
        # 只在清单**带信息**时才印：单字节正文的页起点就是 page_size 的整数倍，
        # 已经被 page_size + page_count 完全决定，再印一遍是白花字节（F-E3 的
        # 每条描述符 token 上限没有余量）。真正需要它的恰好是事件 AF 的形态——
        # 多字节正文的页起点不是整数倍，光靠步长推一定推错。
        wire["valid_offsets"] = starts
    return canonical_json(wire)


def legacy_bounded_summary(descriptor, content):
    """事件 AF 之前的 F-E3 有界形态：只读兼容，永不再产出。

    理由与 :func:`legacy_summary` 一模一样（review M1）：``admitted_current_page``
    重新校验的是**已持久化的**父请求，而 ``check_runtime_dependencies`` 每一轮都
    会对此前每个 ``context_page_in`` 效果重跑一遍。升级瞬间还在飞的 Run，其请求里
    存的是 ``pages`` 形态；不接受它，Run 会因为一次纯措辞变更永久 fail closed。

    接受它不放宽任何权限：两种形态都是同一对重新推导出的 ``(descriptor, content)``
    的确定函数，比对仍是对权威的逐字节相等，且 ``canonical_hash(descriptor)``
    这个准入键在两种形态下完全相同。
    """
    return canonical_json(dict(kind=MARKER,
        source=dict(effect_id=descriptor["effect_id"], tool_name=descriptor["tool_name"],
                    content_bytes=descriptor["content_bytes"]),
        excerpt=_summary_excerpt(content),
        pages=-(-int(descriptor["content_bytes"]) // PAGE_BYTES),
        reference_id=reference(descriptor), source_hash=descriptor["content_hash"],
        page_tool="context_page_in"))


def legacy_summary(descriptor, content):
    """The pre-F-E3 wire form: accepted on read, never produced (review M1).

    ``MARKER`` did not change with the bounded shape, and it must not: a page
    reference recorded *before* the upgrade is still a reference to the same
    descriptor and the same body, and ``reference_id`` — the admission digest —
    is byte-identical in both forms.  What changed is only how much of the
    descriptor is printed.

    Without this, an upgrade closes any in-flight Run that had already paged a
    body and paged it back: ``admitted_current_page`` re-verifies the *persisted*
    parent request (``read_effect_facts`` rebuilds it from
    ``provider_invocations.request_json``), and
    ``primary_dependencies.check_runtime_dependencies`` re-runs that for every
    prior ``context_page_in`` effect on every later turn.  A stored old-shape
    summary would stop equalling ``summary(descriptor, content)`` and the Run
    would fail closed on a formatting change, permanently.

    Accepting it admits nothing new.  Both forms are deterministic functions of
    the same re-derived ``(descriptor, content)``; the comparison is still an
    exact byte equality against authority, and both yield the same
    ``canonical_hash(descriptor)`` admission key.
    """
    return canonical_json(dict(kind=MARKER, source=descriptor, excerpt=_excerpt(content),
        reference_id=reference(descriptor), source_hash=descriptor["content_hash"],
        page_tool="context_page_in"))


def worth_paging(body_bytes, content_bytes, *, margin=True):
    """Is turning ``content_bytes`` of body into ``body_bytes`` of summary a win?

    F-E3.  The old rule was only ``summary < body``, which passes for a body
    that is one byte larger than its own summary.  Native HM-TO-A6 attempt 9
    shows what that costs: a 2 098 B ``tool_describe`` result was paged into a
    2 011 B descriptor — 4 % saved, and the model lost the body.  With ``margin``
    a body must be at least ``PAGE_SAVING_DIVISOR`` times its summary before it
    is worth paging at all; below that it keeps its bytes.

    ``margin=False`` is the ordered-degradation form (Incident O ``force_all``):
    once the Host has already decided this turn does not fit, *any* shrink beats
    closing the Run, so it falls back to "smallest form wins".
    """
    if margin:
        return int(body_bytes) * PAGE_SAVING_DIVISOR <= int(content_bytes)
    return int(body_bytes) < int(content_bytes)


def control_stub_content(facts, *, min_bytes=0):
    """Public elision descriptor for one settled CONTROL result body (F-E2).

    Control carriers are deliberately *not* pageable: ``source_content`` refuses
    them (``source_not_pageable``) and keeps refusing them, because a
    ``primary_settled_effect_v1`` reference is an admission token — anything
    reachable through it can be read back byte-exactly by ``context_page_in``,
    and an executable attestation must not become re-servable that way.

    What this builds instead is an *elision notice*, not a summary: the body is
    dropped and replaced by its public content address (``result_hash`` from the
    run's own audit head, plus the sha of the exact public body and its length)
    and the identity of the effect that produced it.  No excerpt, no digest of
    its meaning, nothing derived from the payload beyond a hash — so the notice
    can neither stand in for the attestation nor leak it.  Every field comes
    from public audit/effect facts, so an auditor holding ``(run_id, effect_id)``
    re-derives the identical notice.
    """
    from simple_harness import thaw_json
    effect, head, _parent, _ = facts
    _require(effect.terminal and effect.state.value == "succeeded" and effect.result is not None
             and effect.result.outcome.value == "succeeded"
             and effect.tool_name in STUBBABLE_CONTROL_TOOLS, "control_not_stubbable")
    result = effect.result
    # The same public projection ``source_content`` uses, with the same Host
    # transcript redaction, so the transcript equality check below compares the
    # same bytes for both families.
    content = redact_credential_shapes(canonical_json(dict(outcome=result.outcome.value,
        value=thaw_json(result.value), error_code=result.error_code,
        public_message=result.public_message)))[0]
    _require(len(content.encode()) > int(min_bytes), "control_not_large")
    descriptor = dict(run_id=effect.run_id.value, effect_id=effect.effect_id.value,
        effect_version=effect.version, result_hash=head.result_hash,
        tool_name=effect.tool_name, raw_call_id=effect.raw_call_id,
        content_hash=_sha(content), content_bytes=len(content.encode()))
    return descriptor, content


def control_stub(descriptor):
    """The elision notice's exact bytes: fixed shape, fixed small cost."""
    return canonical_json(dict(kind=CONTROL_MARKER, source=descriptor,
        reason="same_run_control_result_bound",
        refetch=CONTROL_REFETCH_HINTS[descriptor["tool_name"]]))


def verify_control_stubs(stack, run_id, messages):
    """Re-derive every control elision notice in an outgoing request (F-E2).

    Deliberately separate from :func:`verify_request` and deliberately *not*
    returning an admission map: a page reference admits a ``context_page_in``
    read of the source body, and a control body must never become readable that
    way.  This only proves that each notice really describes a settled control
    effect of this Run and carries that effect's own public content address —
    i.e. that the Host elided something it may elide, and said so truthfully.

    Note the deliberate asymmetry with :func:`verify_request` (review N5): the
    notice keeps ``run_id`` on the wire and checks it here, while the F-E3
    bounded page summary drops it and checks the *re-derived* one instead.  Both
    are safe; the difference is a size decision (a notice is emitted a handful
    of times per Run, a summary once per settled result) and F-E2's shape is
    frozen.  Do not "harmonize" this by removing the check below.
    """
    stubs = 0
    for message in messages:
        metadata = message.metadata
        if not isinstance(metadata, Mapping) or metadata.get("source") != CONTROL_MARKER:
            continue
        _require(message.role.value == "tool" and isinstance(message.content, str), "control_stub_shape")
        claimed = json.loads(message.content).get("source", {})
        _require(claimed.get("run_id") == run_id, "control_stub_foreign_source")
        descriptor, _ = control_stub_content(
            stack.read_primary_effect_page_facts(run_id, claimed.get("effect_id")))
        _require(message.content == control_stub(descriptor)
                 and message.name == descriptor["tool_name"]
                 and message.call_id.value == descriptor["raw_call_id"], "control_stub_mismatch")
        stubs += 1
    return stubs


def verify_request(stack, run_id, messages):
    """Rebuild every retained marker against public authority before use."""
    found = {}
    for message in messages:
        metadata = message.metadata
        if not isinstance(metadata, Mapping) or metadata.get("source") != MARKER:
            continue
        _require(message.role.value == "tool" and isinstance(message.content, str), "summary_shape")
        body = json.loads(message.content)
        claimed = body.get("source", {})
        if isinstance(claimed, Mapping) and "run_id" in claimed:
            # Pre-F-E3 wire shape: keep main's check *and its position*, so a
            # rejection recorded before the upgrade replays with the same
            # error code.  ``primary_dependencies`` re-derives a recorded
            # deterministic ``context_page_in`` failure and raises
            # ``primary_page_rejection_mismatch`` when the code differs.
            _require(claimed.get("run_id") == run_id, "foreign_source")
        # F-E3: the bounded wire no longer repeats ``run_id`` (88 B per message,
        # and the only Run identity that may be trusted here is the caller's
        # anyway), so the foreignness check moves onto the *re-derived* facts.
        # It is the same check made stronger: ``read_primary_effect_page_facts``
        # is given the trusted ``run_id``, ``read_effect_facts`` already refuses
        # an effect belonging to another Run, and the assertion below re-states
        # it against authority instead of against model-visible text.
        descriptor, content = source_content(
            stack.read_primary_effect_page_facts(run_id, claimed.get("effect_id")
                                                 if isinstance(claimed, Mapping) else None), min_bytes=0)
        _require(descriptor["run_id"] == run_id, "foreign_source")
        _require(message.content in (summary(descriptor, content),
                                     legacy_bounded_summary(descriptor, content),
                                     legacy_summary(descriptor, content))
                 and message.name == descriptor["tool_name"]
                 and message.call_id.value == descriptor["raw_call_id"], "summary_mismatch")
        found[canonical_hash(descriptor)] = descriptor, content
    return found


def _offset_reason(content, offset):
    """None（可受理）或这次 offset 被拒的确切原因。受理口径与 ``_excerpt`` 一致。"""
    raw = content.encode("utf-8")
    if type(offset) is not int or offset < 0:
        return "offset_negative"
    if offset >= len(raw):
        return "offset_past_end"
    try:
        raw[offset:].decode("utf-8")
    except UnicodeDecodeError:
        # 事件 AF 的真实形态：中文正文里 56 % 的字节位置都落在码点中间。
        return "offset_not_on_character_boundary"
    return None


async def _admitted_next_offset(*, db, stack, sdk_run_id, digest, content, before_sequence):
    """本 Run 已准入过的最高一页之后的下一个 offset（没有已准入页时是 0）。

    只读**权威回执**：Host 的 ``primary_effect_identities`` 给出本 Run 在本次调用
    之前的 ``context_page_in`` 效果顺序，公共审计事实给出每个效果的实参与结果。
    绝不从模型可见的请求文本里读——那是模型写的，拿它算"下一步"等于让模型自证。

    只走拒绝路径，且回溯上限是 ``MAX_SCANNED_PAGE_EFFECTS``；任何一个效果读不出
    权威事实就跳过。提示读不出来时退回 0（"从头翻"），永远不把整次调用变成别的错。
    """
    from simple_harness import thaw_json
    rows = await (await db.execute(
        "SELECT effect_id FROM primary_effect_identities WHERE sdk_run_id=? AND tool_name='context_page_in' "
        "AND sequence<? ORDER BY sequence DESC LIMIT ?",
        (sdk_run_id, before_sequence, MAX_SCANNED_PAGE_EFFECTS))).fetchall()
    highest = None
    for row in rows:
        try:
            prior = stack.read_primary_effect_page_facts(sdk_run_id, row["effect_id"])[0]
            if (prior.result is None or prior.result.outcome.value != "succeeded"):
                continue
            prior_args = thaw_json(prior.arguments)
            prior_ref = prior_args.get("reference_id") if isinstance(prior_args, Mapping) else None
            if not isinstance(prior_ref, str) or not prior_ref.startswith(PREFIX):
                continue
            prior_digest, prior_raw = prior_ref.removeprefix(PREFIX).split(":")
            prior_offset = int(prior_raw)
        except Exception:  # noqa: BLE001 — 提示永远不得改变这次调用的结果
            continue
        if prior_digest == digest and _offset_reason(content, prior_offset) is None:
            highest = prior_offset if highest is None else max(highest, prior_offset)
    if highest is None:
        return 0
    nxt = highest + len(_excerpt(content, highest).encode("utf-8"))
    return nxt if nxt < len(content.encode("utf-8")) else None


async def _reject_bad_offset(*, db, stack, sdk_run_id, digest, content, offset, before_sequence):
    """事件 AF：offset 不可受理时，带着可执行的下一步拒绝。

    稳定码一个字都没变（``primary_page_offset_invalid``），所以
    ``primary_dependencies`` 对旧 Run 的重放比对原样成立；变的只是**这一次**拒绝
    额外携带的 ``detail``——页大小、精确页数、页起点清单（前 16 + 最后一个）、
    本 Run 的下一个未读 offset，以及可以直接复制去重试的完整 reference_id。
    """
    reason = _offset_reason(content, offset)
    if reason is None:
        return
    starts = page_starts(content)
    detail = dict(reason=reason, requested_offset=offset, page_size=PAGE_SIZE,
                  page_count=len(starts), content_bytes=len(content.encode("utf-8")),
                  valid_offsets=_listed_offsets(starts),
                  offsets_listed=len(_listed_offsets(starts)),
                  next_step="offset is a BYTE offset into the source body and must be one of "
                            "valid_offsets; retry context_page_in with retry_reference_id")
    next_offset = await _admitted_next_offset(db=db, stack=stack, sdk_run_id=sdk_run_id,
                                              digest=digest, content=content,
                                              before_sequence=before_sequence)
    detail["next_offset"] = next_offset
    if next_offset is not None:
        detail["retry_reference_id"] = PREFIX + digest + ":" + str(next_offset)
    raise PrimaryContextPageUnavailable("primary_page_offset_invalid", detail)


async def admitted_current_page(*, db, stack, run, sdk_run_id, page_effect, arguments):
    _require(isinstance(arguments, Mapping) and set(arguments) == {"reference_id", "source_hash"}
             and all(isinstance(v, str) for v in arguments.values()), "arguments")
    ref = arguments["reference_id"]
    try:
        digest, raw_offset = ref.removeprefix(PREFIX).split(":")
        offset = int(raw_offset)
    except ValueError as exc:
        raise PrimaryContextPageUnavailable("primary_effect_page_reference") from exc
    _require(ref.startswith(PREFIX) and str(offset) == raw_offset, "reference")
    actual, _, _, parent_request = stack.read_primary_effect_page_facts(sdk_run_id, page_effect.effect_id.value)
    _require(actual == page_effect and actual.tool_name == "context_page_in", "caller_mismatch")
    found = verify_request(stack, sdk_run_id, parent_request.messages)
    if digest not in found:
        # 事件 AF (c)：这不是"offset 不对"，是"这条引用在本次请求里根本不在了"
        # ——正文这轮走了原文、消息被裁掉、或者引用来自另一个 Run。给一个不同的
        # 稳定码和一条可执行的下一步，而不是让模型继续换 offset 重试。
        raise PrimaryContextPageUnavailable(REFERENCE_UNAVAILABLE_CODE, dict(
            reason="reference_not_in_this_request",
            next_step="this reference is not available in this request; re-run the tool that "
                      "produced the result, or copy reference_id/source_hash from a "
                      "primary_settled_effect_v1 summary present in THIS request"))
    descriptor, content = found[digest]
    _require(arguments["source_hash"] == descriptor["content_hash"], "hash_mismatch")
    # Immutable Host identities supply ordering, not result authority. The
    # source must precede this page; prefix dependency checks include its output.
    rows = await (await db.execute("SELECT * FROM primary_effect_identities WHERE sdk_run_id=? AND effect_id IN (?,?)",
        (sdk_run_id, descriptor["effect_id"], page_effect.effect_id.value))).fetchall()
    _require(len(rows) == 2, "order_missing")
    order = {}
    for row in rows:
        identity = dict(host_run_id=run["host_run_id"], sdk_run_id=sdk_run_id,
                        effect_id=row["effect_id"], tool_name=row["tool_name"])
        _require(row["identity_json"] == canonical_json(identity) and row["identity_hash"] == canonical_hash(identity), "order_binding")
        order[row["effect_id"]] = row["sequence"]
    _require(order[descriptor["effect_id"]] < order[page_effect.effect_id.value], "source_not_prior")
    await _reject_bad_offset(db=db, stack=stack, sdk_run_id=sdk_run_id, digest=digest,
                             content=content, offset=offset,
                             before_sequence=order[page_effect.effect_id.value])
    page = _excerpt(content, offset)
    end = offset + len(page.encode())
    return dict(ok=True, kind="primary_current_tool_page_v1", reference_id=ref, source=descriptor,
        source_hash=descriptor["content_hash"], offset=offset, content=page, page_hash=_sha(page),
        next_reference_id=reference(descriptor, end) if end < descriptor["content_bytes"] else None)


def _settled_tool_tokens(messages):
    """Tokens this Run's own non-control settled tool bodies currently occupy.

    Only the current Run's results are physically ``role=tool`` here: settled
    history arrives as quoted ``role=user`` groups (``project_history_group``).
    """
    from deskpet.sdk_adapters.context_partitions import text_tokens
    return sum(text_tokens(m.content) for m in messages
               if m.role.value == "tool" and isinstance(m.content, str)
               and m.name not in CONTROL_TOOLS)


def _control_tool_tokens(messages):
    """Tokens this Run's own CONTROL tool bodies currently occupy (F-E2).

    Counts *every* control family, ``context_route`` included, even though route
    carriers can never be elided: the quantity being bounded is the whole
    control mass, and an honest bound must show when the un-elidable part alone
    already fills it (at which point the loop simply runs out of candidates).
    """
    from deskpet.sdk_adapters.context_partitions import text_tokens
    return sum(text_tokens(m.content) for m in messages
               if m.role.value == "tool" and isinstance(m.content, str)
               and m.name in CONTROL_TOOLS)


def control_result_allowance(metadata, *, provider_turn_ordinal=0):
    """This Run's CONTROL results' share of the frozen effective input budget.

    Same ratio-scaled integer arithmetic as :func:`current_tool_allowance`, for
    the same reason (Incident N): ``_control_tool_tokens`` measures bodies with
    the uncalibrated ``text_tokens`` while ``_plan_turn_messages`` judges them
    through the per-model calibration, so the divisor is scaled by that ratio or
    a calibrated model could pass this bound and still fail the budget.

    Sharing ``CURRENT_TOOL_BUDGET_DIVISOR``'s value (4) is deliberate: control
    and non-control results are each *aimed* at a quarter of the effective
    budget.  Neither is a cap — like the settled bound, this is a best-effort
    drain that stops when the candidates run out, and the un-elidable
    ``context_route`` mass alone can hold the residual above the quarter
    permanently (see ``_control_tool_tokens``).  What the divisor fixes is when
    the drain *starts*, not where it ends; the only real ceiling is
    ``_plan_turn_messages``'s fail-closed check.
    """
    from deskpet.sdk_adapters.context_authority import _resolve_model_id, _resolve_window_tokens
    from deskpet.sdk_adapters.context_partitions import (
        calibration_for_model, effective_input_budget, window_tokens_for,
    )
    mapping = metadata if isinstance(metadata, Mapping) else {}
    model_id = _resolve_model_id(mapping)
    window = window_tokens_for(_resolve_window_tokens(mapping), model_id)
    ratio = calibration_for_model(model_id).ratio(provider_turn_ordinal)
    scale = max(1, round(CONTROL_RESULT_BUDGET_DIVISOR * ratio * 1000))
    return effective_input_budget(window) * 1000 // scale


def current_tool_allowance(metadata, *, provider_turn_ordinal=0):
    """The current Run's share of the frozen effective input budget.

    Incident N: ``_settled_tool_tokens`` measures bodies with the uncalibrated
    ``text_tokens``, while ``_plan_turn_messages`` now judges the same bodies
    through the per-model calibration.  Divide the allowance by that same ratio
    so the two stay in proportion — otherwise a calibrated model could pass this
    bound and still fail the budget, i.e. fail closed where paging had room left.
    """
    from deskpet.sdk_adapters.context_authority import _resolve_model_id, _resolve_window_tokens
    from deskpet.sdk_adapters.context_partitions import (
        calibration_for_model, effective_input_budget, window_tokens_for,
    )
    mapping = metadata if isinstance(metadata, Mapping) else {}
    model_id = _resolve_model_id(mapping)
    # Same window resolution as _plan_turn_messages, so the two bounds cannot
    # disagree about which tier this Run is in.
    window = window_tokens_for(_resolve_window_tokens(mapping), model_id)
    ratio = calibration_for_model(model_id).ratio(provider_turn_ordinal)
    # Integer arithmetic end to end: a float divide could shift the bound by a
    # token purely from representation.
    scale = max(1, round(CURRENT_TOOL_BUDGET_DIVISOR * ratio * 1000))
    return effective_input_budget(window) * 1000 // scale


class CurrentToolProjector:
    def __init__(self, path, stack_getter):
        self.path, self.stack_getter = path, stack_getter

    async def __call__(self, request, messages, *, force_all=False):
        """Project this Run's settled tool results onto page references.

        ``force_all`` is the first step of the Host's ordered budget degradation
        (Incident O, 2026-09-09).  The normal pass pages only what the >16 KiB
        rule and the same-Run allowance require, which is right: a body the model
        can still read costs it nothing.  When the assembled turn does not fit
        even so, the caller comes back with ``force_all=True`` and *every*
        pageable settled body goes — including the newest provider turn's, which
        the bounded pass deliberately protects.  That is a real loss (the model
        has to call ``context_page_in`` to read back what it just produced) and
        it is still strictly better than the alternative at this point, which is
        closing the Run.  Control carriers are never *paged* either way — F-E2
        elides the oldest of them instead (see ``_project``), and the newest
        provider turn's control results are never elided at all, not even under
        ``force_all``: the model must be able to read what it just got.
        """

        large = [m for m in messages if m.role.value == "tool"
                 and isinstance(m.content, str) and len(m.content.encode()) > DEFAULT_LARGE_RESULT_BYTES]
        settled = [m for m in messages if m.role.value == "tool"
                   and isinstance(m.content, str) and m.name not in CONTROL_TOOLS]
        controls = [m for m in messages if m.role.value == "tool"
                    and isinstance(m.content, str) and m.name in STUBBABLE_CONTROL_TOOLS]
        if not large and not settled and not controls:
            return None
        pageable = [m for m in large if m.name not in CONTROL_TOOLS]
        stack = self.stack_getter()
        run_id = request.run_id.value
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT r.host_run_id,r.subject FROM foreground_runs r "
                "JOIN foreground_run_sdk_bindings b USING(host_run_id) "
                "WHERE b.sdk_run_id=?", (run_id,))).fetchall()
        start, _ = stack.read_primary_dependency_facts(run_id)
        admission = start.get("input", {})
        metadata = admission.get("context_metadata", {})
        if not rows and "visibility_dependencies" not in metadata:
            # This authority is also composed for non-primary SDK callers.
            # A trusted Host lookup plus absence of primary admission selects
            # their existing planner, not a fabricated primary source grant.
            return None
        allowance = current_tool_allowance(
            metadata,
            provider_turn_ordinal=int(getattr(request, "provider_turn_ordinal", 0) or 0),
        )
        over_bound = _settled_tool_tokens(messages) > allowance
        control_allowance = control_result_allowance(
            metadata,
            provider_turn_ordinal=int(getattr(request, "provider_turn_ordinal", 0) or 0),
        )
        control_over_bound = bool(controls) and _control_tool_tokens(messages) > control_allowance
        if not large and not over_bound and not control_over_bound and not force_all:
            # Nothing to page: leave the existing generic planner untouched so
            # this turn's request bytes stay exactly what they were before.
            return None
        try:
            return await self._project(messages, stack=stack, run_id=run_id, rows=rows,
                admission=admission, metadata=metadata, pageable=pageable,
                over_bound=over_bound, allowance=allowance, force_all=force_all,
                control_over_bound=control_over_bound, control_allowance=control_allowance)
        except (PrimaryContextPageUnavailable, PrimaryToolCausalityUnavailable):
            if large:
                # A >16 KiB result must never travel raw: this turn already
                # reached here before the bound existed, so keep its original
                # fail-closed behaviour untouched.
                raise
            # Accumulation-bound-only turn — a path that used to return early.
            # Without public causal authority we cannot page anything, but the
            # turn ran fine before, so leave it exactly as it was;
            # _plan_turn_messages still fails closed on a genuine overflow.
            return None

    async def _project(self, messages, *, stack, run_id, rows, admission, metadata,
                       pageable, over_bound, allowance, force_all=False,
                       control_over_bound=False, control_allowance=0):
        from simple_harness.contracts.messages import Message
        from deskpet.sdk_adapters.composition import project_primary_transcript
        from deskpet.sdk_adapters.context_partitions import text_tokens

        _require(len(rows) == 1 and metadata.get("root_run_id") == rows[0]["host_run_id"], "host_run_missing")
        run = rows[0]
        current_text = admission.get("input", {}).get("text")
        admitted_users = [m.get("content") for m in admission.get("messages", ()) if m.get("role") == "user"]
        _require(isinstance(current_text, str) and current_text and admitted_users
                 and admitted_users[-1] == current_text
                 and admission.get("turn", {}).get("text") == current_text, "current_input_missing")
        if not pageable and not over_bound and not control_over_bound and not force_all:
            return messages  # real primary control carriers keep complete bytes
        transcript = project_primary_transcript(messages, current_text=current_text)
        sources = await stack.read_primary_tool_causal_sources(db_path=self.path, host_run_id=run["host_run_id"],
            run_id=run_id, subject=run["subject"], current_text=current_text, messages=transcript)
        # The public causal reader verifies the complete suffix before ordinal
        # mapping. A repeated raw call ID alone never selects an effect.
        anchors = [i for i, m in enumerate(messages) if m.role.value == "user" and m.content == current_text]
        anchor = anchors[-1]
        indices = [i for i in range(anchor, len(messages)) if messages[i].role.value != "system"]
        output = list(messages)

        def replace(index, source, *, min_bytes, margin=True):
            """Swap one settled body for its content-addressed page summary."""
            message = messages[index]
            descriptor, content = source_content(
                stack.read_primary_effect_page_facts(run_id, source["effect_id"]), min_bytes=min_bytes)
            _require(transcript[source["item_ordinal"] - 1]["content"] == content, "transcript_mismatch")
            body = summary(descriptor, content)
            saved = text_tokens(content) - text_tokens(body)
            if saved <= 0 or not worth_paging(len(body.encode()), len(content.encode()), margin=margin):
                # F-E3: not merely "not smaller" — not smaller *by a margin*.
                # Paging a body into a summary of comparable size trades readable
                # content for no budget, so the body stays.  Under ``force_all``
                # the caller drops the margin and only "smaller" is required.
                #
                # Review N1 (2026-09-09): the margin is a *byte* rule while the
                # bound it feeds is a *token* one, and ``text_tokens`` prices CJK
                # at one token per character.  A body of multibyte codepoints can
                # therefore be smaller in bytes and larger in tokens once paged,
                # which would make ``carried`` grow instead of shrink.  Requiring
                # a positive token saving as well makes that impossible: the
                # accumulation loop is now monotone by construction.
                return 0
            output[index] = Message(message.role, body, name=message.name,
                call_id=message.call_id, metadata={"source": MARKER})
            return saved

        def elide(index, source):
            """Swap one settled CONTROL body for its public elision notice."""
            message = messages[index]
            descriptor, content = control_stub_content(
                stack.read_primary_effect_page_facts(run_id, source["effect_id"]))
            _require(transcript[source["item_ordinal"] - 1]["content"] == content, "transcript_mismatch")
            body = control_stub(descriptor)
            if len(body.encode()) >= len(content.encode()):
                return 0  # a body smaller than its own notice saves nothing
            output[index] = Message(message.role, body, name=message.name,
                call_id=message.call_id, metadata={"source": CONTROL_MARKER})
            return text_tokens(content) - text_tokens(body)

        bounded, control = [], []
        for source in sources:
            index = indices[source["item_ordinal"] - 1]
            message = messages[index]
            if message.role.value != "tool" or not isinstance(message.content, str):
                continue
            if message.name in CONTROL_TOOLS:
                # Executable context/recall attestations are never *paged*: a
                # page reference is an admission token for reading the source
                # body back byte-exactly.  Only the families that nothing
                # re-reads out of the request may be elided (F-E2); a route
                # carrier is not one of them and keeps its full bytes here.
                if message.name in STUBBABLE_CONTROL_TOOLS:
                    control.append((index, source))
                continue
            if len(message.content.encode()) > DEFAULT_LARGE_RESULT_BYTES:
                replace(index, source, min_bytes=DEFAULT_LARGE_RESULT_BYTES)
            else:
                bounded.append((index, source))
        # Never the results of the newest provider turn: those are what the
        # in-flight tool calls just produced and the model is still acting on.
        # Only a settled *succeeded* effect has a pageable public body, so a
        # failed/rejected/partial result simply keeps its own bytes instead of
        # failing the whole Run.  Under ``force_all`` the newest batch loses that
        # protection too — see ``__call__`` — but a non-succeeded effect still
        # has no public body to page, so that half of the rule is not a policy
        # choice and stays.
        newest = max((source["provider_turn_ordinal"] for _, source in bounded), default=0)
        candidates = sorted(((index, source) for index, source in bounded
                             if (force_all or source["provider_turn_ordinal"] < newest)
                             and source["state"] == "succeeded"),
                            key=lambda item: item[0])
        carried = _settled_tool_tokens(output)
        for index, source in candidates:
            if carried <= allowance and not force_all:
                break
            try:
                # F-E3: the margin holds for the ordinary bound; only the
                # already-declared "this turn does not fit" pass gives it up.
                carried -= replace(index, source, min_bytes=0, margin=not force_all)
            except PrimaryContextPageUnavailable:
                continue  # this body is not pageable; it keeps its own bytes
        # F-E2: the same bound for CONTROL results, with three differences.
        # (1) The oldest bodies are *elided*, not paged — no admission token is
        #     minted for an executable attestation.
        # (2) The newest provider turn keeps its control bodies even under
        #     ``force_all``: whatever this Run just routed, searched or paged in
        #     is what the model is acting on right now, and eliding it would
        #     make the very next turn incoherent rather than merely poorer.
        # (3) ``context_route`` is out of ``control`` entirely (see
        #     STUBBABLE_CONTROL_TOOLS), so it never reaches this loop.
        # Everything else matches the settled-body bound: oldest first by
        # message index, succeeded effects only, per-item failures skipped, and
        # a notice that is not smaller than the body it replaces is not applied.
        # Review MUST-FIX (2026-09-09): the protected turn must be derived from
        # *all* causal sources, not from the stubbable subset.  Taking it from
        # ``control`` alone means "the most recent turn that happened to produce
        # an elidable control result, however stale", which disables the bound
        # in exactly the incident's shape: a Run whose only elidable carrier is
        # one turn-1 ``task_scope_search`` would have ``newest == 1`` forever
        # and never elide anything, not even under ``force_all``.
        newest_turn = max((source["provider_turn_ordinal"] for source in sources), default=0)
        control_candidates = sorted(((index, source) for index, source in control
                                     if source["provider_turn_ordinal"] < newest_turn
                                     and source["state"] == "succeeded"),
                                    key=lambda item: item[0])
        carried_control = _control_tool_tokens(output)
        for index, source in control_candidates:
            if carried_control <= control_allowance and not force_all:
                break
            try:
                carried_control -= elide(index, source)
            except PrimaryContextPageUnavailable:
                continue  # not elidable; this carrier keeps its own bytes
        return tuple(output)
