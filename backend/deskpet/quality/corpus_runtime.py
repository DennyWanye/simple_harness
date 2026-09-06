"""Scoring-turn adapter over actual Host enqueue/runtime/terminal authority.

Inputs are only authored turn text plus transport identity. No Case, setup,
gold, type selection, fake history, or per-case memory policy is accepted.
"""
from dataclasses import dataclass
from deskpet.memory.human_memory_service import QueueTurnRequest
from deskpet.memory.conversation_registration import PrimaryConversationAuthority


@dataclass(frozen=True)
class ExecutedCorpusTurn:
    queue_receipt: object
    completed_group: object


async def execute_scoring_turn(*, service, runtime, scoring_path, subject,
                               text: str, delivery_key: str, ingestion_worker=None,
                               approval_driver=None):
    if type(text) is not str or type(delivery_key) is not str:
        raise TypeError('corpus_runtime_requires_isolated_turn_text')
    if runtime.subject != subject:
        raise ValueError('corpus_runtime_subject_differs')
    queued = await service.enqueue_turn(QueueTurnRequest(None,delivery_key,text))
    await runtime.after_enqueue(subject=subject)
    await runtime.drain()
    if runtime.last_error is not None:
        raise RuntimeError('corpus_runtime_driver_failed') from runtime.last_error
    if approval_driver is not None:
        while await approval_driver(service=service, queued=queued):
            await runtime.after_control(subject=subject)
            await runtime.drain()
            if runtime.last_error is not None:
                raise RuntimeError('corpus_runtime_driver_failed') from runtime.last_error
    if runtime.last_error is not None:
        raise RuntimeError('corpus_runtime_driver_failed') from runtime.last_error
    # Complete-group authority requires the original USER ingestion ACK. An
    # existing worker may deliver it; its return value is never group proof.
    if ingestion_worker is not None:
        await ingestion_worker.run_once()
    authority = PrimaryConversationAuthority(scoring_path,subject=subject)
    matched = []
    # Each case uses an isolated scoring DB with only authored turns. Full-run
    # completion is accepted solely via actual terminal/group validation.
    for host_run_id in await authority.completed_run_ids():
        group=await authority.registrations_for_run(host_run_id)
        terminal=group.terminal_source[0].sanitized_payload
        if terminal.get('turn_id') == queued['turn_ref']:
            matched.append(group)
    if len(matched)!=1:
        raise RuntimeError('corpus_runtime_exact_completed_turn_unavailable')
    group=matched[0]
    if group.registrations[0].envelope.sanitized_payload.get('text') != text:
        raise RuntimeError('corpus_runtime_completed_input_differs')
    return ExecutedCorpusTurn(queued,group)
