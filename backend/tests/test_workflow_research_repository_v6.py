from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

import aiosqlite
import pytest

from deskpet.workflows.definitions.deep_research_v6_delivery import (
    TerminalDeliveryManifestV1,
    build_intent_specs,
)
from deskpet.workflows.definitions.deep_research_v6_compiler import compile_research_spec
from deskpet.workflows.definitions.deep_research_v6_contracts import parse_blob_ref
from deskpet.workflows.definitions.deep_research_v6_evidence import (
    AdmittedResearchFactV1,
    AnswerAssessmentV1,
    EvidenceFactBatchV1,
    GENESIS_EVIDENCE_HEAD,
)
from deskpet.workflows.definitions.deep_research_v6_integrity import ClaimBatchV1
from deskpet.workflows.store.blob_store import BlobStore
from deskpet.workflows.store.research_repository import (
    ResearchRepositoryError,
    ResearchWorkflowRepository,
    _v6_continuation_identity,
)
from deskpet.workflows.store.run_store import WorkflowRunStore


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))


class Clock:
    value = 1_000.0

    def __call__(self):
        return self.value


async def _put(path, root, value, *, raw=False):
    data = value if raw else _canonical(value).encode()
    ref = BlobStore(root).put(data, media_type="application/json")
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT OR IGNORE INTO workflow_blobs(
            sha256,size_bytes,media_type,relative_path,created_at) VALUES(?,?,?,?,900)""",
            (ref.sha256, ref.size_bytes, ref.media_type, BlobStore(root).path_for(ref.sha256).relative_to(root).as_posix()),
        )
        await db.commit()
    return "sha256:" + ref.sha256


async def _seed_parent(tmp_path, parent="11111111-1111-1111-1111-111111111111"):
    path = tmp_path / "workflow.db"
    root = tmp_path / "blobs"
    clock = Clock()
    store = WorkflowRunStore(path, clock=clock)
    await store.create_run(
        request_key="root:" + parent, session_id="session-1", request_id="root-request",
        turn_id="root-turn", workflow_name="deep_research", workflow_version="v6",
        manifest_hash="manifest-v6", implementation_hash="implementation-v6",
        capability_hash="capability-v6", capability_snapshot={"research": True},
        state_schema_version=6, run_id=parent, trace_id="root-trace", thread_id="root-thread",
    )
    await store.bind_session_refs(parent, (("base", "session-1", 1), ("delivery", "session-1", 1)))
    repo = ResearchWorkflowRepository(path, clock=clock, blob_root=root)
    await repo.ensure_root_lineage(run_id=parent, operation_id="research:" + parent, budget_lease_id="root-budget")

    spec = compile_research_spec(
        "What was China's total population in 2024?",
        answer_locale="en-US", as_of_date="2026-07-18",
    )
    spec_hash = spec.spec_hash
    spec_ref = await _put(path, root, spec.to_json())
    policy_keys = (
        "compiler", "route", "extraction", "llm_extract", "llm_repair", "llm_inference",
        "admission", "inference", "assessment", "claim", "quality",
    )
    policy_refs = {
        key: await _put(path, root, {"schema_version": 1, "policy_id": f"{key}-test-v1"})
        for key in policy_keys
    }
    requirement_id = str(spec.requirements[0]["requirement_id"])
    fact = AdmittedResearchFactV1.create(
        run_id=parent, spec_hash=spec_hash, requirement_id=requirement_id,
        target_kind="scalar", item_or_cell_id=None, field_or_facet_key=None,
        candidate_id="ecd-repository", page_id="page-repository", span_id="span-repository",
        binding_id="binding-repository", source_family_id="official-repository",
        source_tier="first_party", admission_policy_hash=parse_blob_ref(policy_refs["admission"]),
        status="admitted", semantic_payload={
            "value": 140828, "canonical_unit": "ten_thousand_persons",
            "time_scope": "2024", "scope": "China",
            "definition": "year-end national population",
        },
    )
    provenance_ref = await _put(path, root, fact.to_json())
    batch = EvidenceFactBatchV1.create(
        batch_kind="facts", run_id=parent, spec_hash=spec_hash,
        previous_head_hash=GENESIS_EVIDENCE_HEAD, ordinal=0,
        page_result_refs=(), candidate_slot_results=(), inference_slot_results=(),
        admitted_fact_refs=(provenance_ref,), registered_inference_refs=(),
        rejected_candidate_ids=(), conflict_ids=(), provenance_refs=(provenance_ref,),
        policy_refs=policy_refs,
    )
    batch_ref = await _put(path, root, batch.to_json())
    evidence_head_hash = batch.head_hash
    assessment = AnswerAssessmentV1.create(
        spec_hash=spec_hash, evidence_head_hash=evidence_head_hash,
        requirement_results=(), missing_requirement_ids=(requirement_id,),
        minimum_useful=False, status="insufficient", reason_codes=("required_evidence_missing",),
        policy_hash=parse_blob_ref(policy_refs["assessment"]),
        ordered_fact_refs=(provenance_ref,), ordered_inference_refs=(),
    )
    assessment_hash = assessment.assessment_hash
    assessment_input_hash = assessment.assessment_input_hash
    assessment_ref = await _put(path, root, assessment.to_json())
    claim = ClaimBatchV1.create(
        run_id=parent, spec_hash=spec_hash, evidence_head_hash=evidence_head_hash,
        assessment_hash=assessment_hash,
        claim_policy_hash=parse_blob_ref(policy_refs["claim"]), claims=(), status="valid",
    )
    claim_ref = await _put(path, root, claim.to_json())
    closure = sorted({
        spec_ref, batch_ref, assessment_ref, claim_ref, provenance_ref, *policy_refs.values(),
    })
    snapshot_base = {
        "schema_version": 1, "run_id": parent, "workflow_name": "deep_research",
        "workflow_version": "v6", "spec_ref": spec_ref, "spec_hash": spec_hash,
        "fact_batch_refs": [batch_ref], "evidence_head_hash": evidence_head_hash,
        "assessment_ref": assessment_ref, "assessment_hash": assessment_hash,
        "assessment_input_hash": assessment_input_hash, "claim_batch_ref": claim_ref,
        "provenance_refs": [provenance_ref], "policy_refs": policy_refs, "closure_refs": closure,
    }
    snapshot_hash = hashlib.sha256(_canonical(snapshot_base).encode()).hexdigest()
    snapshot = {**snapshot_base, "snapshot_id": "rcs_" + snapshot_hash[:24], "snapshot_hash": snapshot_hash}
    snapshot_ref = await _put(path, root, snapshot)
    final_ref = await _put(path, root, {"text": "answer"})
    report_ref = await _put(path, root, {"text": "report"})
    quality_ref = await _put(path, root, {"hard_gate_status": "passed"})
    manifest = TerminalDeliveryManifestV1.create(
        workflow_name="deep_research", workflow_version="v6", run_id=parent,
        answer_status="partial", spec_hash=spec_hash, assessment_hash=assessment_hash,
        claim_policy_hash=parse_blob_ref(policy_refs["claim"]),
        quality_policy_hash=parse_blob_ref(policy_refs["quality"]),
        continuation_snapshot_ref=snapshot_ref, continuation_snapshot_hash=snapshot_hash,
        content_refs={"final_assistant_ref": final_ref, "report_ref": report_ref,
                      "safe_summary_ref": None, "claim_batch_ref": claim_ref,
                      "quality_audit_ref": quality_ref},
        intent_specs=build_intent_specs(run_id=parent, final_assistant_ref=final_ref,
                                        report_ref=report_ref, artifact_required=True),
        cardinality={"final_assistant": 1, "workflow_final_status": 1, "report": 1,
                     "artifact": 1, "run_terminal": 1},
        engine_terminal={"status": "completed", "error_code": None, "recovery_action": None},
    )
    manifest_ref = await _put(path, root, manifest.to_json())
    assert manifest_ref == manifest.manifest_ref
    all_refs = {manifest_ref, snapshot_ref, *closure, final_ref, report_ref, quality_ref}
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """UPDATE workflow_runs SET status='completed',head_checkpoint_id='terminal-head',
            head_checkpoint_ns='',ended_at=999,updated_at=999 WHERE run_id=?""", (parent,),
        )
        await db.execute(
            """INSERT INTO workflow_checkpoints(
            thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,checkpoint_type,
            checkpoint_blob,metadata_blob,engine_kind,snapshot_version,created_at)
            VALUES('root-thread','','terminal-head',NULL,?,'native',X'7B7D',X'7B7D','deskpet-native',1,999)""",
            (parent,),
        )
        await db.execute(
            """INSERT INTO workflow_checkpoint_owners(
            run_id,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at)
            VALUES(?,'root-thread','','terminal-head',NULL,999)""", (parent,),
        )
        for ref in all_refs:
            await db.execute(
                "INSERT INTO workflow_blob_refs(sha256,owner_kind,owner_id,created_at) VALUES(?,'checkpoint','terminal-head',999)",
                (ref[7:],),
            )
        await db.execute(
            """INSERT INTO workflow_events(event_id,event_key,run_id,seq,event_type,payload_json,created_at)
            VALUES('terminal-event','run:terminal',?,1,'workflow.final',?,999)""",
            (parent, _canonical({"manifest_ref": manifest_ref, "answer_status": "partial"})),
        )
        await db.commit()
    await repo.persist_v6_continuation_snapshot(
        run_id=parent, operation_id="research:" + parent,
        terminal_manifest_ref=manifest_ref, continue_until=2_000.0,
    )
    return path, root, repo, clock, snapshot


def test_v6_continuation_identity_golden_is_independent_constant():
    canonical_name = (
        '{"parent_run_id":"11111111-1111-1111-1111-111111111111","policy_version":1}'
    )
    namespace = uuid.uuid5(
        uuid.NAMESPACE_URL,
        "https://deskpet.local/workflow/deep-research/v6/continuation",
    )
    assert canonical_name == _canonical({
        "parent_run_id": "11111111-1111-1111-1111-111111111111",
        "policy_version": 1,
    })
    assert str(namespace) == "a7e5f48a-9b2b-549f-a67c-3ebdee7b9c78"
    identity = _v6_continuation_identity("11111111-1111-1111-1111-111111111111")
    assert identity["child_run_id"] == "2c2bba0d-c87b-5e70-ba41-1bf66ade02ff"
    assert identity["child_operation_id"] == "research:2c2bba0d-c87b-5e70-ba41-1bf66ade02ff"
    assert identity["child_start_operation_id"] == (
        "research:2c2bba0d-c87b-5e70-ba41-1bf66ade02ff:start"
    )
    assert identity["child_request_key"] == (
        "research-continuation-v6:11111111-1111-1111-1111-111111111111:1"
    )
    assert identity["child_request_id"] == (
        "continue:v6:2c2bba0d-c87b-5e70-ba41-1bf66ade02ff"
    )
    assert identity["child_turn_id"] == "7a7859966d71596fa9ab7d17b75526bc"
    assert identity["child_trace_id"] == "12f427c8b6a85a5aa12ccdde03333ece"
    assert identity["child_thread_id"] == "b072c692df6c5afdb875d5b84f28250d"
    assert identity["budget_lease_id"] == "5c544596151f5b6b8266a8055e774b1b"


@pytest.mark.asyncio
async def test_v6_create_get_is_single_head_audit_only_and_hydrates(tmp_path):
    path, root, repo, clock, snapshot = await _seed_parent(tmp_path)
    first = await repo.create_or_get_continuation_v6(
        "11111111-1111-1111-1111-111111111111", "caller-a"
    )
    second = await ResearchWorkflowRepository(path, clock=clock, blob_root=root).create_or_get_continuation_v6(
        first.parent_run_id, "caller-b"
    )
    replay = await repo.create_or_get_continuation_v6(first.parent_run_id, "caller-a")
    assert first.created is True and second.created is False and replay.created is True
    assert first.child_run_id == second.child_run_id == replay.child_run_id
    assert first.start_payload == second.start_payload
    assert first.start_request_hash == second.start_request_hash
    assert first.audit_operation_id != second.audit_operation_id
    assert await repo.load_continuation_snapshot_v6(first.child_run_id) == snapshot
    async with aiosqlite.connect(path) as db:
        assert (await (await db.execute("SELECT COUNT(*) FROM workflow_research_continuation_heads")).fetchone())[0] == 1
        await db.execute("UPDATE workflow_research_snapshot_pins SET expires_at=999 WHERE pin_kind='continue_parent'")
        await db.execute(
            "UPDATE workflow_events SET payload_json=? WHERE event_id='terminal-event'",
            (_canonical({"manifest_ref": "sha256:" + "0" * 64, "answer_status": "completed"}),),
        )
        await db.commit()
    third = await repo.create_or_get_continuation_v6(first.parent_run_id, "caller-c")
    assert third.child_run_id == first.child_run_id and third.created is False


@pytest.mark.asyncio
async def test_v6_two_connections_race_to_one_child(tmp_path):
    path, root, _, clock, snapshot = await _seed_parent(tmp_path)
    one = ResearchWorkflowRepository(path, clock=clock, blob_root=root)
    two = ResearchWorkflowRepository(path, clock=clock, blob_root=root)
    results = await asyncio.gather(
        one.create_or_get_continuation_v6("11111111-1111-1111-1111-111111111111", "race-a"),
        two.create_or_get_continuation_v6("11111111-1111-1111-1111-111111111111", "race-b"),
    )
    assert len({result.child_run_id for result in results}) == 1
    assert sorted(result.created for result in results) == [False, True]
    expected_payload = {
        "schema_version": 1,
        "parent_run_id": "11111111-1111-1111-1111-111111111111",
        "source_snapshot_hash": "b9b8b74f573e176f545804cf05a0441d9d6544d0b05b76dee7968792ea5f1936",
    }
    assert snapshot["snapshot_hash"] == expected_payload["source_snapshot_hash"]
    for result in results:
        assert result.parent_run_id == expected_payload["parent_run_id"]
        assert result.child_run_id == "2c2bba0d-c87b-5e70-ba41-1bf66ade02ff"
        assert result.child_operation_id == "research:2c2bba0d-c87b-5e70-ba41-1bf66ade02ff"
        assert result.start_payload == expected_payload
        assert result.start_request_hash == (
            "ababeb3e8f0b9ee2c4c0c66047b21e5c5f931a4cd3d86ae2b3e8c753a1dc5c11"
        )
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        start_request = await (
            await db.execute(
                "SELECT * FROM workflow_start_requests WHERE run_id=?",
                (results[0].child_run_id,),
            )
        ).fetchone()
        start_operation = await (
            await db.execute(
                "SELECT * FROM workflow_operations WHERE operation_id=?",
                ("research:2c2bba0d-c87b-5e70-ba41-1bf66ade02ff:start",),
            )
        ).fetchone()
        run = await (
            await db.execute(
                "SELECT request_id,turn_id,trace_id,thread_id,capability_hash FROM workflow_runs WHERE run_id=?",
                (results[0].child_run_id,),
            )
        ).fetchone()
        capability = await (
            await db.execute(
                "SELECT snapshot_json FROM workflow_capabilities WHERE capability_hash=?",
                (run["capability_hash"],),
            )
        ).fetchone()
    identity = json.loads(capability["snapshot_json"])["_workflow_start"]["identity"]
    assert start_request["request_key"] == (
        "research-continuation-v6:11111111-1111-1111-1111-111111111111:1"
    )
    assert start_request["request_id"] == run["request_id"] == (
        "continue:v6:2c2bba0d-c87b-5e70-ba41-1bf66ade02ff"
    )
    assert identity["logical_slot"] == start_request["request_key"]
    assert start_operation["request_hash"] == results[0].start_request_hash
    assert json.loads(start_operation["result_json"]) == expected_payload
    assert run["turn_id"] == "7a7859966d71596fa9ab7d17b75526bc"
    assert run["trace_id"] == "12f427c8b6a85a5aa12ccdde03333ece"
    assert run["thread_id"] == "b072c692df6c5afdb875d5b84f28250d"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", (
    "after_capability_insert", "after_run_insert", "after_audit_operation_insert",
    "after_start_request_insert", "after_session_refs_insert", "after_lineage_insert",
    "after_snapshot_pin_insert", "after_start_operation_insert",
    "after_inherited_staging_owners_insert", "after_continuation_head_insert", "before_commit",
))
async def test_v6_new_branch_fault_points_fully_roll_back(tmp_path, stage):
    path, root, _, clock, _ = await _seed_parent(tmp_path)
    async def fail(point):
        if point == stage:
            raise RuntimeError(stage)
    repo = ResearchWorkflowRepository(path, clock=clock, blob_root=root, fault_injector=fail)
    with pytest.raises(RuntimeError, match=stage):
        await repo.create_or_get_continuation_v6(
            "11111111-1111-1111-1111-111111111111", "fault"
        )
    child = "2c2bba0d-c87b-5e70-ba41-1bf66ade02ff"
    async with aiosqlite.connect(path) as db:
        for table in ("workflow_research_continuation_heads", "workflow_research_lineage",
                      "workflow_operations", "workflow_start_requests", "workflow_session_refs"):
            column = "child_run_id" if table == "workflow_research_continuation_heads" else "run_id"
            count = (await (await db.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (child,))).fetchone())[0]
            assert count == 0
        assert (await (await db.execute("SELECT COUNT(*) FROM workflow_blob_refs WHERE owner_kind='run_staging' AND owner_id=?", (child,))).fetchone())[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ("after_audit_operation_insert", "before_commit"))
async def test_v6_existing_branch_fault_rolls_back_only_new_audit(tmp_path, stage):
    path, root, repo, clock, _ = await _seed_parent(tmp_path)
    first = await repo.create_or_get_continuation_v6(
        "11111111-1111-1111-1111-111111111111", "first"
    )
    async def fail(point):
        if point == stage:
            raise RuntimeError(stage)
    crashing = ResearchWorkflowRepository(path, clock=clock, blob_root=root, fault_injector=fail)
    with pytest.raises(RuntimeError, match=stage):
        await crashing.create_or_get_continuation_v6(first.parent_run_id, "new-audit")
    audit_id = hashlib.sha256(
        ("deep-research-v6-continuation-audit-v1|" + first.parent_run_id + "|new-audit").encode()
    ).hexdigest()
    async with aiosqlite.connect(path) as db:
        assert (await (await db.execute("SELECT COUNT(*) FROM workflow_operations WHERE operation_id=?", (audit_id,))).fetchone())[0] == 0
        assert (await (await db.execute("SELECT COUNT(*) FROM workflow_research_continuation_heads WHERE child_run_id=?", (first.child_run_id,))).fetchone())[0] == 1


@pytest.mark.asyncio
async def test_v6_arbitrary_same_parent_blob_is_not_inherited(tmp_path):
    path, root, repo, _, snapshot = await _seed_parent(tmp_path)
    arbitrary = await _put(path, root, {"arbitrary": True})
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO workflow_blob_refs(sha256,owner_kind,owner_id,created_at) VALUES(?,'checkpoint','terminal-head',999)",
            (arbitrary[7:],),
        )
        await db.commit()
    result = await repo.create_or_get_continuation_v6(
        "11111111-1111-1111-1111-111111111111", "arbitrary"
    )
    assert arbitrary not in snapshot["closure_refs"]
    async with aiosqlite.connect(path) as db:
        inherited = await (
            await db.execute(
                """SELECT 1 FROM workflow_blob_refs
                WHERE sha256=? AND owner_kind='run_staging' AND owner_id=?""",
                (arbitrary[7:], result.child_run_id),
            )
        ).fetchone()
    assert inherited is None


async def _publish_mutated_snapshot(path, root, snapshot, mutate):
    async with aiosqlite.connect(path) as db:
        row = await (
            await db.execute(
                "SELECT payload_json FROM workflow_events WHERE event_id='terminal-event'"
            )
        ).fetchone()
    old_manifest = json.loads(
        BlobStore(root).get(json.loads(row[0])["manifest_ref"][7:]).decode()
    )
    base = dict(snapshot)
    base.pop("snapshot_id")
    base.pop("snapshot_hash")
    mutate(base)
    snapshot_hash = hashlib.sha256(_canonical(base).encode()).hexdigest()
    changed = {
        **base,
        "snapshot_id": "rcs_" + snapshot_hash[:24],
        "snapshot_hash": snapshot_hash,
    }
    snapshot_ref = await _put(path, root, changed)
    manifest_args = dict(old_manifest)
    manifest_args.pop("schema_version")
    manifest_args.pop("manifest_id")
    manifest_args["continuation_snapshot_ref"] = snapshot_ref
    manifest_args["continuation_snapshot_hash"] = snapshot_hash
    manifest = TerminalDeliveryManifestV1.create(**manifest_args)
    manifest_ref = await _put(path, root, manifest.to_json())
    async with aiosqlite.connect(path) as db:
        for ref in (snapshot_ref, manifest_ref, *changed["closure_refs"]):
            await db.execute(
                "INSERT OR IGNORE INTO workflow_blob_refs(sha256,owner_kind,owner_id,created_at) "
                "VALUES(?,'checkpoint','terminal-head',999)",
                (ref[7:],),
            )
        await db.execute(
            "UPDATE workflow_events SET payload_json=? WHERE event_id='terminal-event'",
            (_canonical({"manifest_ref": manifest_ref, "answer_status": "partial"}),),
        )
        await db.commit()
    return manifest_ref


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "error"),
    (
        ("extra", "v6_snapshot_provenance_invalid"),
        ("missing", "v6_snapshot_provenance_invalid"),
        ("wrong_class", "v6_snapshot_ref_class_invalid"),
    ),
)
async def test_v6_snapshot_recomputes_exact_transitive_closure(tmp_path, case, error):
    path, root, repo, _, snapshot = await _seed_parent(tmp_path)
    arbitrary = await _put(path, root, {"schema_version": 1, "arbitrary": True})

    def mutate(base):
        fact_ref = snapshot["provenance_refs"][0]
        if case == "extra":
            base["provenance_refs"] = sorted([*base["provenance_refs"], arbitrary])
            base["closure_refs"] = sorted([*base["closure_refs"], arbitrary])
        elif case == "missing":
            base["provenance_refs"] = []
            base["closure_refs"] = [ref for ref in base["closure_refs"] if ref != fact_ref]
        else:
            policy_ref = base["policy_refs"]["compiler"]
            base["provenance_refs"] = sorted([fact_ref, policy_ref])

    manifest_ref = await _publish_mutated_snapshot(path, root, snapshot, mutate)
    with pytest.raises(ResearchRepositoryError) as caught:
        await repo.persist_v6_continuation_snapshot(
            run_id=snapshot["run_id"], operation_id="research:" + snapshot["run_id"],
            terminal_manifest_ref=manifest_ref, continue_until=2_000.0,
        )
    assert caught.value.code == error


@pytest.mark.asyncio
async def test_v6_repository_child_terminal_snapshot_creates_hydratable_grandchild(tmp_path):
    path, root, repo, _, parent_snapshot = await _seed_parent(tmp_path)
    child = await repo.create_or_get_continuation_v6(
        parent_snapshot["run_id"], "child-create"
    )
    spec = json.loads(BlobStore(root).get(parent_snapshot["spec_ref"][7:]).decode())
    old_batch = json.loads(
        BlobStore(root).get(parent_snapshot["fact_batch_refs"][-1][7:]).decode()
    )
    policy_refs = dict(parent_snapshot["policy_refs"])
    requirement_id = str(spec["requirements"][0]["requirement_id"])
    child_fact = AdmittedResearchFactV1.create(
        run_id=child.child_run_id, spec_hash=parent_snapshot["spec_hash"],
        requirement_id=requirement_id, target_kind="scalar", item_or_cell_id=None,
        field_or_facet_key=None, candidate_id="ecd-child", page_id="page-child",
        span_id="span-child", binding_id="binding-child",
        source_family_id="official-child", source_tier="first_party",
        admission_policy_hash=parse_blob_ref(policy_refs["admission"]), status="admitted",
        semantic_payload={
            "value": 140829, "canonical_unit": "ten_thousand_persons",
            "time_scope": "2025", "scope": "China", "definition": "test continuation fact",
        },
    )
    child_fact_ref = await _put(path, root, child_fact.to_json())
    child_batch = EvidenceFactBatchV1.create(
        batch_kind="facts", run_id=child.child_run_id,
        spec_hash=parent_snapshot["spec_hash"],
        previous_head_hash=parent_snapshot["evidence_head_hash"], ordinal=1,
        page_result_refs=(), candidate_slot_results=(), inference_slot_results=(),
        admitted_fact_refs=(child_fact_ref,), registered_inference_refs=(),
        rejected_candidate_ids=(), conflict_ids=(), provenance_refs=(child_fact_ref,),
        policy_refs=policy_refs,
    )
    child_batch_ref = await _put(path, root, child_batch.to_json())
    ordered_facts = [parent_snapshot["provenance_refs"][0], child_fact_ref]
    assessment = AnswerAssessmentV1.create(
        spec_hash=parent_snapshot["spec_hash"], evidence_head_hash=child_batch.head_hash,
        requirement_results=(), missing_requirement_ids=(requirement_id,),
        minimum_useful=False, status="insufficient", reason_codes=("required_evidence_missing",),
        policy_hash=parse_blob_ref(policy_refs["assessment"]),
        ordered_fact_refs=ordered_facts, ordered_inference_refs=(),
    )
    assessment_ref = await _put(path, root, assessment.to_json())
    claim = ClaimBatchV1.create(
        run_id=child.child_run_id, spec_hash=parent_snapshot["spec_hash"],
        evidence_head_hash=child_batch.head_hash, assessment_hash=assessment.assessment_hash,
        claim_policy_hash=parse_blob_ref(policy_refs["claim"]), claims=(), status="valid",
    )
    claim_ref = await _put(path, root, claim.to_json())
    provenance = sorted({*parent_snapshot["provenance_refs"], child_fact_ref})
    child_snapshot_base = {
        "schema_version": 1, "run_id": child.child_run_id,
        "workflow_name": "deep_research", "workflow_version": "v6",
        "spec_ref": parent_snapshot["spec_ref"], "spec_hash": parent_snapshot["spec_hash"],
        "fact_batch_refs": [*parent_snapshot["fact_batch_refs"], child_batch_ref],
        "evidence_head_hash": child_batch.head_hash, "assessment_ref": assessment_ref,
        "assessment_hash": assessment.assessment_hash,
        "assessment_input_hash": assessment.assessment_input_hash,
        "claim_batch_ref": claim_ref, "provenance_refs": provenance,
        "policy_refs": policy_refs,
        "closure_refs": sorted({
            parent_snapshot["spec_ref"], *parent_snapshot["fact_batch_refs"], child_batch_ref,
            assessment_ref, claim_ref, *provenance, *policy_refs.values(),
        }),
    }
    child_snapshot_hash = hashlib.sha256(_canonical(child_snapshot_base).encode()).hexdigest()
    child_snapshot = {
        **child_snapshot_base, "snapshot_id": "rcs_" + child_snapshot_hash[:24],
        "snapshot_hash": child_snapshot_hash,
    }
    child_snapshot_ref = await _put(path, root, child_snapshot)
    final_ref = await _put(path, root, {"text": "continued answer"})
    report_ref = await _put(path, root, {"text": "continued report"})
    quality_ref = await _put(path, root, {"hard_gate_status": "passed"})
    manifest = TerminalDeliveryManifestV1.create(
        workflow_name="deep_research", workflow_version="v6", run_id=child.child_run_id,
        answer_status="partial", spec_hash=parent_snapshot["spec_hash"],
        assessment_hash=assessment.assessment_hash,
        claim_policy_hash=parse_blob_ref(policy_refs["claim"]),
        quality_policy_hash=parse_blob_ref(policy_refs["quality"]),
        continuation_snapshot_ref=child_snapshot_ref,
        continuation_snapshot_hash=child_snapshot_hash,
        content_refs={
            "final_assistant_ref": final_ref, "report_ref": report_ref,
            "safe_summary_ref": None, "claim_batch_ref": claim_ref,
            "quality_audit_ref": quality_ref,
        },
        intent_specs=build_intent_specs(
            run_id=child.child_run_id, final_assistant_ref=final_ref,
            report_ref=report_ref, artifact_required=True,
        ),
        cardinality={
            "final_assistant": 1, "workflow_final_status": 1, "report": 1,
            "artifact": 1, "run_terminal": 1,
        },
        engine_terminal={"status": "completed", "error_code": None, "recovery_action": None},
    )
    manifest_ref = await _put(path, root, manifest.to_json())
    terminal_refs = {
        manifest_ref, child_snapshot_ref, *child_snapshot["closure_refs"],
        final_ref, report_ref, quality_ref,
    }
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        run = await (
            await db.execute(
                "SELECT thread_id,checkpoint_ns,source_checkpoint_id FROM workflow_runs WHERE run_id=?",
                (child.child_run_id,),
            )
        ).fetchone()
        await db.execute(
            "UPDATE workflow_runs SET status='completed',head_checkpoint_id='terminal-child',"
            "ended_at=1100,updated_at=1100 WHERE run_id=?", (child.child_run_id,),
        )
        await db.execute(
            """INSERT INTO workflow_checkpoints(
            thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,checkpoint_type,
            checkpoint_blob,metadata_blob,engine_kind,snapshot_version,created_at)
            VALUES(?,?,?,?,?,'native',X'7B7D',X'7B7D','deskpet-native',1,1100)""",
            (run["thread_id"], run["checkpoint_ns"], "terminal-child",
             run["source_checkpoint_id"], child.child_run_id),
        )
        await db.execute(
            """INSERT INTO workflow_checkpoint_owners(
            run_id,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at)
            VALUES(?,?,?,?,?,1100)""",
            (child.child_run_id, run["thread_id"], run["checkpoint_ns"],
             "terminal-child", run["source_checkpoint_id"]),
        )
        for ref in terminal_refs:
            await db.execute(
                "INSERT OR IGNORE INTO workflow_blob_refs(sha256,owner_kind,owner_id,created_at) "
                "VALUES(?,'checkpoint','terminal-child',1100)", (ref[7:],),
            )
        await db.execute(
            """INSERT INTO workflow_events(
            event_id,event_key,run_id,seq,event_type,payload_json,created_at)
            VALUES('terminal-event-child','run:terminal',?,1,'workflow.final',?,1100)""",
            (child.child_run_id, _canonical({
                "manifest_ref": manifest_ref, "answer_status": "partial",
            })),
        )
        await db.commit()
    await repo.persist_v6_continuation_snapshot(
        run_id=child.child_run_id, operation_id=child.child_operation_id,
        terminal_manifest_ref=manifest_ref, continue_until=2_000.0,
    )
    grandchild = await repo.create_or_get_continuation_v6(
        child.child_run_id, "grandchild-create"
    )
    inherited = await repo.load_continuation_snapshot_v6(grandchild.child_run_id)
    assert inherited == child_snapshot
    assert inherited["spec_ref"] == parent_snapshot["spec_ref"]
    assert inherited["fact_batch_refs"][:-1] == parent_snapshot["fact_batch_refs"]
    assert inherited["evidence_head_hash"] == child_batch.head_hash
    assert set(parent_snapshot["provenance_refs"]).issubset(inherited["closure_refs"])
    assert set(policy_refs.values()).issubset(inherited["closure_refs"])


@pytest.mark.asyncio
async def test_v6_generate_now_clamps_to_automatic_wall_guard_and_cancel_obeys_it(tmp_path):
    path = tmp_path / "deadline.db"
    clock = Clock()
    store = WorkflowRunStore(path, clock=clock)
    await store.create_run(
        request_key="deadline-root", session_id="session-1", request_id="request-1",
        turn_id="turn-1", workflow_name="deep_research", workflow_version="v6",
        manifest_hash="manifest-v6", implementation_hash="implementation-v6",
        capability_hash="capability-v6", capability_snapshot={"research": True},
        state_schema_version=6, run_id="deadline-run", trace_id="trace", thread_id="thread",
    )
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        for checkpoint, parent in (("brief", None), ("head", "brief")):
            await db.execute(
                """INSERT INTO workflow_checkpoints(
                thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
                checkpoint_type,checkpoint_blob,metadata_blob,engine_kind,snapshot_version,created_at)
                VALUES('thread','',?,?,?,'native',X'7B7D',X'7B7D','deskpet-native',1,1000)""",
                (checkpoint, parent, "deadline-run"),
            )
            await db.execute(
                """INSERT INTO workflow_checkpoint_owners(
                run_id,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at)
                VALUES(?,'thread','',?,?,1000)""", ("deadline-run", checkpoint, parent),
            )
        await db.execute(
            """UPDATE workflow_runs SET status='running',run_version=7,head_checkpoint_id='head',
            head_checkpoint_ns='',started_at=1000,updated_at=1000 WHERE run_id='deadline-run'"""
        )
        await db.execute(
            """INSERT INTO workflow_research_deadlines(
            deadline_id,schema_version,run_id,parent_deadline_id,logical_scope,policy_hash,
            budget_ms,remaining_ms,created_at,last_observed_at,wall_not_after,offline_policy,
            rollback_tolerance_ms,revision,status)
            VALUES('deadline-id',1,'deadline-run',NULL,'run:automatic',?,900000,900000,
            1000,1000,1900,'count',0,0,'open')""", ("a" * 64,),
        )
        await db.commit()
    repo = ResearchWorkflowRepository(path, clock=clock)
    command, _ = await repo.open_control(
        run_id="deadline-run", idempotency_key="generate", action="generate_now",
        expected_run_version=7, expected_head_checkpoint_ns="", expected_head_checkpoint_id="head",
    )
    clock.value = 1895.0
    accepted = await repo.accept_generate_now(
        command["command_id"], brief_checkpoint_ns="", brief_checkpoint_id="brief"
    )
    assert accepted["settle_deadline"] == 1900.0
    assert (await repo.request_cancel_settle(
        "deadline-run", idempotency_key="cancel", expected_run_version=7
    ))["action"] == "cancel_settle"
    clock.value = 1900.0
    with pytest.raises(Exception, match="wall guard"):
        await repo.request_cancel_settle(
            "deadline-run", idempotency_key="cancel", expected_run_version=7
        )


@pytest.mark.asyncio
async def test_v6_completed_answer_cannot_create_first_continuation_head(tmp_path):
    path, root, repo, _, _ = await _seed_parent(tmp_path)
    async with aiosqlite.connect(path) as db:
        payload_row = await (
            await db.execute(
                "SELECT payload_json FROM workflow_events WHERE event_id='terminal-event'"
            )
        ).fetchone()
    payload = json.loads(payload_row[0])
    partial = json.loads(BlobStore(root).get(payload["manifest_ref"][7:]).decode())
    values = dict(partial)
    values.pop("schema_version")
    values.pop("manifest_id")
    values["answer_status"] = "completed"
    completed = TerminalDeliveryManifestV1.create(**values)
    completed_ref = await _put(path, root, completed.to_json())
    assert completed_ref == completed.manifest_ref
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO workflow_blob_refs(sha256,owner_kind,owner_id,created_at) VALUES(?,'checkpoint','terminal-head',999)",
            (completed_ref[7:],),
        )
        await db.execute(
            "UPDATE workflow_events SET payload_json=? WHERE event_id='terminal-event'",
            (_canonical({"manifest_ref": completed_ref, "answer_status": "completed"}),),
        )
        await db.commit()
    with pytest.raises(ResearchRepositoryError, match="does not admit a continuation"):
        await repo.create_or_get_continuation_v6(
            "11111111-1111-1111-1111-111111111111", "completed"
        )
