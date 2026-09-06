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

    async def allow_request(self, *, request, sdk_run_id, queued, service):
        from simple_harness import thaw_json
        from deskpet.quality.corpus_c05_runtime import read_candidate_events
        effect_id = request.get('effect_id')
        if not isinstance(effect_id, str) or not effect_id:
            return False
        _, (effect,) = self.main._sdk_runtime_stack.read_primary_dependency_facts(sdk_run_id, (effect_id,))
        if (effect is None or effect.run_id.value != sdk_run_id or effect.effect_id.value != effect_id
                or effect.call_id.value != request.get('call_id')
                or effect.tool_name != request.get('tool_name')
                or thaw_json(effect.arguments) != request.get('arguments')):
            return False
        name, args = request.get('tool_name'), request.get('arguments')
        if not isinstance(args, dict):
            return False
        if name == 'task_scope_search':
            return True  # Actual production schema, source/disclosure and final guards still apply.
        if await super().allow_request(request=request, sdk_run_id=sdk_run_id, queued=queued, service=service):
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
            effect_id=effect_id, sources=matches))
        return True

