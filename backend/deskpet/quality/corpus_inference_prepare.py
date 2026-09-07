"""Public setup + actual job settlement before exposing a scoring manager.

Inputs are setup text, scenario clock and the actual completed source Run only.
No Case/gold/query/history argument; the original source conversation stays in
its source Host DB. This is a synthetic fixture, never a production extractor.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import simple_harness_memory as m
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.quality.corpus_source import import_setup_conversation_sources
from deskpet.quality.corpus_inference_drain import InferenceFixtureAuthority, drain_inference_setup


@asynccontextmanager
async def open_prepared_inference_fixture(*, case_id, setup_text, scenario_time,
        source_path, scoring_path, memory_path, proof_path, principal, host_run_id,
        classification_policy, supported_filter_policies, clock):
    """Always drain and revalidate before yielding; no ready-with-pending mode."""
    destinations = [Path(p).resolve() for p in (source_path, scoring_path, memory_path, proof_path)]
    if len(set(destinations)) != len(destinations) or any(
            a.exists() and b.exists() and a.samefile(b)
            for i, a in enumerate(destinations) for b in destinations[i + 1:]):
        raise ValueError('inference_prepare_paths_must_be_distinct')
    if case_id not in {'C02-19', 'C03-20'}:
        raise ValueError('inference_prepare_case_not_supported')
    if case_id == 'C02-19':
        from deskpet.quality.corpus_c01 import compile_setup
        batch = compile_setup(case_id, setup_text,
            scenario_clock=datetime.fromtimestamp(scenario_time, timezone.utc).isoformat())
    else:
        from deskpet.quality.corpus_c03 import SETUPS
        if setup_text != SETUPS['C03-20'][0]:
            raise ValueError('inference_prepare_setup_differs')
    group = await import_setup_conversation_sources(source_path=source_path,
        scoring_path=scoring_path, subject=principal.actor_id, host_run_id=host_run_id)
    original = group.registrations[0]
    if original.envelope.sanitized_payload.get('text') != setup_text:
        raise ValueError('inference_prepare_original_setup_differs')
    authority = InferenceFixtureAuthority()
    manager = await m.build_human_memory_v7(memory_path,
        classification_policy=classification_policy, supported_filter_policies=supported_filter_policies,
        evidence_authority=HostEvidenceAuthority(scoring_path),
        analysis_delivery_authority=authority, clock=clock)
    try:
        if case_id == 'C02-19':
            from deskpet.quality.corpus_c01 import apply_setup
            await manager.register_principal_owner(principal, m.MemoryScope.personal(principal.actor_id))
            # C02's accepted low-level apply does not set lineage; preserve the
            # actual original USER lineage before its idempotent ingest replay.
            await manager.ingest_committed_evidence(original.envelope, original.admission_receipt,
                analysis_lineage=group.user_analysis_lineage)
            seed = await apply_setup(manager=manager, principal=principal, batch=batch,
                envelope=original.envelope, receipt=original.admission_receipt,
                inference_path=source_path, inference_host_run_id=host_run_id)
            applied = seed['apply_result']
        else:
            from deskpet.quality.corpus_c03_inference import prepare_c03_20_setup
            seed = await prepare_c03_20_setup(source_path=source_path, scoring_path=scoring_path,
                manager=manager, principal=principal, host_run_id=host_run_id,
                original_envelope=original.envelope, original_receipt=original.admission_receipt,
                scenario_time=scenario_time)
            applied = seed['applied']
        report = await drain_inference_setup(authority=authority, manager=manager,
            proof_path=proof_path, case_id=case_id, source_path=source_path, scoring_path=scoring_path,
            host_run_id=host_run_id, principal=principal, group=group, plan=seed['plan'],
            applied=applied, clock=clock)
        if not report['confirmed']:
            raise ValueError(report['reason'])
        seed = dict(seed, analysis_jobs='applied', analysis_drain=report, source_group=group)
        yield manager, seed
    finally:
        await manager.close()
