from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.companion.build_admission import GrowthCandidateBuildPermitV1
from deskpet.companion.contracts import OwnerRef
from deskpet.companion.contracts import GrowthEvent
from deskpet.companion.growth import (
    CandidateBindingFenceV1,
    GrowthTargetIdentityV1,
    StructuredGrowthProposalV1,
    evidence_set_hash,
)
from deskpet.companion.production_pipeline import (
    CandidateBuildTerminalExtension,
    FirstPartyGrowthTargetResolver,
    FirstPartySkillTemplateV1,
    GrowthProductionPipeline,
    _CandidatePermitView,
    _evaluation_baseline_fields,
    _source_risk_refs,
)
from deskpet.companion.run_adapter import (
    BackgroundRunCanonicalResultV1,
    BackgroundRunDurableResultV1,
    CompanionPreparedRunFacts,
    DelegatedPreparationGrant,
)
from deskpet.companion.store import CompanionStore
from deskpet.harness.contracts import PreparedRunContextV1


OWNER = OwnerRef("profile-a", 1)


def test_store_loads_exact_owner_fenced_live_evidence(tmp_path):
    store = CompanionStore(tmp_path / "companion.db")
    owner = store.create_profile(
        profile_id=OWNER.profile_id,
        generation=OWNER.profile_generation,
        identity_namespace_hash="relay:profile-a",
    )
    store.record_growth_event(
        GrowthEvent(
            owner=owner,
            event_id="event-1",
            source_kind="message_ingress",
            source_ref="message:1",
            context_key="session:1",
            root_run_id="run:1",
            reason_code="explicit_user_correction",
            payload={
                "message_text": "Keep two items.",
                "capability_id": "skill-summarize-day",
            },
        )
    )
    evidence = store.load_live_growth_evidence(
        owner,
        event_ids=("event-1",),
    )
    assert tuple(item.event_id for item in evidence) == ("event-1",)
    assert evidence[0].capability_id == "skill-summarize-day"
    with pytest.raises(Exception, match="growth_evidence_missing"):
        store.load_live_growth_evidence(
            owner,
            event_ids=("missing",),
        )


class _Projection:
    def __init__(self, root: Path) -> None:
        skill_path = root / "skills" / "summarize-day" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\nname: summarize-day\nallowed-tools: []\n---\nOld.\n",
            encoding="utf-8",
        )
        (root / "deskpet-pack.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "id": "skill-summarize-day",
                    "name": "summarize-day",
                    "version": "0.1.0",
                    "source": {"type": "builtin"},
                    "entries": {
                        "skills": [
                            {
                                "id": "summarize-day",
                                "path": "skills/summarize-day/SKILL.md",
                            }
                        ]
                    },
                    "permissions": [],
                    "effects": ["read_only"],
                    "files": [],
                }
            ),
            encoding="utf-8",
        )
        self.inventory = (
            SimpleNamespace(
                skill_id="summarize-day",
                pack_id="skill-summarize-day",
                version="0.1.0",
                manifest_hash="a" * 64,
                allowed_tools=(),
                pack_root=root,
                skill_path=skill_path,
            ),
        )

    def contains(self, name: str) -> bool:
        return name == "summarize-day"


class _Platform:
    def __init__(self) -> None:
        self.keys = None

    async def read_detail_snapshot(self, keys):
        self.keys = tuple(keys)
        return SimpleNamespace(
            bindings=(
                SimpleNamespace(
                    active=True,
                    capability_id="skill-summarize-day",
                    owner_key="builtin",
                    scope="builtin",
                    scope_key="builtin",
                    generation=1,
                    version="0.1.0",
                    manifest_hash="a" * 64,
                ),
            )
        )


@pytest.mark.asyncio
async def test_target_resolver_injects_builtin_override_fences(tmp_path):
    platform = _Platform()
    resolver = FirstPartyGrowthTargetResolver(
        projection=_Projection(tmp_path / "pack"),
        capability_platform=platform,
    )
    facts = await resolver(OWNER, "skill", "summarize-day")
    assert facts.candidate_mode.value == "builtin_override"
    assert facts.source_fence is not None
    assert facts.source_fence.owner_key == "builtin"
    assert facts.target_fence.owner_key == "companion:profile-a:1"
    assert facts.target_fence.expected_absent is True
    assert {item.scope for item in platform.keys} == {"builtin", "user"}


class _Transaction:
    def __init__(self) -> None:
        self.receipt = None
        self.material = None

    async def insert_candidate_draft_receipt(self, values, *, created_at):
        self.receipt = (dict(values), created_at)

    async def insert_candidate_draft_material(self, **values):
        self.material = dict(values)


@pytest.mark.asyncio
async def test_candidate_terminal_extension_persists_model_authored_skill():
    target = GrowthTargetIdentityV1(
        kind="skill",
        target_id="summarize-day",
        stable_name="summarize-day",
        pack_id="skill-summarize-day",
    )
    source = CandidateBindingFenceV1(
        owner_key="builtin",
        scope="builtin",
        scope_key="builtin",
        pack_id=target.pack_id,
        expected_absent=False,
        binding_generation=1,
        version="0.1.0",
        manifest_hash="a" * 64,
    )
    target_fence = CandidateBindingFenceV1(
        owner_key="companion:profile-a:1",
        scope="user",
        scope_key="profile-a",
        pack_id=target.pack_id,
        expected_absent=True,
        binding_generation=0,
    )
    proposal = StructuredGrowthProposalV1(
        decision="candidate",
        evidence_event_ids=("event-1",),
        evidence_set_hash=evidence_set_hash(("event-1",)),
        target=target,
        candidate_mode="builtin_override",
        source_fence=source,
        target_fence=target_fence,
        hypothesis="The correction is durable.",
        structured_diff={"requested_change": "Keep two items."},
        expected_improvement="The output follows the correction.",
        risk_hints=("instruction_only",),
        evaluation_plan=({"case_id": "summary-contract"},),
    )
    permit = GrowthCandidateBuildPermitV1.issue(
        build_id="build-1",
        owner_key="companion:profile-a:1",
        proposal_ref="proposal-1",
        proposal_hash=proposal.proposal_hash,
        evidence_set_hash=proposal.evidence_set_hash,
        source_fence_hash="b" * 64,
        target_fence_hash="c" * 64,
        lease_epoch=1,
    )
    registry = SimpleNamespace(
        get=lambda name: {
            "memory_recall": SimpleNamespace(
                permission_category="read_file",
                effect_class=SimpleNamespace(value="read_only"),
            ),
            "project_group_send": SimpleNamespace(
                permission_category="network",
                effect_class=SimpleNamespace(value="external_send"),
            ),
        }.get(name)
    )
    extension = CandidateBuildTerminalExtension(
        execution_run_id="run-1",
        permit=_CandidatePermitView(
            permit=permit,
            builder_launch_id="builder-launch-1",
            child_run_id="candidate-child-1",
            child_start_hash="d" * 64,
        ),
        proposal=proposal,
        template=FirstPartySkillTemplateV1(
            target=target,
            candidate_mode="builtin_override",
            source_fence=source,
            target_fence=target_fence,
            relative_path="skills/summarize-day/SKILL.md",
            source_markdown="old",
            manifest_template={
                "schema_version": 2,
                "id": target.pack_id,
                "name": "summarize-day",
                "entries": {
                    "skills": [
                        {
                            "id": "summarize-day",
                            "path": "skills/summarize-day/SKILL.md",
                        }
                    ]
                },
                "permissions": [],
                "effects": ["read_only"],
            },
            allowed_tools=(),
        ),
        created_at="2026-07-25T04:00:00+00:00",
        tool_registry=registry,
    )
    transaction = _Transaction()
    markdown = (
        "---\nname: summarize-day\n"
        "allowed-tools: [memory_recall, project_group_send]\n---\n"
        "Keep exactly two items and attach one next step to each.\n"
    )
    structured = {
        "schema_version": 1,
        "skill_markdown": markdown,
        "allowed_tools": ["memory_recall", "project_group_send"],
        "rationale": "Matches the explicit correction.",
        "self_checks": ["instruction-only"],
    }
    product, _receipt = extension.product_and_receipt(structured)
    skill_entry = product.manifest["entries"]["skills"][0]
    assert skill_entry["allowed_tools"] == [
        "memory_recall",
        "project_group_send",
    ]
    assert product.manifest["permissions"] == ["network", "read_file"]
    assert product.manifest["effects"] == ["opaque_manual", "read_only"]
    assert json.loads(product.seed_bytes)["effect_topology"] == {
        "effects": ["external_send", "read_only"]
    }
    with pytest.raises(ValueError, match="candidate_allowed_tools_mismatch"):
        extension.product_and_receipt(
            {**structured, "allowed_tools": ["memory_recall"]}
        )
    receipt_ref = await extension.apply_terminal_commit(
        transaction,
        record=SimpleNamespace(run_id="run-1"),
        terminal_event=SimpleNamespace(
            kind="run.final",
            payload={
                "text": json.dumps(
                    structured
                )
            },
        ),
    )
    assert receipt_ref.kind == "deskpet.candidate-draft.v1"
    assert transaction.receipt is not None
    assert transaction.material is not None
    assert hashlib_sha256(transaction.material["archive_bytes"]) == (
        transaction.receipt[0]["archive_hash"]
    )

    fenced_transaction = _Transaction()
    fenced_ref = await extension.apply_terminal_commit(
        fenced_transaction,
        record=SimpleNamespace(run_id="run-1"),
        terminal_event=SimpleNamespace(
            kind="run.final",
            status="succeeded",
            payload={
                "text": "```json\n"
                + json.dumps(structured, ensure_ascii=False)
                + "\n```"
            },
        ),
    )
    assert fenced_ref.kind == "deskpet.candidate-draft.v1"
    assert fenced_transaction.receipt is not None
    assert fenced_transaction.material is not None

    failed_transaction = _Transaction()
    failed_ref = await extension.apply_terminal_commit(
        failed_transaction,
        record=SimpleNamespace(run_id="run-1"),
        terminal_event=SimpleNamespace(
            kind="run.final",
            status="failed",
            payload={"text": "not valid JSON"},
        ),
    )
    assert failed_ref == extension.descriptor
    assert failed_transaction.receipt is None
    assert failed_transaction.material is None


def hashlib_sha256(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def _candidate_retry_proposal() -> StructuredGrowthProposalV1:
    target = GrowthTargetIdentityV1(
        kind="skill",
        target_id="summarize-day",
        stable_name="summarize-day",
        pack_id="skill-summarize-day",
    )
    return StructuredGrowthProposalV1(
        decision="candidate",
        evidence_event_ids=("event-1",),
        evidence_set_hash=evidence_set_hash(("event-1",)),
        target=target,
        candidate_mode="genesis",
        source_fence=None,
        target_fence=CandidateBindingFenceV1(
            owner_key="companion:profile-a:1",
            scope="user",
            scope_key="profile-a",
            pack_id=target.pack_id,
            expected_absent=True,
            binding_generation=0,
        ),
        hypothesis="The correction is durable.",
        structured_diff={"requested_change": "Keep two items."},
        expected_improvement="The output follows the correction.",
        risk_hints=("instruction_only",),
        evaluation_plan=({"case_id": "summary-contract"},),
    )


def test_evaluation_baseline_fields_match_store_contract():
    genesis = _candidate_retry_proposal()
    absent = _evaluation_baseline_fields(genesis)
    assert absent == {
        "baseline_kind": "capability_absent_v1",
        "old_snapshot_hash": absent["old_snapshot_hash"],
        "absent_baseline_ref": "capability-absent-v1",
        "absent_baseline_hash": absent["old_snapshot_hash"],
    }

    source = CandidateBindingFenceV1(
        owner_key="builtin",
        scope="builtin",
        scope_key="builtin",
        pack_id="skill-summarize-day",
        expected_absent=False,
        binding_generation=4,
        version="1.2.3",
        manifest_hash="a" * 64,
    )
    update = StructuredGrowthProposalV1(
        decision="candidate",
        evidence_event_ids=genesis.evidence_event_ids,
        evidence_set_hash=genesis.evidence_set_hash,
        target=genesis.target,
        candidate_mode="builtin_override",
        source_fence=source,
        target_fence=genesis.target_fence,
        hypothesis=genesis.hypothesis,
        structured_diff=genesis.structured_diff,
        expected_improvement=genesis.expected_improvement,
        risk_hints=genesis.risk_hints,
        evaluation_plan=genesis.evaluation_plan,
    )
    frozen = _evaluation_baseline_fields(update)
    assert frozen["baseline_kind"] == "source_pack"
    assert frozen["source_owner_key"] == "builtin"
    assert frozen["source_scope"] == "builtin"
    assert frozen["source_scope_key"] == "builtin"
    assert frozen["source_pack_id"] == "skill-summarize-day"
    assert frozen["source_version"] == "1.2.3"
    assert frozen["source_manifest_hash"] == "a" * 64
    assert frozen["source_binding_generation"] == 4


def test_source_risk_refs_include_readonly_package_effect_for_golden_update():
    proposal = _candidate_retry_proposal()
    template = FirstPartySkillTemplateV1(
        target=proposal.target,
        candidate_mode=proposal.candidate_mode,
        source_fence=proposal.source_fence,
        target_fence=proposal.target_fence,
        relative_path="skills/summarize-day/SKILL.md",
        source_markdown="old",
        manifest_template={"effects": ["read_only"]},
        allowed_tools=("memory_recall",),
    )
    refs = _source_risk_refs(
        template,
        {"memory_recall": {"spec_ref": "tool:memory_recall:v1"}},
    )

    assert refs == (
        "package-effect:0:read_only",
        "tool:memory_recall:v1",
    )


class _BuiltRetryStore:
    def __init__(self, proposal: StructuredGrowthProposalV1) -> None:
        self.proposal = proposal
        self.claim_calls = 0

    def get_job(self, owner, *, job_id):
        del owner, job_id
        return {
            "payload_json": json.dumps(
                {
                    "request_payload": {
                        "companion_stage": "candidate_build",
                        "build_id": "build-1",
                        "proposal": self.proposal.to_dict(),
                    }
                }
            )
        }

    def get_candidate_build_recovery(self, owner, *, build_id):
        assert owner == OWNER
        assert build_id == "build-1"
        return {
            "build_id": build_id,
            "status": "built",
            "candidate_ref": "candidate-1",
        }

    def claim_next_candidate_build(self, *args, **kwargs):
        del args, kwargs
        self.claim_calls += 1
        raise AssertionError("built retry must not reclaim the candidate build")


def _built_retry_pipeline(store: _BuiltRetryStore) -> GrowthProductionPipeline:
    return GrowthProductionPipeline(
        store=store,
        target_resolver=SimpleNamespace(catalog=()),
        execution_database_path="execution.db",
        tool_registry=SimpleNamespace(),
        revocation_barrier=SimpleNamespace(epoch=1),
    )


@pytest.mark.asyncio
async def test_built_candidate_retry_skips_terminal_build_extension():
    proposal = _candidate_retry_proposal()
    store = _BuiltRetryStore(proposal)
    pipeline = _built_retry_pipeline(store)
    base = PreparedRunContextV1()

    prepared = await pipeline.prepared_context(
        OWNER,
        SimpleNamespace(item_id="candidate-job-1"),
        SimpleNamespace(),
        base,
        "execution-run-2",
    )

    assert prepared is base
    assert prepared.terminal_commit_extensions == ()
    assert store.claim_calls == 0


@pytest.mark.asyncio
async def test_built_candidate_retry_resumes_exact_evaluation_admission():
    proposal = _candidate_retry_proposal()
    store = _BuiltRetryStore(proposal)
    pipeline = _built_retry_pipeline(store)
    admitted = []

    async def _record_admission(
        owner,
        *,
        proposal,
        candidate_id,
        evidence_ids,
        semantic_replan_attempt=0,
    ):
        admitted.append(
            (
                owner,
                proposal,
                candidate_id,
                evidence_ids,
                semantic_replan_attempt,
            )
        )

    pipeline._admit_and_enqueue_evaluation = _record_admission
    result = BackgroundRunCanonicalResultV1(
        owner=OWNER,
        job_id="candidate-job-1",
        claim_owner="runtime-1",
        claim_epoch=3,
        purpose="delegated_task",
        evidence_ids=("event-1",),
        execution_run_id="execution-run-2",
        execution_session_id="execution-session-2",
        status="succeeded",
        result_ref="candidate-result-2",
        result_hash="b" * 64,
        text="ignored replay output",
        structured_result=None,
        terminal_payload={"text": "ignored replay output"},
        artifact_refs=(),
    )

    await pipeline._after_candidate(
        result,
        {
            "build_id": "build-1",
            "proposal": proposal.to_dict(),
        },
    )

    assert admitted == [
        (OWNER, proposal, "candidate-1", ("event-1",), 0)
    ]
    assert store.claim_calls == 0


class _EvaluationAdmissionCaptured(Exception):
    pass


class _EvaluationAdmissionStore:
    def __init__(self) -> None:
        self.case_inputs = ()

    def get_exact_candidate_bundle(self, owner, *, candidate_id):
        assert owner == OWNER
        assert candidate_id == "candidate-1"
        return SimpleNamespace(
            package=SimpleNamespace(
                candidate_package_hash="a" * 64,
                candidate_manifest_hash="b" * 64,
                archive_hash="c" * 64,
            )
        )

    def admit_evaluation_experiment(
        self,
        owner,
        *,
        evaluation,
        case_inputs,
        cases,
    ):
        assert owner == OWNER
        assert evaluation["candidate_id"] == "candidate-1"
        assert {item["variant"] for item in cases} == {"old", "candidate"}
        self.case_inputs = tuple(case_inputs)
        raise _EvaluationAdmissionCaptured


@pytest.mark.asyncio
async def test_production_evaluation_freezes_growth_evidence_as_historical_replay():
    proposal = _candidate_retry_proposal()
    store = _EvaluationAdmissionStore()
    pipeline = GrowthProductionPipeline(
        store=store,
        target_resolver=SimpleNamespace(catalog=()),
        execution_database_path="execution.db",
        tool_registry=SimpleNamespace(),
        revocation_barrier=SimpleNamespace(epoch=1),
    )

    with pytest.raises(_EvaluationAdmissionCaptured):
        await pipeline._admit_and_enqueue_evaluation(
            OWNER,
            proposal=proposal,
            candidate_id="candidate-1",
            evidence_ids=("event-2", "event-1"),
        )

    assert len(store.case_inputs) == 1
    frozen_input = store.case_inputs[0]
    assert frozen_input["source_kind"] == "historical_replay"
    assert frozen_input["source_event_refs"] == ("event-2", "event-1")


class _EvaluationSettlementStore:
    def __init__(self) -> None:
        self.claim_epochs = []
        self.report = None

    def get_evaluation_execution_fence(
        self,
        owner,
        *,
        evaluation_id,
        case_id,
        variant,
    ):
        assert owner == OWNER
        assert evaluation_id == "evaluation-1"
        assert case_id == "case-1"
        assert variant in {"old", "candidate"}
        return {"claim_epoch": 7}

    def settle_evaluation_case_launch(self, *args, **kwargs):
        del args, kwargs

    def record_evaluation_result(self, owner, **kwargs):
        assert owner == OWNER
        self.claim_epochs.append(kwargs["claim_epoch"])
        return {"result_hash": kwargs["result_hash"]}

    def get_exact_candidate_bundle(self, owner, *, candidate_id):
        assert owner == OWNER
        assert candidate_id == "candidate-1"
        return SimpleNamespace(
            candidate_id=candidate_id,
            package=SimpleNamespace(candidate_package_hash="a" * 64)
        )

    def create_evaluation_report(self, owner, **kwargs):
        assert owner == OWNER
        self.report = dict(kwargs)


@pytest.mark.asyncio
async def test_evaluation_settlement_uses_store_claim_epoch_field():
    store = _EvaluationSettlementStore()

    class _NoActivationPipeline(GrowthProductionPipeline):
        async def _activate_candidate(self, *args, **kwargs):
            del args, kwargs
            raise AssertionError("safe_static evaluation must not auto activate")

    pipeline = _NoActivationPipeline(
        store=store,
        target_resolver=SimpleNamespace(catalog=()),
        execution_database_path="execution.db",
        tool_registry=SimpleNamespace(),
        revocation_barrier=SimpleNamespace(epoch=1),
        activation_dispatcher=object(),
    )
    result = BackgroundRunCanonicalResultV1(
        owner=OWNER,
        job_id="evaluation-job-1",
        claim_owner="runtime-1",
        claim_epoch=3,
        purpose="evaluation",
        evidence_ids=("event-1",),
        execution_run_id="execution-run-1",
        execution_session_id="execution-session-1",
        status="succeeded",
        result_ref="evaluation-result-1",
        result_hash="b" * 64,
        text="{}",
        structured_result={
            "schema_version": 1,
            "case_id": "case-1",
            "old_status": "failed",
            "candidate_status": "passed",
            "improved": True,
            "baseline_regression": False,
            "explanation": "candidate satisfies the correction",
        },
        terminal_payload={"text": "{}"},
        artifact_refs=(),
    )

    await pipeline._after_evaluation(
        result,
        {
            "evaluation_id": "evaluation-1",
            "candidate_id": "candidate-1",
            "case_id": "case-1",
            "suite_hash": "suite-1",
            "permit": {"mode": "safe_static"},
            "proposal": _candidate_retry_proposal().to_dict(),
        },
    )

    assert store.claim_epochs == [7, 7]
    assert store.report is not None
    assert store.report["verdict"] == "passed"


class _EvaluationFailureStore(_EvaluationSettlementStore):
    def __init__(self) -> None:
        super().__init__()
        self.transitions = []
        self.events = []
        self.jobs = []

    def transition_candidate(self, owner, **kwargs):
        assert owner == OWNER
        self.transitions.append(dict(kwargs))
        return {"status": kwargs["next_status"]}

    def record_growth_event(self, event):
        assert event.owner == OWNER
        self.events.append(event)
        return {"event_hash": "f" * 64}

    def enqueue_job(self, owner, **kwargs):
        assert owner == OWNER
        self.jobs.append(dict(kwargs))


@pytest.mark.asyncio
async def test_failed_semantic_evaluation_invalidates_candidate_and_replans():
    store = _EvaluationFailureStore()
    pipeline = GrowthProductionPipeline(
        store=store,
        target_resolver=SimpleNamespace(
            catalog=(
                {
                    "target_kind": "skill",
                    "stable_name": "summarize-day",
                },
            )
        ),
        execution_database_path="execution.db",
        tool_registry=SimpleNamespace(),
        revocation_barrier=SimpleNamespace(epoch=1),
    )
    result = BackgroundRunCanonicalResultV1(
        owner=OWNER,
        job_id="evaluation-job-1",
        claim_owner="runtime-1",
        claim_epoch=3,
        purpose="evaluation",
        evidence_ids=("event-1",),
        execution_run_id="execution-run-1",
        execution_session_id="execution-session-1",
        status="succeeded",
        result_ref="evaluation-result-1",
        result_hash="b" * 64,
        text="{}",
        structured_result={
            "schema_version": 1,
            "case_id": "case-1",
            "old_status": "failed",
            "candidate_status": "failed",
            "improved": True,
            "baseline_regression": False,
            "explanation": (
                "Missing explicit config error and partial-send reporting."
            ),
        },
        terminal_payload={"text": "{}"},
        artifact_refs=(),
    )

    await pipeline._after_evaluation(
        result,
        {
            "evaluation_id": "evaluation-1",
            "candidate_id": "candidate-1",
            "case_id": "case-1",
            "suite_hash": "suite-1",
            "proposal": _candidate_retry_proposal().to_dict(),
            "semantic_replan_attempt": 0,
        },
    )

    assert store.report is not None
    replan = store.report["failed_replan"]
    assert replan["candidate_id"] == "candidate-1"
    assert replan["exhausted"] is False
    assert replan["event"]["reason_code"] == (
        "independent_evaluation_failed_replan"
    )
    assert replan["event"]["payload"]["replan_attempt"] == 1
    payload = replan["job"]["payload"]
    assert payload["request_payload"]["semantic_replan_attempt"] == 1
    assert "partial-send reporting" in payload["text"]
    assert "原用户目标：Keep two items." in payload["text"]


@pytest.mark.asyncio
async def test_failed_semantic_evaluation_replan_is_bounded():
    store = _EvaluationFailureStore()
    pipeline = GrowthProductionPipeline(
        store=store,
        target_resolver=SimpleNamespace(catalog=()),
        execution_database_path="execution.db",
        tool_registry=SimpleNamespace(),
        revocation_barrier=SimpleNamespace(epoch=1),
    )
    result = BackgroundRunCanonicalResultV1(
        owner=OWNER,
        job_id="evaluation-job-2",
        claim_owner="runtime-1",
        claim_epoch=3,
        purpose="evaluation",
        evidence_ids=("event-1",),
        execution_run_id="execution-run-2",
        execution_session_id="execution-session-2",
        status="succeeded",
        result_ref="evaluation-result-2",
        result_hash="c" * 64,
        text="{}",
        structured_result={
            "schema_version": 1,
            "case_id": "case-1",
            "old_status": "failed",
            "candidate_status": "failed",
            "improved": False,
            "baseline_regression": False,
            "explanation": "Still incomplete.",
        },
        terminal_payload={"text": "{}"},
        artifact_refs=(),
    )

    await pipeline._after_evaluation(
        result,
        {
            "evaluation_id": "evaluation-1",
            "candidate_id": "candidate-1",
            "case_id": "case-1",
            "suite_hash": "suite-1",
            "proposal": _candidate_retry_proposal().to_dict(),
            "semantic_replan_attempt": (
                pipeline._MAX_SEMANTIC_REPLAN_ATTEMPTS
            ),
        },
    )

    assert store.report is not None
    assert store.report["failed_replan"] == {
        "candidate_id": "candidate-1",
        "exhausted": True,
    }


class _ReminderDraftStore:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def get_job(self, owner, *, job_id):
        assert owner == OWNER
        assert job_id == "reminder-draft-job"
        return {"payload_json": json.dumps(self.payload)}


def _reminder_draft_payload(*, external_send_allowed: bool = False) -> dict:
    refs = [
        "message:session-1:m1",
        "message:session-1:m2",
        "message:session-1:m3",
    ]
    occurrence_id = "occurrence-1"
    expires_at = "2026-08-01T08:00:00.000Z"
    return {
        "purpose": "delegated_task",
        "owner_key": "companion:profile-a:1",
        "evidence_ids": refs,
        "delegated_grants": [
            {
                "grant_id": f"grant-{scope}",
                "scope": scope,
                "target": f"reminder:{occurrence_id}",
                "expires_at": expires_at,
                "version": 1,
            }
            for scope in ("read", "draft", "reversible_local")
        ],
        "text": "Prepare a local weekly draft without tools.",
        "request_payload": {
            "companion_stage": "reminder_draft",
            "operation": "prepare_reminder_draft",
            "reminder_id": "reminder-1",
            "occurrence_id": occurrence_id,
            "reminder_text": "准备本周五周报草稿",
            "source_message_refs": refs,
            "allowed_grant_scopes": [
                "read",
                "draft",
                "reversible_local",
            ],
            "external_send_allowed": external_send_allowed,
        },
    }


@pytest.mark.asyncio
async def test_reminder_draft_stage_validates_frozen_facts_and_returns_durable_body():
    payload = _reminder_draft_payload()
    source_messages = {
        payload["evidence_ids"][0]: "本周已完成角色移动",
        payload["evidence_ids"][1]: "待办是接入存档",
        payload["evidence_ids"][2]: "风险是移动端性能",
    }
    pipeline = GrowthProductionPipeline(
        store=_ReminderDraftStore(payload),
        target_resolver=SimpleNamespace(catalog=()),
        execution_database_path="execution.db",
        tool_registry=SimpleNamespace(),
        revocation_barrier=SimpleNamespace(epoch=1),
        reminder_source_loader=lambda _owner, _refs: source_messages,
    )
    facts = CompanionPreparedRunFacts(
        owner=OWNER,
        owner_key=payload["owner_key"],
        job_id="reminder-draft-job",
        purpose="delegated_task",
        evidence_ids=tuple(payload["evidence_ids"]),
        delegated_grants=tuple(
            DelegatedPreparationGrant.from_mapping(item)
            for item in payload["delegated_grants"]
        ),
    )
    base = PreparedRunContextV1(persistence_required=True)
    prepared = await pipeline.prepared_context(
        OWNER,
        SimpleNamespace(item_id="reminder-draft-job"),
        facts,
        base,
        "execution-run-1",
    )
    assert prepared is base
    grounded_prompt = await pipeline.prompt(
        OWNER,
        SimpleNamespace(item_id="reminder-draft-job"),
        facts,
        payload["text"],
    )
    assert all(value in grounded_prompt for value in source_messages.values())
    assert all(ref in grounded_prompt for ref in payload["evidence_ids"])
    assert "不要发送" in grounded_prompt

    result = BackgroundRunCanonicalResultV1(
        owner=OWNER,
        job_id="reminder-draft-job",
        claim_owner="runtime-1",
        claim_epoch=1,
        purpose="delegated_task",
        evidence_ids=tuple(payload["evidence_ids"]),
        execution_run_id="execution-run-1",
        execution_session_id="execution-session-1",
        status="succeeded",
        result_ref="companion-background:reminder-draft-job:execution-run-1",
        result_hash="a" * 64,
        text=(
            "本周已完成角色移动；待办是接入存档；"
            "风险是移动端性能。"
        ),
        structured_result=None,
        terminal_payload={"text": "draft"},
        artifact_refs=(),
    )
    durable = await pipeline.postprocess(result)

    assert isinstance(durable, BackgroundRunDurableResultV1)
    assert durable.reason_code == "reminder_draft_committed"
    body = json.loads(durable.result_ref.removeprefix("json:"))
    assert body["text"]
    assert body["source_message_refs"] == payload["evidence_ids"]
    assert body["external_send_allowed"] is False
    assert body["external_send_count"] == 0


@pytest.mark.asyncio
async def test_reminder_draft_stage_fails_closed_on_external_send_request():
    payload = _reminder_draft_payload(external_send_allowed=True)
    pipeline = GrowthProductionPipeline(
        store=_ReminderDraftStore(payload),
        target_resolver=SimpleNamespace(catalog=()),
        execution_database_path="execution.db",
        tool_registry=SimpleNamespace(),
        revocation_barrier=SimpleNamespace(epoch=1),
    )
    facts = CompanionPreparedRunFacts(
        owner=OWNER,
        owner_key=payload["owner_key"],
        job_id="reminder-draft-job",
        purpose="delegated_task",
        evidence_ids=tuple(payload["evidence_ids"]),
        delegated_grants=tuple(
            DelegatedPreparationGrant.from_mapping(item)
            for item in payload["delegated_grants"]
        ),
    )

    with pytest.raises(ValueError, match="reminder_draft_request_invalid"):
        await pipeline.prepared_context(
            OWNER,
            SimpleNamespace(item_id="reminder-draft-job"),
            facts,
            PreparedRunContextV1(persistence_required=True),
            "execution-run-1",
        )
