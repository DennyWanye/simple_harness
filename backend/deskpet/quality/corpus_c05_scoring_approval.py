"""C05 scoring-only decisions over actual public effects and disclosed sources."""
from deskpet.quality.corpus_approval import ReadOnlyMemoryApproval


class C05ScoringApproval(ReadOnlyMemoryApproval):
    """Exact benchmark decisions, separate from write-capable setup approval.

    A final authored selection permits read-only scope context use. Its semantic
    correctness is post-execution scoring, never a gold-derived approval rule.
    Files, task mutations and workspace creation are not on this allowlist.
    """
    def __init__(self, *, main, subject, selection_allowed, preview_runs, persist):
        super().__init__(ingress=main._sdk_ingress, persist=persist)
        self.main, self.subject = main, subject
        self.selection_allowed = selection_allowed
        self.preview_runs = tuple(preview_runs)

    async def allow_request(self, *, request, sdk_run_id, decision_id, queued, service):
        from deskpet.quality.corpus_c05_approval import verify_pending_call
        from deskpet.quality.corpus_c05_runtime import read_candidate_events
        # OPEN authorization precedes EffectRecord creation. The common helper
        # reads actual public waiting/proposal/response facts, not a fake effect.
        proof = await verify_pending_call(stack=self.main._sdk_runtime_stack, ingress=self.ingress,
            sdk_run_id=sdk_run_id, decision_id=decision_id, request=request)
        self.record(dict(status='PENDING_CALL_OBSERVED', sdk_run_id=sdk_run_id,
            decision_id=decision_id, effect_id=proof.effect_id,
            internal_call_id=proof.internal_call_id, raw_call_id=proof.raw_call_id,
            waiting_source_ref=proof.waiting_source_ref, waiting_source_hash=proof.waiting_source_hash,
            provider_invocation_ref=proof.provider_invocation_ref,
            provider_response_hash=proof.provider_response_hash))
        name, args = request.get('tool_name'), request.get('arguments')
        if not isinstance(args, dict):
            return False
        if name == 'task_scope_search':
            return True  # Actual production schema, source/disclosure and final guards still apply.
        if await super().allow_request(request=request, sdk_run_id=sdk_run_id,
                decision_id=decision_id, queued=queued, service=service):
            return True
        if name != 'context_route' or args.get('route') != 'resume_existing' or not self.selection_allowed:
            return False
        # Revalidate the originally observed preview runs under current policy.
        # Include a new search in this Run, without reading the fixture labels.
        sources = []
        for run_id in (*self.preview_runs, sdk_run_id):
            events = await read_candidate_events(path=self.main._state_db_path, subject=self.subject,
                sdk_run_id=run_id, stack=self.main._sdk_runtime_stack,
                policy=self.main._primary_history_policy(self.subject))
            sources.extend(source for event in events for source in event['visible_sources'])
        matches = [source for source in sources if source['task_scope_id'] == args.get('task_scope_id')
            and (args.get('expected_source_hash') is None or source['source_hash'] == args['expected_source_hash'])]
        if not matches:
            return False
        self.record(dict(status='CURRENT_PREVIEW_SOURCE_OBSERVED', sdk_run_id=sdk_run_id,
            effect_id=proof.effect_id, sources=matches))
        return True
