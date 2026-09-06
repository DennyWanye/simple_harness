"""Child process for the new checkpoint tests, never a production launcher."""
import asyncio
import json
import os
from pathlib import Path
import sys

import simple_harness_memory as m
from deskpet.quality.corpus_inference_prepare import open_prepared_inference_fixture
from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from tests.memory.test_primary_visibility import classification_policy, FILTERS


async def run(config):
    mode = config.pop('fault')
    output = Path(config.pop('output'))
    now = config.pop('now')
    principal = m.MemoryPrincipal('host', 'household', config.pop('subject'), 'corpus-fixture')
    builder = m.build_human_memory_v7
    if mode in {'before_finalize', 'after_finalize'}:
        async def fault_builder(*args, **kwargs):
            manager = await builder(*args, **kwargs)
            finalize = manager.backend.finalize_analysis_application
            async def interrupted(claim, application):
                assert application.receipt.validation_status.value == 'accepted'
                if mode == 'after_finalize':
                    assert await finalize(claim, application)
                # The outer observer has already persisted the real candidate.
                # Exit this OS process without Python finally/manager cleanup.
                output.write_text(json.dumps(dict(boundary=mode,
                    request=claim.request.to_json(), receipt=application.receipt.to_json())))
                os._exit(83 if mode == 'after_finalize' else 84)
            manager.backend.finalize_analysis_application = interrupted
            return manager
        m.build_human_memory_v7 = fault_builder
    async with open_prepared_inference_fixture(**config, principal=principal,
            classification_policy=classification_policy(), supported_filter_policies=FILTERS,
            clock=lambda: now) as (manager, seed):
        report = seed['analysis_drain']
        graph = await manager.get_twin_graph_view(principal=principal)
        assert await PrimaryConversationAuthority(config['scoring_path'], subject=principal.actor_id).completed_run_ids() == ()
        output.write_text(json.dumps(dict(analysis_jobs=seed['analysis_jobs'],
            confirmed=report['confirmed'], executions=report['fixture_executions'],
            outcomes=[o.value for o in report['outcomes']], receipt=seed['receipt'].to_json(),
            graph=graph.to_json(),
            requests=[p.request.to_json() for p in report['applied']],
            application_receipts=[p.application.receipt.to_json() for p in report['applied']]),
            ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    asyncio.run(run(json.loads(Path(sys.argv[1]).read_text())))
