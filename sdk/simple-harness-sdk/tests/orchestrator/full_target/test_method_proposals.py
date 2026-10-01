# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""A method the Planner proposes: the system's side (§7.3 source 4, §18.5 C8).

Three properties, and one absence:

1. **The context hands over no authority.**  It describes the goal, the criteria to
   cover, the operators this deployment really registers and the applicability
   reports of the methods that did not fit — and carries none of the fields the
   system binds.  ``SYSTEM_BOUND_FIELDS`` is imported from the module that owns it,
   so the context and ``planning.planner``'s ingress cannot drift apart.
2. **A proposal goes through the same six steps as a human's.**  ``admit_proposal``
   hands the decoded proposal to ``MethodRegistry.admit``; a legal proposal reaches
   ``TRIAL_ADMITTED`` and nothing further.
3. **A model may not award itself a status or an authorship.**  The declared
   ``registry_status`` travels with the submission precisely so the protocol can
   *refuse* it and record the attempt — it is never normalised away — and the author
   on this path is fixed at MODEL, with no way for a caller to say otherwise.

The absence: nothing here promotes.  Past ``TRIAL_ADMITTED`` lies the offline
evaluation, and the registry answers ``PROMOTION_NOT_AVAILABLE`` for every target.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any

import pytest

_HTN_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "htn"
if str(_HTN_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_HTN_FIXTURES))

from htn_world import (  # noqa: E402
    Env,
    load_proposal,
    method,
    param,
    ref,
    seed_env,
    step,
    task_binding,
)

from agent_orchestrator.contracts.htn import (  # noqa: E402
    MethodRegistryStatus,
    RegistryAuthor,
    TaskForm,
)
from agent_orchestrator.contracts.models import ContractError  # noqa: E402
from agent_orchestrator.planning.htn import method_proposals  # noqa: E402
from agent_orchestrator.planning.htn.applicability import (  # noqa: E402
    ApplicabilityReport,
    ApplicabilityStatus,
    assess_method,
)
from agent_orchestrator.planning.htn.method_proposals import (  # noqa: E402
    PROPOSAL_AUTHOR,
    TERMINAL_STATUSES,
    ApplicabilityNote,
    MethodProposalContext,
    OperatorOffer,
    admit_proposal,
    authority_claims,
    build_context,
    goal_type_ref,
)
from agent_orchestrator.planning.htn.registry import (  # noqa: E402
    AdmissionVerdict,
    MethodProposal,
    RejectionCode,
)
from agent_orchestrator.planning.planner import SYSTEM_BOUND_FIELDS  # noqa: E402

MISSION = "mission-1"


# --------------------------------------------------------------------------------------
# A small deployment: one compound goal, two registered operators, no method for it
# --------------------------------------------------------------------------------------


def empty_library_env() -> Env:
    env = Env(mission=MISSION)
    env.register_type(
        "synth.goal",
        form=TaskForm.COMPOUND,
        parameters=(("subject", "string"),),
        criteria=("c-root",),
        domain="synth",
    )
    env.register_type(
        "synth.collect",
        parameters=(("subject", "string"),),
        outputs=(("result", "synth.result"),),
        capabilities=("synth.read",),
        domain="synth",
    )
    env.register_type(
        "synth.deliver",
        parameters=(("subject", "string"),),
        inputs=(("result", "synth.result", True),),
        outputs=(("receipt", "synth.receipt"),),
        capabilities=("synth.send",),
        domain="synth",
    )
    return env


def goal(env: Env) -> Any:
    return task_binding(
        env, "synth.goal", task_id="task-root", obligation="obl-root", parameters={"subject": "a"}
    )


def context(env: Env, binding: Any = None, **options: Any) -> MethodProposalContext:
    capabilities = options.pop("capabilities", None) or env.capabilities()
    return build_context(
        goal(env) if binding is None else binding, capabilities, env.registry,
        catalog=env.catalog, **options)


def legal_method(method_id: str = "synth.collect-then-deliver") -> Any:
    return method(
        method_id,
        "synth.goal",
        parameter_schema="synth.goal.params",
        steps=(
            step(
                "collect",
                "synth.collect",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("synth.read",),
            ),
            step(
                "deliver",
                "synth.deliver",
                TaskForm.PRIMITIVE,
                {
                    "subject": param("subject"),
                    "result": {"op": "output", "step": "collect", "port": "result"},
                },
                capabilities=("synth.send",),
            ),
        ),
        ordering=(("collect", "deliver"),),
        links=(("c-root", "deliver", "c-delivered"),),
        finalizer="deliver",
    )


def proposal(contract: Any, **extra: Any) -> MethodProposal:
    return MethodProposal.from_json(
        {"method": contract.to_json(), "rationale": "it covers the root", **extra})


def admit(env: Env, submitted: MethodProposal) -> Any:
    return admit_proposal(submitted, registry=env.registry, policy=env.policy())



# ======================================================================================
# 1. The context: typed, no authority
# ======================================================================================


def test_the_request_names_the_goal_and_the_criteria_to_cover() -> None:
    env = empty_library_env()
    request = context(env)
    assert request.goal_task_id == "task-root"
    assert request.obligation_id == "obl-root"
    assert request.goal_signature["signature_id"] == "synth.goal"
    assert request.required_criteria == ("req-1",)
    assert request.goal_parameters == {"subject": "a"}


def test_the_request_offers_only_the_operators_the_deployment_registers() -> None:
    env = empty_library_env()
    request = context(env)
    offered = {item.task_type_id for item in request.operators}
    assert offered == {"synth.collect", "synth.deliver"}
    assert all(item.form == str(TaskForm.PRIMITIVE) for item in request.operators)


def test_the_request_copies_the_operator_refs_verbatim() -> None:
    env = empty_library_env()
    request = context(env)
    offer = next(item for item in request.operators if item.task_type_id == "synth.collect")
    declared = env.catalog.require(ref("synth.collect")).task_type_ref
    assert (offer.version, offer.content_hash) == (declared.version, declared.content_hash)


def test_an_unhealthy_capability_is_offered_with_its_unavailability_stated() -> None:
    env = empty_library_env()
    request = context(env, capabilities=env.capabilities(unavailable=("synth.send",)))
    deliver = next(item for item in request.operators if item.task_type_id == "synth.deliver")
    assert deliver.unavailable_capabilities == ("synth.send",)
    assert deliver.available is False
    assert request.unavailable_capabilities == ("synth.send",)
    assert {item.task_type_id for item in request.operators if item.available} == {"synth.collect"}


def test_a_domain_filter_narrows_the_operator_offers() -> None:
    """The seed library ships two domains; a request may ask for only one."""

    env = seed_env(MISSION)
    compound = task_binding(env, "code.fix-failing-test", task_id="t-1", obligation="o-1")
    scoped = context(env, compound, domain="code")
    everything = context(env, compound)
    assert scoped.operators
    assert all(item.domain in (None, "code") for item in scoped.operators)
    assert any(item.domain == "appworld" for item in everything.operators)
    assert len(scoped.operators) < len(everything.operators)


def test_a_goal_type_the_catalogue_declares_is_found_for_candidate_retrieval() -> None:
    env = seed_env(MISSION)
    compound = task_binding(env, "code.fix-failing-test", task_id="t-1", obligation="o-1")
    found = goal_type_ref(env.catalog, compound.goal_signature)
    assert found is not None
    assert found.id == "code.fix-failing-test"
    assert env.registry.candidates_for(found, mission_id=MISSION)


def test_a_goal_type_nobody_declares_has_no_candidates_to_retrieve() -> None:
    env = empty_library_env()
    assert goal_type_ref(env.catalog, goal(env).goal_signature) is not None
    other = task_binding(env, "synth.goal", task_id="t-2", obligation="o-2")
    request = context(env, other, mission_id=MISSION)
    assert request.rejected_methods == ()
    assert request.suggested_method_refs == ()


def test_the_request_carries_no_field_the_system_binds() -> None:
    env = empty_library_env()
    request = context(env)
    assert authority_claims(request.to_json()) == ()
    assert set(request.to_json()) & SYSTEM_BOUND_FIELDS == set()


def test_the_request_tells_the_model_which_fields_are_forbidden() -> None:
    env = empty_library_env()
    request = context(env)
    assert set(request.forbidden_fields) == SYSTEM_BOUND_FIELDS
    assert "registry_status" in request.forbidden_fields
    assert "manager_epoch" in request.forbidden_fields


def test_a_request_that_carried_an_authority_field_is_refused_at_construction() -> None:
    with pytest.raises(ContractError, match="system-bound"):
        MethodProposalContext(
            goal_task_id="t",
            obligation_id="o",
            goal_signature={"signature_id": "g", "registry_status": "ADMITTED"},
        )


def test_a_domain_parameter_named_like_an_authority_field_is_a_value_not_a_claim() -> None:
    """``planning.planner`` draws the same line: only structural keys are refused."""

    request = MethodProposalContext(
        goal_task_id="t",
        obligation_id="o",
        goal_signature={"signature_id": "g"},
        goal_parameters={"scope": "the whole repository", "manager_epoch": "n/a"},
    )
    assert authority_claims(request.to_json()) == ()
    assert request.goal_parameters["scope"] == "the whole repository"


def test_authority_claims_finds_a_nested_structural_claim() -> None:
    payload = {"operators": [{"task_type_ref": {"id": "x"}, "provenance": "system"}]}
    assert authority_claims(payload) == ("operators.[0].provenance",)


def test_a_primitive_goal_is_not_something_to_propose_a_method_for() -> None:
    env = empty_library_env()
    primitive = task_binding(env, "synth.collect", task_id="t-leaf", obligation="o-leaf")
    with pytest.raises(ContractError, match="compound goal"):
        context(env, primitive)


def test_build_context_refuses_something_that_is_not_a_binding() -> None:
    env = empty_library_env()
    with pytest.raises(ContractError, match="TaskSemanticBindingV1"):
        build_context(object(), env.capabilities(), env.registry, catalog=env.catalog)  # type: ignore[arg-type]


def test_build_context_refuses_something_that_is_not_a_capability_snapshot() -> None:
    env = empty_library_env()
    with pytest.raises(ContractError, match="CapabilitySnapshot"):
        build_context(goal(env), object(), env.registry, catalog=env.catalog)  # type: ignore[arg-type]


def test_an_empty_library_offers_no_rejected_methods_to_explain() -> None:
    env = empty_library_env()
    request = context(env, mission_id=MISSION)
    assert request.rejected_methods == ()


def test_a_method_that_did_not_apply_is_reported_on_its_own_axis() -> None:
    env = empty_library_env()
    env.register_predicate("synth.ready", parameters=(("subject", "string"),))
    contract = method(
        "synth.guarded",
        "synth.goal",
        parameter_schema="synth.goal.params",
        applicable=(
            {
                "op": "predicate",
                "predicate_ref": ref("synth.ready").to_json(),
                "arguments": {"subject": param("subject")},
            },
        ),
        steps=(
            step(
                "collect",
                "synth.collect",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("synth.read",),
            ),
        ),
        links=(("c-root", "collect", "c-collected"),),
        finalizer="collect",
    )
    assert env.admit(contract).admitted
    report = assess_method(
        goal(env), contract, env.snapshot(), env.capabilities(), registry=env.predicates
    )
    assert not report.applicable
    request = context(env, reports={"synth.guarded@1": report}, mission_id=MISSION)
    assert [item.method_id for item in request.rejected_methods] == ["synth.guarded"]
    note = request.rejected_methods[0]
    assert note.status == str(report.status)
    assert note.needs_evidence == tuple(report.needs_evidence)


def test_an_applicable_method_is_not_reported_as_rejected() -> None:
    env = empty_library_env()
    contract = legal_method()
    assert env.admit(contract).admitted
    applicable = ApplicabilityReport(
        status=ApplicabilityStatus.APPLICABLE,
        truth=assess_method(
            goal(env), contract, env.snapshot(), env.capabilities(), registry=env.predicates
        ).truth,
    )
    request = context(env, reports={f"{contract.method_id}@1": applicable}, mission_id=MISSION)
    assert request.rejected_methods == ()


def test_the_note_keeps_the_four_axes_apart() -> None:
    note = ApplicabilityNote(
        method_id="m",
        method_version=1,
        content_hash="a" * 64,
        status="CAPABILITY_UNAVAILABLE",
        truth="UNKNOWN",
        unmet_capabilities=("tool.x",),
        needs_evidence=("digest-1",),
        conflicts=("c",),
        type_errors=("t",),
    )
    payload = note.to_json()
    assert payload["unmet_capabilities"] == ["tool.x"]
    assert payload["needs_evidence"] == ["digest-1"]
    assert payload["conflicts"] == ["c"]
    assert payload["type_errors"] == ["t"]


def test_the_offer_json_states_availability_explicitly() -> None:
    offer = OperatorOffer(
        task_type_id="x",
        version=1,
        content_hash="b" * 64,
        form="primitive",
        statement="s",
        required_capabilities=("cap",),
        unavailable_capabilities=("cap",),
    )
    assert offer.to_json()["available"] is False


# ======================================================================================
# 2. The proposal: the same admission protocol, and only to TRIAL_ADMITTED
# ======================================================================================


def test_a_legal_proposal_reaches_trial_admitted() -> None:
    env = empty_library_env()
    receipt = admit(env, proposal(legal_method()))
    assert receipt.verdict is AdmissionVerdict.TRIAL_ADMITTED
    assert receipt.status is MethodRegistryStatus.TRIAL_ADMITTED
    assert receipt.registration is not None
    assert receipt.registration.trial_scope_mission == MISSION


def test_a_trial_admission_is_scoped_to_this_mission_only() -> None:
    env = empty_library_env()
    receipt = admit(env, proposal(legal_method()))
    assert receipt.registration is not None
    assert receipt.registration.trial_scope_mission == MISSION
    other = env.registry.candidates_for(ref("synth.goal"), mission_id="mission-2")
    assert other == ()


def test_the_admitted_method_becomes_a_candidate_for_this_mission() -> None:
    env = empty_library_env()
    admit(env, proposal(legal_method()))
    candidates = env.registry.candidates_for(ref("synth.goal"), mission_id=MISSION)
    assert [item.method_ref.method_id for item in candidates] == ["synth.collect-then-deliver"]
    assert candidates[0].trial_scoped


def test_every_terminal_status_a_proposal_may_reach_is_one_of_two() -> None:
    assert TERMINAL_STATUSES == {
        MethodRegistryStatus.TRIAL_ADMITTED,
        MethodRegistryStatus.REJECTED,
    }
    assert MethodRegistryStatus.ADMITTED not in TERMINAL_STATUSES
    assert MethodRegistryStatus.EVALUATED not in TERMINAL_STATUSES


def test_a_model_that_declares_a_registry_status_is_refused_and_recorded() -> None:
    env = empty_library_env()
    receipt = admit(env, proposal(legal_method(), registry_status=str(MethodRegistryStatus.ADMITTED)))
    assert receipt.verdict is AdmissionVerdict.REJECTED
    assert RejectionCode.MODEL_CLAIMED_STATUS in receipt.codes()
    assert receipt.registration is not None
    assert receipt.registration.status is MethodRegistryStatus.REJECTED


def test_a_model_may_not_present_itself_as_the_system() -> None:
    env = empty_library_env()
    receipt = admit(env, proposal(legal_method(), author=str(RegistryAuthor.SYSTEM)))
    assert receipt.verdict is AdmissionVerdict.REJECTED
    assert RejectionCode.MODEL_CLAIMED_STATUS in receipt.codes()


def test_a_proposal_naming_an_operator_nobody_registered_is_refused() -> None:
    env = empty_library_env()
    contract = method(
        "synth.ghost",
        "synth.goal",
        parameter_schema="synth.goal.params",
        steps=(step("ghost", "synth.nowhere", TaskForm.PRIMITIVE, {"subject": param("subject")}),),
        links=(("c-root", "ghost", "c-done"),),
        finalizer="ghost",
    )
    receipt = admit(env, proposal(contract))
    assert receipt.verdict is AdmissionVerdict.REJECTED
    assert receipt.missing_operators or RejectionCode.UNKNOWN_TASK_TYPE in receipt.codes()


def test_a_proposal_that_covers_no_root_criterion_is_refused() -> None:
    """The composition must say which child criterion carries which parent one."""

    env = empty_library_env()
    contract = method(
        "synth.uncovered",
        "synth.goal",
        parameter_schema="synth.goal.params",
        steps=(
            step(
                "collect",
                "synth.collect",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("synth.read",),
            ),
        ),
        # A link to a parent criterion the goal type does not declare covers nothing.
        links=(("c-unrelated", "collect", "c-collected"),),
        finalizer="collect",
    )
    receipt = admit(env, proposal(contract))
    assert receipt.verdict is AdmissionVerdict.REJECTED
    assert RejectionCode.ROOT_COVERAGE_GAP in receipt.codes()


# ======================================================================================
# 3. Promotion is not available, and this path has no way to ask for it
# ======================================================================================


def test_the_module_has_no_promote_entry() -> None:
    assert [name for name in dir(method_proposals) if "promote" in name.lower()] == []


def test_the_registry_refuses_every_promotion_target() -> None:
    env = empty_library_env()
    receipt = admit(env, proposal(legal_method()))
    for target in (MethodRegistryStatus.EVALUATED, MethodRegistryStatus.ADMITTED):
        answer = env.registry.promote(receipt.method_ref, target, policy=env.policy())
        assert answer.verdict is AdmissionVerdict.PROMOTION_NOT_AVAILABLE


def test_succeeding_once_does_not_change_the_registration() -> None:
    env = empty_library_env()
    receipt = admit(env, proposal(legal_method()))
    env.registry.promote(receipt.method_ref, MethodRegistryStatus.ADMITTED, policy=env.policy())
    registration = env.registry.registration(receipt.method_ref)
    assert registration is not None
    assert registration.status is MethodRegistryStatus.TRIAL_ADMITTED


# ======================================================================================
# 5. Mutation self-check
# ======================================================================================


def test_mutant_a_request_that_leaked_the_registry_status_would_be_caught() -> None:
    env = empty_library_env()
    good = context(env)
    leaked = {**good.to_json(), "registry_status": "TRIAL_ADMITTED"}
    assert authority_claims(good.to_json()) == ()
    assert authority_claims(leaked) == ("registry_status",)


def test_mutant_normalising_the_declared_status_would_hide_the_attempt() -> None:
    env = empty_library_env()
    claimed = proposal(legal_method(), registry_status=str(MethodRegistryStatus.ADMITTED))
    assert claimed.declared_status is MethodRegistryStatus.ADMITTED  # kept, not normalised
    refused = admit(env, claimed)
    assert refused.verdict is AdmissionVerdict.REJECTED
    # The clean version of the same method is admitted, so the refusal is about the
    # claim and not about the definition.
    clean = admit(empty_library_env(), proposal(legal_method()))
    assert clean.verdict is AdmissionVerdict.TRIAL_ADMITTED


def test_mutant_offering_an_operator_the_catalogue_lacks_would_be_admitted_blindly() -> None:
    env = empty_library_env()
    request = context(env)
    assert "synth.nowhere" not in {item.task_type_id for item in request.operators}
    ghost = method(
        "synth.ghost2",
        "synth.goal",
        parameter_schema="synth.goal.params",
        steps=(step("ghost", "synth.nowhere", TaskForm.PRIMITIVE, {"subject": param("subject")}),),
        links=(("c-root", "ghost", "c-done"),),
        finalizer="ghost",
    )
    assert admit(env, proposal(ghost)).verdict is AdmissionVerdict.REJECTED


def test_mutant_a_registry_that_returned_an_admitted_receipt_would_be_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = empty_library_env()
    real = env.registry.admit

    def promoted(*args: Any, **kwargs: Any) -> Any:
        import dataclasses

        receipt = real(*args, **kwargs)
        return dataclasses.replace(receipt, transitions=(MethodRegistryStatus.ADMITTED,))

    monkeypatch.setattr(env.registry, "admit", promoted)
    with pytest.raises(ContractError, match="not a promotion"):
        admit(env, proposal(legal_method()))


# ======================================================================================
# 6. The author on this path is the model, and nobody can say otherwise
# ======================================================================================


def test_the_path_attributes_a_proposal_to_the_model() -> None:
    """A fixed value (§6.3, §7.3): the entry takes no author at all."""

    assert PROPOSAL_AUTHOR is RegistryAuthor.MODEL
    assert set(inspect.signature(admit_proposal).parameters) == {"proposal", "registry", "policy"}


def test_an_admitted_proposal_is_recorded_as_model_authored() -> None:
    env = empty_library_env()
    receipt = admit(env, proposal(legal_method()))
    assert receipt.author is RegistryAuthor.MODEL
    # Past DRAFT the *row* is always written by the registry service, which is what
    # stops a promoted status from being attributed to the submitter.
    assert receipt.registration is not None
    assert receipt.registration.author is RegistryAuthor.SYSTEM


def test_the_entry_takes_a_decoded_proposal_and_a_real_policy() -> None:
    env = empty_library_env()
    with pytest.raises(ContractError, match="decoded MethodProposal"):
        admit_proposal("<method_proposal>{}</method_proposal>",  # type: ignore[arg-type]
                       registry=env.registry, policy=env.policy())
    with pytest.raises(ContractError, match="AdmissionPolicy"):
        admit_proposal(proposal(legal_method()), registry=env.registry, policy=object())  # type: ignore[arg-type]
    assert env.registry.method_refs() == ()


def test_a_scripted_seed_domain_proposal_goes_through_the_same_path() -> None:
    env = seed_env(MISSION)
    payload = load_proposal("valid")
    receipt = admit(env, MethodProposal.from_json({"method": payload["method"], "rationale": "r"}))
    assert receipt.verdict in (AdmissionVerdict.TRIAL_ADMITTED, AdmissionVerdict.REJECTED)
    assert receipt.status in TERMINAL_STATUSES


# ======================================================================================
# 7. What the context tells the model to copy is what the codec accepts
# ======================================================================================


def test_method_shape_is_the_codecs_own_field_list() -> None:
    """Drop any name METHOD_SHAPE lists → the codec names it; add one → refused.

    So the list the context hands the model cannot drift from the codec.
    """

    import json

    from agent_orchestrator.contracts.htn import MethodContract
    from agent_orchestrator.planning.htn.method_proposals import METHOD_SHAPE

    base = legal_method().to_json()
    assert set(base) == set(METHOD_SHAPE["method"])
    assert set(base["steps"][0]) == set(METHOD_SHAPE["step"])
    assert set(base["composition"]) == set(METHOD_SHAPE["composition"])
    assert set(base["composition"]["criterion_links"][0]) == set(METHOD_SHAPE["criterion_link"])
    assert set(base["goal_type_ref"]) == set(METHOD_SHAPE["versioned_ref"])

    def refused(payload: dict[str, Any]) -> str:
        with pytest.raises(ContractError) as caught:
            MethodContract.from_json(payload)
        return str(caught.value)

    for name in METHOD_SHAPE["method"]:
        assert name in refused({k: v for k, v in base.items() if k != name}), name
    for name in METHOD_SHAPE["step"]:
        mutated = json.loads(json.dumps(base))
        del mutated["steps"][0][name]
        assert name in refused(mutated), name
    for name in METHOD_SHAPE["composition"]:
        mutated = json.loads(json.dumps(base))
        del mutated["composition"][name]
        assert name in refused(mutated), name
    for name in METHOD_SHAPE["criterion_link"]:
        mutated = json.loads(json.dumps(base))
        del mutated["composition"]["criterion_links"][0][name]
        assert name in refused(mutated), name
    assert "unknown fields: ['id']" in refused({**base, "id": base["method_id"]})


def test_the_context_carries_the_goal_type_ref_and_the_field_list_to_copy() -> None:
    from agent_orchestrator.planning.htn.method_proposals import METHOD_SHAPE

    env = empty_library_env()
    package = context(env).to_json()
    declared = env.catalog.require(ref("synth.goal")).task_type_ref
    assert package["goal_type_ref"] == declared.to_json()
    assert package["method_shape"] == {key: list(value) for key, value in METHOD_SHAPE.items()}
    # every operator ref carries its content hash, as the prompt says to copy
    assert all(len(item["task_type_ref"]["content_hash"]) == 64 for item in package["operators"])
    # nothing that addressed a separate synthesiser role (its tag, its prompt, its re-ask)
    assert not {"output_tag", "role_prompt_version", "schema_feedback", "review_feedback"} & set(package)


def test_a_proposed_method_wider_than_the_bound_is_refused() -> None:
    """Eight steps is the width a Mission's attempt budget can hold across a repair."""

    import dataclasses

    from agent_orchestrator.planning.htn.method_proposals import MAX_PROPOSED_METHOD_STEPS

    assert MAX_PROPOSED_METHOD_STEPS == 8
    env = empty_library_env()

    def wide(count: int) -> Any:
        return method(
            f"synth.wide-{count}", "synth.goal", parameter_schema="synth.goal.params",
            steps=tuple(step(f"s{index}", "synth.collect", TaskForm.PRIMITIVE,
                             {"subject": param("subject")}, capabilities=("synth.read",))
                        for index in range(count)),
            links=(("c-root", "s0", "c-done"),), finalizer="s0")

    policy = dataclasses.replace(env.policy(), max_steps=MAX_PROPOSED_METHOD_STEPS)
    refused = admit_proposal(proposal(wide(9)), registry=env.registry, policy=policy)
    assert refused.verdict is AdmissionVerdict.REJECTED
    assert RejectionCode.SIZE_BOUND in refused.codes()
    assert admit_proposal(proposal(wide(8)), registry=env.registry, policy=policy).admitted
