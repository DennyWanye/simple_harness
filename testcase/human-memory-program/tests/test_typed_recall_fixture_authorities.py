"""Fixture-construction regressions against installed public packages, not 401 cells.

Run directly with the isolated installed-wheel Python, or collect with pytest.
No Memory test fixtures, SDK internals, SQL, or candidate oracle are imported.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import simple_harness as h
import simple_harness_memory as m

HELPER_PATH = (
    Path(__file__).resolve().parents[1] / "adapters/typed_recall_fixture_authorities.py"
)
_spec = importlib.util.spec_from_file_location(
    "typed_recall_fixture_authorities", HELPER_PATH
)
fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixtures)


def canonical_hash(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def raw_fixture(
    evidence_id="fixture-1", *, assistant=False, text="已核实：偏好简短回复。"
):
    disclosure = h.DisclosureContext(
        run_id="fixture-run",
        subject="fixture-user",
        recipient=h.DeliveryRecipient.USER_SELF,
        recipient_id="fixture-user",
        intended_audience=h.IntendedAudience.USER_SELF,
        purpose=h.DisclosurePurpose.PERSONALIZATION,
        source=h.DisclosureSource.AUTHENTICATED_HOST,
        trust=h.DisclosureTrust.TRUSTED_AUTHORITY,
        generation=h.DisclosureGeneration.CURRENT,
        authority_ref="fixture-disclosure",
        reason_codes=(h.DisclosureReasonCode.MINIMUM_NECESSARY,),
    )
    payload = {"item_id": evidence_id + "-item", "public_text": text}
    source_hash = hashlib.sha256(text.encode()).hexdigest()
    env = h.SanitizedEvidenceEnvelope(
        evidence_id=evidence_id,
        run_id=disclosure.run_id,
        subject=disclosure.subject,
        source_kind=h.EvidenceSourceKind.ASSISTANT_MESSAGE
        if assistant
        else h.EvidenceSourceKind.USER_MESSAGE,
        source_ref=evidence_id + "/raw",
        source_hash=source_hash,
        sanitized_payload=payload,
        sanitized_hash=canonical_hash(payload),
        filter_policy_version="credential-filter/v1",
        removed_spans=(),
        disclosure_context=disclosure,
        evidence_refs=(),
    )
    receipt = h.SanitizedEvidenceReceipt(
        receipt_id=evidence_id + "-admission",
        run_id=env.run_id,
        subject=env.subject,
        evidence_id=env.evidence_id,
        envelope_hash=env.envelope_hash,
        source_hash=env.source_hash,
        sanitized_hash=env.sanitized_hash,
        filter_policy_version=env.filter_policy_version,
        accepted=True,
        reason_codes=(h.EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
        disclosure_context=env.disclosure_context,
        evidence_refs=(),
        admitted_at=0.0,
    )
    span = h.EvidenceSpanRef(
        span_id=evidence_id + "-span",
        evidence_id=env.evidence_id,
        envelope_hash=env.envelope_hash,
        sanitized_hash=env.sanitized_hash,
        admission_receipt_id=receipt.receipt_id,
        admission_receipt_hash=receipt.receipt_hash,
        source_kind=env.source_kind,
        item_ordinal=1,
        item_id=payload["item_id"],
        item_json_pointer="/public_text",
        start_byte=0,
        end_byte=len(text.encode()),
        exact_quote=text,
        quote_hash=source_hash,
        source_hash=source_hash,
        normalization_version=h.EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1,
        actor_role=h.EvidenceActorRole.ASSISTANT
        if assistant
        else h.EvidenceActorRole.USER,
        provenance=h.EvidenceProvenance.MODEL_OUTPUT
        if assistant
        else h.EvidenceProvenance.AUTHENTICATED_USER,
        support_kind=h.EvidenceSupportKind.MODEL_INFERENCE
        if assistant
        else h.EvidenceSupportKind.EXPLICIT_USER_ASSERTION,
        typed_observation=None,
    )
    return env, receipt, span


def item_authority(span):
    return h.EvidenceItemAuthority(
        schema_version=h.EVIDENCE_ITEM_AUTHORITY_SCHEMA_VERSION,
        authority_id=span.evidence_id + "-authority",
        evidence_id=span.evidence_id,
        envelope_hash=span.envelope_hash,
        sanitized_hash=span.sanitized_hash,
        source_hash=span.source_hash,
        source_kind=span.source_kind,
        item_ordinal=span.item_ordinal,
        item_id=span.item_id,
        item_json_pointer=span.item_json_pointer,
        normalization_version=span.normalization_version,
        actor_role=span.actor_role,
        provenance=span.provenance,
        required_privacy_class=h.PrivacyClass.PERSONAL,
        required_information_attributes=(),
        classification_authority_ref="fixture-classification",
        issuer_ref="fixture-evidence-authority",
    )


class FixtureAuthority:
    def __init__(self, records, typed):
        self.admitted = {
            env.evidence_id: h.AdmittedEvidenceAuthority(
                env, receipt, item_authority(span)
            )
            for env, receipt, span in records
        }
        # Registered trusted fixtures, never receipts supplied by the proposed plan.
        self.typed = {receipt.receipt_id: receipt for receipt in typed}
        self.typed_calls = []

    async def resolve_admitted_evidence(self, span):
        return self.admitted[span.evidence_id]

    async def resolve_typed_observation(self, reference):
        self.typed_calls.append(reference)
        if reference.observation_receipt_id not in self.typed:
            raise ValueError("fixture typed observation not registered")
        # Return even a tampered fixture in negative tests: the real SDK must
        # detect the mismatch, rather than a mock doing its validation for it.
        return self.typed[reference.observation_receipt_id]

    async def resolve_memory_action_authority(self, reference):
        raise ValueError("no memory action authority registered")


def operation(spans, epistemic, verification):
    return h.MemoryMutationOperation(
        operation_id="fixture-create",
        kind=h.MemoryMutationKind.CREATE,
        memory_type=h.LongTermMemoryType.SEMANTIC,
        payload=h.SemanticMemoryPayload("user:self", "response_style", "concise", ()),
        target=None,
        depends_on_operation_ids=(),
        lifecycle_state=h.SemanticLifecycleState.ACTIVE,
        epistemic_status=h.EpistemicStatus(epistemic),
        conflict_status=h.ConflictStatus.UNCONTESTED,
        verification_state=h.VerificationState(verification),
        valid_time_interval=h.ValidTimeInterval(None, None),
        proposed_privacy_class=h.PrivacyClass.PERSONAL,
        proposed_information_attributes=(),
        evidence_spans=tuple(spans),
        reason_code="typed_fixture_observation",
    )


def plan_for(records, epistemic, verification):
    env = records[0][0]
    return h.MemoryMutationPlan(
        plan_id="fixture-plan",
        run_id=env.run_id,
        turn_id="fixture-turn",
        subject=env.subject,
        base_revision=1,
        outcome=h.MemoryMutationPlanOutcome.MUTATE,
        operations=(
            operation([record[2] for record in records], epistemic, verification),
        ),
        disclosure_context=env.disclosure_context,
        evidence_refs=tuple(
            h.EvidenceRef(record[0].evidence_id, record[0].envelope_hash, ordinal)
            for ordinal, record in enumerate(records, 1)
        ),
        idempotency_key="fixture-plan-idem",
    )


class FixtureAuthoritiesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="typed-authority-")
        self.addCleanup(self.temp.cleanup)
        self.principal = m.MemoryPrincipal(
            "fixture-deployment", "fixture-household", "fixture-user", "fixture-session"
        )
        self.db_index = 0

    async def open_manager(self, records, typed):
        self.db_index += 1
        authority = FixtureAuthority(records, typed)
        manager = await m.build_human_memory_v7(
            Path(self.temp.name) / f"case-{self.db_index}.db",
            clock=lambda: 20.0,
            evidence_authority=authority,
            classification_policy=m.InformationClassificationPolicy(
                policy_id="fixture-policy",
                policy_version="1",
                authority_ref="fixture-policy-authority",
                required_privacy_class=h.PrivacyClass.PERSONAL,
                required_information_attributes=(),
            ),
        )
        self.addAsyncCleanup(manager.close)
        await manager.register_principal_owner(
            self.principal, m.MemoryScope.personal(self.principal.actor_id)
        )
        for env, receipt, _span in records:
            await manager.ingest_committed_evidence(env, receipt)
        return manager, authority

    async def apply_and_check(self, manager, plan):
        result = await manager.apply_memory_mutation_plan(
            principal=self.principal,
            scope=m.MemoryScope.personal(self.principal.actor_id),
            plan=plan,
        )
        self.assertIs(result.outcome, h.MemoryMutationApplyOutcome.COMMITTED)
        self.assertIsNotNone(result.receipt_ref)
        view = await manager.get_memory_mutation_receipt_view(
            principal=self.principal, receipt_ref=result.receipt_ref
        )
        self.assertEqual(len(view.operations), 1)
        self.assertEqual(
            view.operations[0].epistemic_status,
            plan.operations[0].epistemic_status.value,
        )
        self.assertEqual(
            set(view.operations[0].evidence_ids),
            {s.evidence_id for s in plan.operations[0].evidence_spans},
        )
        self.assertEqual(view.operations[0].revision, 1)
        return result

    def test_installed_versions_and_independent_schema_hash(self):
        self.assertEqual(
            importlib.metadata.version("simple-harness-memory-sdk"), "0.6.31"
        )
        self.assertEqual(importlib.metadata.version("simple-harness-sdk"), "0.7.10")
        for package in (h, m):
            self.assertTrue(
                Path(package.__file__)
                .resolve()
                .is_relative_to(Path(sys.prefix).resolve())
            )
        schema = {
            "type": "string",
            "description": "Admitted public memory assertion text",
        }
        self.assertEqual(fixtures.typed_observation_schema(), schema)
        self.assertEqual(fixtures.SCHEMA_HASH, canonical_hash(schema))
        copy = fixtures.typed_observation_schema()
        copy["type"] = "integer"
        self.assertEqual(fixtures.typed_observation_schema()["type"], "string")

    def test_rebinding_preserves_text_partial_utf8_span_and_legitimate_zero_time(self):
        env, receipt, span = raw_fixture(text="证据：简短回复。🙂")
        quote = "简短回复"
        start = len("证据：".encode())
        span = replace(
            span,
            start_byte=start,
            end_byte=start + len(quote.encode()),
            exact_quote=quote,
            quote_hash=hashlib.sha256(quote.encode()).hexdigest(),
        )
        old = (env.to_json(), receipt.to_json(), span.to_json())
        new_env, new_receipt, new_span, typed = fixtures.bind_typed_observation_fixture(
            env,
            receipt,
            span,
            epistemic_status="verified_external",
            verification_state="source_verified",
        )
        self.assertEqual(old, (env.to_json(), receipt.to_json(), span.to_json()))
        self.assertEqual(new_env.sanitized_payload, env.sanitized_payload)
        self.assertEqual(new_env.source_hash, env.source_hash)
        self.assertNotEqual(new_env.envelope_hash, env.envelope_hash)
        self.assertNotEqual(new_receipt.receipt_hash, receipt.receipt_hash)
        self.assertEqual(new_receipt.admitted_at, 0.0)
        self.assertEqual(
            (new_span.start_byte, new_span.end_byte, new_span.exact_quote),
            (span.start_byte, span.end_byte, quote),
        )
        self.assertEqual(
            typed.value_hash, canonical_hash(env.sanitized_payload["public_text"])
        )
        self.assertNotEqual(typed.value_hash, canonical_hash(quote))
        self.assertEqual(
            new_span.typed_observation, fixtures.typed_observation_ref(typed)
        )
        self.assertEqual(typed.envelope_hash, new_env.envelope_hash)
        self.assertEqual(typed.admission_receipt_hash, new_receipt.receipt_hash)
        self.assertEqual(new_span.admission_receipt_hash, new_receipt.receipt_hash)

    async def test_external_and_internal_verified_fixtures_commit_through_public_manager(
        self,
    ):
        for epistemic, verification in (
            ("verified_external", "source_verified"),
            ("observed_behavior", "source_verified"),
            ("observed_behavior", "repeated_observation"),
        ):
            with self.subTest(epistemic=epistemic, verification=verification):
                env, receipt, span, typed = fixtures.bind_typed_observation_fixture(
                    *raw_fixture(),
                    epistemic_status=epistemic,
                    verification_state=verification,
                )
                external = epistemic == "verified_external"
                self.assertIs(
                    env.source_kind,
                    h.EvidenceSourceKind.PROVIDER_RECORD
                    if external
                    else h.EvidenceSourceKind.TOOL_RESULT,
                )
                self.assertIs(
                    span.actor_role,
                    h.EvidenceActorRole.EXTERNAL
                    if external
                    else h.EvidenceActorRole.TOOL,
                )
                self.assertIs(
                    span.provenance,
                    h.EvidenceProvenance.EXTERNAL_SOURCE
                    if external
                    else h.EvidenceProvenance.TRUSTED_TOOL,
                )
                self.assertIs(
                    span.support_kind, h.EvidenceSupportKind.TYPED_OBSERVATION
                )
                records = [(env, receipt, span)]
                manager, authority = await self.open_manager(records, [typed])
                await self.apply_and_check(
                    manager, plan_for(records, epistemic, verification)
                )
                self.assertTrue(authority.typed_calls)
                self.assertTrue(
                    all(ref == span.typed_observation for ref in authority.typed_calls)
                )

    async def test_internal_verification_retains_independent_user_support(
        self,
    ):
        for epistemic in ("explicit_user",):
            for verification in ("source_verified", "repeated_observation"):
                with self.subTest(epistemic=epistemic, verification=verification):
                    original = raw_fixture("original")
                    env, receipt, span, typed = fixtures.bind_typed_observation_fixture(
                        *raw_fixture("independent-tool-observation"),
                        epistemic_status=epistemic,
                        verification_state=verification,
                    )
                    with self.assertRaises(ValueError):
                        operation((span,), epistemic, verification)
                    records = [original, (env, receipt, span)]
                    manager, _authority = await self.open_manager(records, [typed])
                    await self.apply_and_check(
                        manager, plan_for(records, epistemic, verification)
                    )

    async def test_inference_and_unknown_cannot_be_verified_even_with_a_typed_receipt(
        self,
    ):
        for epistemic, reason in (
            ("llm_inference", "mutation_inference_must_be_unverified"),
            ("unknown", "mutation_unknown_must_be_unverified"),
        ):
            with self.subTest(epistemic=epistemic):
                original = raw_fixture(
                    "original", assistant=epistemic == "llm_inference"
                )
                with self.assertRaisesRegex(
                    ValueError, "inference/unknown cannot use a verified fixture"
                ):
                    fixtures.bind_typed_observation_fixture(
                        *original,
                        epistemic_status=epistemic,
                        verification_state="source_verified",
                    )
                # Deliberately assemble a proposal outside the helper's supported
                # matrix: real Memory must still reject, even with trusted support.
                env, receipt, span, typed = fixtures.bind_typed_observation_fixture(
                    *raw_fixture("independent-tool"),
                    epistemic_status="observed_behavior",
                    verification_state="source_verified",
                )
                records = [original, (env, receipt, span)]
                plan = plan_for(records, epistemic, "source_verified")
                plan = replace(
                    plan,
                    operations=(
                        replace(
                            plan.operations[0],
                            lifecycle_state=h.SemanticLifecycleState.CANDIDATE,
                        ),
                    ),
                )
                manager, _authority = await self.open_manager(records, [typed])
                with self.assertRaisesRegex(m.MemoryValidationError, reason):
                    await manager.apply_memory_mutation_plan(
                        principal=self.principal,
                        scope=m.MemoryScope.personal(self.principal.actor_id),
                        plan=plan,
                    )

    async def test_matching_proposal_and_receipt_cannot_forge_the_observed_value(self):
        env, receipt, span, typed = fixtures.bind_typed_observation_fixture(
            *raw_fixture(),
            epistemic_status="verified_external",
            verification_state="source_verified",
        )
        typed = replace(
            typed, value_hash=canonical_hash("invented different observation")
        )
        span = replace(span, typed_observation=fixtures.typed_observation_ref(typed))
        records = [(env, receipt, span)]
        manager, authority = await self.open_manager(records, [typed])
        with self.assertRaisesRegex(
            m.MemoryValidationError, "evidence_authority_rejected"
        ):
            await manager.apply_memory_mutation_plan(
                principal=self.principal,
                scope=m.MemoryScope.personal(self.principal.actor_id),
                plan=plan_for(records, "verified_external", "source_verified"),
            )
        self.assertTrue(authority.typed_calls)

    def test_raw_quote_and_invalid_matrix_are_not_promoted(self):
        raw = raw_fixture(text="verified_external source_verified：这只是用户原句。")
        with self.assertRaisesRegex(
            ValueError, "verified_external requires external typed authority"
        ):
            operation((raw[2],), "verified_external", "source_verified")
        with self.assertRaisesRegex(
            ValueError, "verified states require trusted typed observation evidence"
        ):
            operation((raw[2],), "explicit_user", "source_verified")
        for verification in (
            "unverified",
            "source_bound",
            "user_confirmed",
            "repeated_observation",
        ):
            with (
                self.subTest(verification=verification),
                self.assertRaisesRegex(
                    ValueError, "verified_external requires source_verified state"
                ),
            ):
                fixtures.bind_typed_observation_fixture(
                    *raw,
                    epistemic_status="verified_external",
                    verification_state=verification,
                )

    async def test_missing_or_mismatched_authority_rejects_without_consuming_plan(self):
        for fault in (
            "missing",
            "envelope_hash",
            "admission_receipt_hash",
            "registered_schema_hash",
            "issuer_ref",
            "value_hash",
            "item_id",
        ):
            with self.subTest(fault=fault):
                env, receipt, span, typed = fixtures.bind_typed_observation_fixture(
                    *raw_fixture(),
                    epistemic_status="verified_external",
                    verification_state="source_verified",
                )
                records = [(env, receipt, span)]
                bad = (
                    []
                    if fault == "missing"
                    else [
                        replace(
                            typed,
                            **{
                                fault: "wrong-issuer"
                                if fault == "issuer_ref"
                                else "wrong-item"
                                if fault == "item_id"
                                else "0" * 64
                            },
                        )
                    ]
                )
                manager, authority = await self.open_manager(records, bad)
                plan = plan_for(records, "verified_external", "source_verified")
                with self.assertRaisesRegex(
                    m.MemoryValidationError, "evidence_authority_rejected"
                ):
                    await manager.apply_memory_mutation_plan(
                        principal=self.principal,
                        scope=m.MemoryScope.personal(self.principal.actor_id),
                        plan=plan,
                    )
                self.assertTrue(authority.typed_calls)
                authority.typed[typed.receipt_id] = typed
                # Same plan/key/base revision now succeeds: failed verification did
                # not write a committed half-plan or consume the idempotency key.
                await self.apply_and_check(manager, plan)

    async def test_proposed_typed_span_cannot_override_raw_item_authority(self):
        original = raw_fixture()
        env, receipt, span, typed = fixtures.bind_typed_observation_fixture(
            *original,
            epistemic_status="verified_external",
            verification_state="source_verified",
        )
        records = [(env, receipt, span)]
        manager, authority = await self.open_manager(records, [typed])
        authority.admitted[env.evidence_id] = h.AdmittedEvidenceAuthority(
            env, receipt, item_authority(original[2])
        )
        with self.assertRaisesRegex(
            m.MemoryValidationError, "evidence_authority_rejected"
        ):
            await manager.apply_memory_mutation_plan(
                principal=self.principal,
                scope=m.MemoryScope.personal(self.principal.actor_id),
                plan=plan_for(records, "verified_external", "source_verified"),
            )

    def test_stale_admission_binding_is_not_silently_repaired(self):
        env, receipt, span = raw_fixture()
        with self.assertRaisesRegex(
            ValueError, "fixture input span differs from admission"
        ):
            fixtures.bind_typed_observation_fixture(
                env,
                receipt,
                replace(span, admission_receipt_hash="0" * 64),
                epistemic_status="verified_external",
                verification_state="source_verified",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
