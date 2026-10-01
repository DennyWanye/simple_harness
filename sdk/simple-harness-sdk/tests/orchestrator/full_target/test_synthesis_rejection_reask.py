# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3i: a synthesiser reply refused for a slip the model can correct is asked once more.

Found by the Grok acceptance episode H-L3-C1-r0 on the 0.12.2 candidate (c7cfedd).  The
synthesiser's only round, on ``method-synthesizer-v2``, wrote a complete, *decodable*
six-step method — facts → reproduce → apply → verify → inspect → summarize, an ordering,
both root criteria linked — and the admission protocol refused it for one line::

    PORT_UNAVAILABLE: step 'summarize' binds input port 'report', which task type
    'code.summarize-review' does not declare

The request package had said ``code.summarize-review: input_ports ["findings"]``; the
model bound ``findings`` *and* ``report``.  P2.3g's second ask covered only a reply the
codec could not read, so the round was concluded ``REJECTED{asks:1}`` on the spot, the
Planner's three rounds all said ``no_applicable_method`` — correctly — and the Mission
ended ``planning_failed`` on 32K tokens.  The reply is in ``fixtures/htn/replies/``.

What this file pins:

1. the fixture decodes and is refused for exactly that line against the shipped code
   domain; drop the one binding and the same registry admits it (``TRIAL_ADMITTED``);
2. which of the protocol's rejection codes are *correctable* — every one names a
   reference or shape the package states the right value for — and which conclude a
   round on the spot; the two sets partition ``RejectionCode``;
3. ``method-synthesizer-v3`` tells the model both kinds of problem travel in
   ``schema_feedback``; v2 keeps its bytes and stays pinnable.

2026-10-01 HTN 精简片 A：方法合成器运行路径（第二次询问、预算不足、
``apply_synthesizer_reply`` 发布到库）整条删除，对应测试随之删除；
只保留纯函数与提示词模板部分。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

_HTN_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "htn"
if str(_HTN_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_HTN_FIXTURES))

import test_method_synthesis as synth  # noqa: E402
from htn_world import method, out, param, ref, step  # noqa: E402

from agent_orchestrator.contracts.htn import TaskForm  # noqa: E402
from agent_orchestrator.planning.htn.registry import (  # noqa: E402
    AdmissionProblem,
    AdmissionStepId,
    AdmissionVerdict,
    RejectionCode,
)
from agent_orchestrator.planning.htn.synthesis import (  # noqa: E402
    CORRECTABLE_REJECTIONS,
    NON_CORRECTABLE_REJECTIONS,
    MethodSynthesizer,
    SynthesisReplyUnreadable,
    SynthesisRequest,
    rejection_is_correctable,
    rejection_problems,
    synthesis_rejection_feedback,
)
from agent_orchestrator.planning.htn.world import build_planning_world  # noqa: E402
from agent_orchestrator.planning.planner import parse_method_proposal  # noqa: E402
from agent_orchestrator.runtime.role_templates import (  # noqa: E402
    METHOD_PROPOSAL_TAG,
    METHOD_SYNTHESIZER,
    METHOD_SYNTHESIZER_V1,
    METHOD_SYNTHESIZER_V1_VERSION,
    METHOD_SYNTHESIZER_V2,
    METHOD_SYNTHESIZER_V2_VERSION,
    METHOD_SYNTHESIZER_V3,
    METHOD_SYNTHESIZER_V3_VERSION,
    METHOD_SYNTHESIZER_V4_VERSION,
    METHOD_SYNTHESIZER_V5_VERSION,
    METHOD_SYNTHESIZER_V6_VERSION,
    METHOD_SYNTHESIZER_VERSION,
    TEMPLATE_VERSIONS,
    template_for,
)
from agent_orchestrator.testing.fixtures import (  # noqa: E402
    method_proposal_step,
)

REPLIES = _HTN_FIXTURES / "replies"


def reply(name: str) -> str:
    return (REPLIES / f"{name}.txt").read_text(encoding="utf-8")


#: ``MethodSynthesisRoundRecorded`` of H-L3-C1-r0, ``orchestrator.db`` ``events``, verbatim.
EPISODE_RECORD = {
    "admitted": False,
    "asks": 1,
    "author": "model",
    "goal_task_id": "task-root",
    "method_id": "code.fix-by-reproduce-patch-verify-explain",
    "problems": [
        "PORT_UNAVAILABLE: step 'summarize' binds input port 'report', which task type "
        "'code.summarize-review' does not declare"
    ],
    "verdict": "REJECTED",
}
EPISODE_PROBLEM = EPISODE_RECORD["problems"][0]
#: The one binding that was wrong, exactly as the model spelled it.
EPISODE_SLIP = ',"report":{"op":"output","step":"verify","port":"report"}'


def c1_reply() -> str:
    return reply("grok_synthesizer_c1r0_round1")


def c1_reply_corrected() -> str:
    """The same reply with the one undeclared binding removed — nothing else."""

    text = c1_reply()
    assert text.count(EPISODE_SLIP) == 1
    return text.replace(EPISODE_SLIP, "")


def _code_world(mission: str = "m-c1"):
    return build_planning_world(mission, domains=("code",), deployed_layers=("code_test",))


# ======================================================================================
# 1. the episode, verbatim, against the shipped code domain
# ======================================================================================


def test_the_c1_reply_decodes_and_is_refused_for_exactly_the_port_the_episode_recorded():
    """The fixture reproduces the episode's refusal byte for byte — and shows why.

    Not an unreadable reply: ``parse_method_proposal`` accepts it, every content hash
    in it is the catalogue's, and the protocol's only complaint is the one line the
    episode recorded.  The package had stated the port list the model got wrong.
    """

    text = c1_reply()
    proposal = parse_method_proposal(text)  # decodes: the codec is not the problem
    assert proposal.method.method_id == EPISODE_RECORD["method_id"]
    assert [item.local_id for item in proposal.method.steps] == [
        "facts",
        "reproduce",
        "apply",
        "verify",
        "inspect",
        "summarize",
    ]
    world = _code_world()
    receipt = MethodSynthesizer(world.registry, world.catalog).accept_response(
        text, policy=world.policy()
    )
    assert receipt.verdict is AdmissionVerdict.REJECTED
    assert list(rejection_problems(receipt)) == EPISODE_RECORD["problems"]
    assert receipt.codes() == {RejectionCode.PORT_UNAVAILABLE}
    # what the package said, read off the same catalogue the request is built from
    summarize = world.catalog.require(ref("code.summarize-review"))
    assert [port.port_key for port in summarize.input_ports] == ["findings"]
    assert [port.port_key for port in summarize.output_ports] == ["summary"]
    written = json.loads(re.search(r"<method_proposal>(.*)</method_proposal>", text, re.S).group(1))
    bound = written["method"]["steps"][-1]["arguments"]
    assert set(bound) == {"findings", "report"}, "findings is declared; report is the slip"
    assert bound["report"] == {"op": "output", "step": "verify", "port": "report"}
    # every ref the model copied really is the catalogue's
    for item in proposal.method.steps:
        assert world.catalog.resolve(item.task_type_ref) is not None, item.local_id
    assert world.catalog.resolve(proposal.method.goal_type_ref) is not None


def test_the_c1_refusal_is_correctable_and_its_feedback_is_the_protocols_own_line():
    world = _code_world()
    receipt = MethodSynthesizer(world.registry, world.catalog).accept_response(
        c1_reply(), policy=world.policy()
    )
    assert rejection_is_correctable(receipt) is True
    feedback = synthesis_rejection_feedback(receipt)
    assert feedback[0] == EPISODE_PROBLEM
    assert len(feedback) == 2
    assert METHOD_PROPOSAL_TAG in feedback[-1] and "注册协议" in feedback[-1]
    assert "method_id" in feedback[-1] and "method_version" in feedback[-1]


def test_the_corrected_c1_reply_is_admitted_on_the_registry_that_refused_the_first():
    """Drop the one binding: same method_id, same version, new hash → TRIAL_ADMITTED.

    On the *same* registry: the refused definition is kept under its own content hash
    (§7.3 wants the refusal to be a fact one can point at) and does not block the
    corrected one — which is what the second ask needs, since a model keeps its
    ``method_id`` and ``method_version`` when it corrects a slip.
    """

    world = _code_world()
    synthesizer = MethodSynthesizer(world.registry, world.catalog)
    first = synthesizer.accept_response(c1_reply(), policy=world.policy())
    assert first.verdict is AdmissionVerdict.REJECTED
    second = synthesizer.accept_response(c1_reply_corrected(), policy=world.policy())
    assert second.verdict is AdmissionVerdict.TRIAL_ADMITTED, rejection_problems(second)
    assert second.problems == ()
    assert second.method_ref.method_id == first.method_ref.method_id
    assert second.method_ref.version == first.method_ref.version == 1
    assert second.method_ref.content_hash != first.method_ref.content_hash
    assert second.method_ref in world.registry.method_refs()
    assert second.status.value == "TRIAL_ADMITTED"


# ======================================================================================
# 2. which refusals are correctable
# ======================================================================================


def test_every_rejection_code_is_classified_once_and_the_reference_slips_are_correctable():
    """The two sets partition the enum; a new code must pick a side at import time."""

    assert CORRECTABLE_REJECTIONS | NON_CORRECTABLE_REJECTIONS == frozenset(RejectionCode)
    assert CORRECTABLE_REJECTIONS.isdisjoint(NON_CORRECTABLE_REJECTIONS)
    assert CORRECTABLE_REJECTIONS == frozenset(
        {
            RejectionCode.PORT_UNAVAILABLE,  # the C1 slip: a port the type does not declare
            RejectionCode.UNKNOWN_TASK_TYPE,  # a ref not offered (goal type or compound step)
            RejectionCode.UNKNOWN_OPERATOR,  # a primitive step's ref not in operators[]
            RejectionCode.UNKNOWN_SCHEMA,  # parameter/output schema ref not the one shown
            RejectionCode.FORM_MISMATCH,  # step form not the type's
            RejectionCode.MALFORMED_DEFINITION,  # argument / link naming an unknown step/parameter
            RejectionCode.ORDERING_CYCLE,  # the partial order the model wrote is cyclic
            RejectionCode.ROOT_COVERAGE_GAP,  # a coverage criterion the package listed is unlinked
        }
    )
    assert NON_CORRECTABLE_REJECTIONS == frozenset(
        {
            RejectionCode.MODEL_CLAIMED_STATUS,  # §18.5 / §6.3 boundary: the refusal is the record
            RejectionCode.ALREADY_REGISTERED,  # registry state, not the reply's shape
            RejectionCode.UNKNOWN_PREDICATE,  # a precondition the package never offered
            RejectionCode.PREDICATE_TYPE_ERROR,  # likewise; I18 is not relaxed by asking again
            RejectionCode.UNKNOWN_CAPABILITY,  # a deployment fact
            RejectionCode.UNBOUNDED_RECURSION,  # a policy limit the package does not state
            RejectionCode.SIZE_BOUND,  # likewise; width overflow is special-cased in P2.3q
        }
    )


def _receipt_with(receipt, *codes: RejectionCode):
    """The same receipt with these problems appended (a mixed step-report)."""

    extra = tuple(
        AdmissionProblem(
            code=code, detail=f"synthetic {code!s}", step=AdmissionStepId.STRUCTURAL_CHECKS
        )
        for code in codes
    )
    return dataclasses.replace(receipt, problems=(*receipt.problems, *extra))


def test_a_refusal_the_model_cannot_correct_is_not_correctable_even_beside_one_it_could():
    env = synth.empty_library_env()
    synthesizer = synth.synthesizer(env)
    # a port slip in the neutral domain: correctable
    slipped = method(
        "synth.goal.slipped",
        "synth.goal",
        parameter_schema="synth.goal.params",
        steps=(
            step("collect", "synth.collect", TaskForm.PRIMITIVE, {"subject": param("subject")}),
            step(
                "deliver",
                "synth.deliver",
                TaskForm.PRIMITIVE,
                {
                    "subject": param("subject"),
                    "result": out("collect", "result"),
                    "report": out("collect", "result"),
                },
            ),
        ),
        links=(("c-root", "deliver", "c-delivered"),),
        finalizer="deliver",
    )
    port = synthesizer.accept_response(method_proposal_step(slipped.to_json()), policy=env.policy())
    assert port.verdict is AdmissionVerdict.REJECTED
    assert port.codes() == {RejectionCode.PORT_UNAVAILABLE}, rejection_problems(port)
    assert rejection_is_correctable(port) is True
    # a capability nobody declares: a deployment fact, concluded on the spot
    gapped = method(
        "synth.goal.gapped",
        "synth.goal",
        parameter_schema="synth.goal.params",
        steps=(
            step(
                "collect",
                "synth.collect",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("synth.capability-nobody-declares",),
            ),
        ),
        links=(("c-root", "collect", "c-collected"),),
        finalizer="collect",
    )
    capability = synthesizer.accept_response(
        method_proposal_step(gapped.to_json()), policy=env.policy()
    )
    assert capability.codes() == {RejectionCode.UNKNOWN_CAPABILITY}, rejection_problems(
        capability
    )
    assert rejection_is_correctable(capability) is False
    # a claimed status: the boundary, never re-asked
    claimed = synthesizer.accept_response(
        method_proposal_step(
            synth.legal_method().to_json(), extras={"registry_status": "ADMITTED"}
        ),
        policy=env.policy(),
    )
    assert claimed.codes() == {RejectionCode.MODEL_CLAIMED_STATUS}
    assert rejection_is_correctable(claimed) is False
    # one uncorrectable problem beside a correctable one: not correctable
    assert rejection_is_correctable(_receipt_with(port, RejectionCode.UNKNOWN_CAPABILITY)) is False
    assert rejection_is_correctable(_receipt_with(port, RejectionCode.ORDERING_CYCLE)) is True
    # an admitted receipt is not a refusal; a refusal with no problems is not correctable
    admitted = synthesizer.accept_response(
        method_proposal_step(synth.legal_method().to_json()), policy=env.policy()
    )
    assert admitted.admitted and rejection_is_correctable(admitted) is False
    assert rejection_is_correctable(dataclasses.replace(port, problems=())) is False
    # the codec's refusal is a different thing and is not a receipt at all
    with pytest.raises(SynthesisReplyUnreadable):
        synthesizer.accept_response("没有块", policy=env.policy())


# ======================================================================================
# 4. the prompt: v3 says what schema_feedback may hold; v2 keeps its bytes
# ======================================================================================


def test_v3_tells_the_model_a_protocol_refusal_may_travel_in_schema_feedback():
    # P2.3j merge: v3 is this slice's prompt and keeps its bytes; the default moved to
    # v4 (v3 plus ``review_feedback``) and, with P2.3k, to v5 (v4 plus "feed the
    # explaining step the change") — each carries every sentence checked here.
    v3 = METHOD_SYNTHESIZER_V3.instructions
    v2 = METHOD_SYNTHESIZER_V2.instructions
    assert METHOD_SYNTHESIZER_V3.prompt_version == METHOD_SYNTHESIZER_V3_VERSION
    assert METHOD_SYNTHESIZER_V3_VERSION == "method-synthesizer-v3"
    assert METHOD_SYNTHESIZER.prompt_version == METHOD_SYNTHESIZER_VERSION
    assert METHOD_SYNTHESIZER_VERSION == "method-synthesizer-v9"  # 2026-09-26: v8 beside v7
    assert "拒绝码" in METHOD_SYNTHESIZER.instructions
    for code in sorted(CORRECTABLE_REJECTIONS, key=str):
        assert str(code) in v3, f"v3 names every correctable code: {code!s}"
    for code in sorted(NON_CORRECTABLE_REJECTIONS, key=str):
        assert str(code) not in v3, f"v3 does not invite a repair of {code!s}"
    assert "拒绝码" in v3 and "拒绝码" not in v2
    assert "保留 method_id 与 method_version" in v3
    assert "input_ports" in v3 and "output_ports" in v3
    assert "没有通过解码" in v2
    # the schema_feedback sentence of v2 is replaced, the rest is v2 byte for byte
    assert v3.startswith("[role:method_synthesizer]")
    assert len(v3) > len(v2)
    assert v3.count("<method_proposal>") == v2.count("<method_proposal>")


def test_v2_keeps_its_bytes_stays_registered_and_is_still_pinnable():
    """§18.5 C8: a new wording is a new version beside the old one, never an edit."""

    assert hashlib.sha256(METHOD_SYNTHESIZER_V2.instructions.encode("utf-8")).hexdigest() == (
        "27ccb23492ef00b73404735a03f51439d2bf0eb1339a6a6b4320950ee8beaccb"
    )
    assert hashlib.sha256(METHOD_SYNTHESIZER_V1.instructions.encode("utf-8")).hexdigest() == (
        "9341ab10390015fb45d528d95dac0af5b29658f0ee9b70060369d607f8f0ae32"
    )
    versions = TEMPLATE_VERSIONS["method_synthesizer"]
    assert set(versions) == {
        METHOD_SYNTHESIZER_V1_VERSION,
        METHOD_SYNTHESIZER_V2_VERSION,
        METHOD_SYNTHESIZER_V3_VERSION,
        METHOD_SYNTHESIZER_V4_VERSION,
        METHOD_SYNTHESIZER_V5_VERSION,
        METHOD_SYNTHESIZER_V6_VERSION,
        "method-synthesizer-v7",
        "method-synthesizer-v8",
        METHOD_SYNTHESIZER_VERSION,
    }
    assert versions[METHOD_SYNTHESIZER_V3_VERSION] is METHOD_SYNTHESIZER_V3
    assert hashlib.sha256(METHOD_SYNTHESIZER_V3.instructions.encode("utf-8")).hexdigest() == (
        "a38309fdb328c929d6ddefa37ff4de294628f0f508dfad82c7a27bb1cd0e6c9c"
    )
    assert versions[METHOD_SYNTHESIZER_V2_VERSION] is METHOD_SYNTHESIZER_V2
    assert versions[METHOD_SYNTHESIZER_VERSION] is METHOD_SYNTHESIZER
    assert (
        template_for(METHOD_SYNTHESIZER, {"method_synthesizer": METHOD_SYNTHESIZER_V2_VERSION})
        is METHOD_SYNTHESIZER_V2
    )
    assert (
        template_for(METHOD_SYNTHESIZER, {"method_synthesizer": METHOD_SYNTHESIZER_V1_VERSION})
        is METHOD_SYNTHESIZER_V1
    )
    assert template_for(METHOD_SYNTHESIZER, None) is METHOD_SYNTHESIZER
    # a fresh request names the default version
    env = synth.empty_library_env()
    request = synth.synthesizer(env).build_request(synth.goal(env), env.capabilities())
    assert isinstance(request, SynthesisRequest)
    assert request.to_json()["role_prompt_version"] == "method-synthesizer-v9"
