"""C18's single authored between-turn revision through the actual main stack.

Only new Run bindings select new process-only Provider identities. Retired
entries remain unchanged for audit; no bound Run is pointed at another server.
"""
import asyncio

from deskpet.quality.corpus_c05 import operational_text
from deskpet.quality.corpus_c05_prepare import prepare_scope_archive
from deskpet.quality.corpus_c05_transport import TaskSetupHttpProvider
from deskpet.quality.corpus_trace import wire

SCORING_AFTER_REVISION = 'corpus-real-provider-after-task-revision'


class TaskRevisionPhase:
    def __init__(self, *, main, batch, service, runtime, history_reader, workspace_root,
                 directory, collect_turn, record, worker, scoring_base_url, scoring_key):
        if batch.case_id != 'C05-18':
            raise ValueError('c05_revision_not_declared')
        self.main, self.batch, self.service, self.runtime = main, batch, service, runtime
        self.history_reader, self.workspace_root = history_reader, workspace_root
        self.directory, self.collect_turn, self.record, self.worker = directory, collect_turn, record, worker
        self.scoring_base_url, self.scoring_key = scoring_base_url, scoring_key
        self.started = False

    async def _admit(self, fields):
        main = self.main
        if main._sdk_provider_binding_resolver.active_provider_ids():
            raise ValueError('c05_phase_provider_still_active')
        await main._provider_registry.add_ephemeral_provider(fields)
        if main._provider_registry.get_chain()[0]['id'] != fields['id']:
            raise ValueError('c05_phase_provider_not_selected')
        await main._session_db.reconcile_provider_bindings({item['id']:
            (str(item['incarnation_id']), int(item['config_revision']))
            for item in main._provider_registry.list_providers()},
            registry_digest=main._provider_registry.snapshot_digest())

    async def __call__(self, *, prior_run):
        from deskpet.quality.corpus_c05_approval import C05SetupApproval
        from deskpet.quality.corpus_c05_session import _confirmed_setup_trace
        from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
        from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
        from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore
        from deskpet.memory.trusted_disclosure import resolve_current_disclosure
        from deskpet.memory.human_memory_service import OpenTaskScopeRequest
        if self.started:
            raise ValueError('c05_revision_phase_already_attempted')
        self.started = True
        main, service, runtime = self.main, self.service, self.runtime
        stack, subject = main._sdk_runtime_stack, runtime.subject
        originals = self.history_reader.completed_setup_archives()
        if (len(originals) != 1 or originals[0].case_id != 'C05-18'
                or originals[0].label != 'A' or originals[0].phase != 'create'):
            raise ValueError('c05_revision_original_not_unique')
        original = originals[0]
        prior_terminal = stack.read_run_terminal_evidence(prior_run)
        prior_binding = SdkRunBindingV1.from_record(stack.read_closure_run_facts(prior_run).binding_record)
        if (prior_terminal is None or prior_terminal.state != 'completed'
                or prior_binding.provider_id != 'corpus-real-provider'
                or prior_run == original.sdk_run_id or original.subject != subject):
            raise ValueError('c05_revision_requires_completed_scoring_preview')
        # Current official open, before mutation; no stale saved package is
        # mistaken for the head that will actually be revised.
        before = await service.open_task_scope(OpenTaskScopeRequest(original.task_scope_id,
            expected_source_hash=original.source_hash))
        primary = (await service.read_primary_state(request_id='c05-revision-primary'))['primary_ref']
        phase_dir = self.directory / 'setup-task-revision'
        phase_dir.mkdir(exist_ok=False)
        phase = dict(kind='authored_between_turn_revision', status='NOT_CONFIRMED', real_model_calls=0,
            prior_scoring_run_id=prior_run, original_source_id=original.source_id,
            original_source_hash=original.source_hash)
        transport = TaskSetupHttpProvider(model=main.config.llm.local.model, phase='revision')
        observations = None
        observation_started = False
        approval = C05SetupApproval(ingress=main._sdk_ingress, transport=transport, stack=stack,
            ledger=ContextRouteLedgerStore(main._state_db_path),
            binding_store=WorkspaceBindingAuthorityStore(main._state_db_path,
                configured_workspace_root=self.workspace_root), expected_configured_root=self.workspace_root,
            persist=lambda name, value: self.record(phase_dir / (name + '.json'), value), revision_origin=original)

        async def drive():
            await runtime.after_enqueue(subject=subject)
            await runtime.drain()
            if runtime.last_error is not None:
                raise RuntimeError('c05_revision_runtime_failed') from runtime.last_error
            while await approval(service=service, queued=transport.queued):
                await runtime.after_control(subject=subject)
                await runtime.drain()
                if runtime.last_error is not None:
                    raise RuntimeError('c05_revision_runtime_failed') from runtime.last_error
            await self.worker.run_once()

        async def disclosure(*, run_id, turn_id):
            if transport.queued is None or transport.queued['turn_ref'] != turn_id:
                raise ValueError('c05_revision_turn_differs')
            return await resolve_current_disclosure(db_path=main._state_db_path, subject=subject,
                run_id=run_id, turn_id=turn_id, request_id='c05-revision:' + turn_id)

        try:
            await transport.start()
            await self._admit(transport.registration())
            archive = await prepare_scope_archive(batch=self.batch, label='A', subject=subject,
                service=service, provider=transport, drive=drive, stack=stack, path=main._state_db_path,
                policy=main._primary_history_policy(subject), disclosure_context_resolver=disclosure,
                revision_of=original)
            observation_started = True
            observations = await self.collect_turn(main, service, subject, transport.queued,
                operational_text(self.batch, 'A', phase='before_selection'), directory=phase_dir)
            binding = SdkRunBindingV1.from_record(stack.read_closure_run_facts(archive.sdk_run_id).binding_record)
            proof = _confirmed_setup_trace(observations, transport, binding=binding, turn_ref=archive.turn_id)
            if (archive.sdk_run_id in {prior_run, original.sdk_run_id}
                    or archive.source_hash == original.source_hash
                    or archive.disclosure['disclosure']['structure']['canonical_revision'] <=
                        before['resume_package']['canonical_revision']
                    or archive.disclosure['disclosure']['fields'].get('resume') != '待确认图片'):
                raise ValueError('c05_revision_actual_state_not_advanced')
            if (stack.read_run_terminal_evidence(prior_run) != prior_terminal
                    or stack.read_run_terminal_evidence(original.sdk_run_id) != original.terminal):
                raise ValueError('c05_revision_changed_prior_terminal')
            self.record(phase_dir / 'archive.json', archive)
            await self.history_reader.add_completed_phase(archive=archive, stack=stack, primary_ref=primary)
            # Join fixture HTTP before admitting the next scoring Run. Keep
            # old registered entries for immutable incarnation/audit lookup.
            await transport.close()
            await self._admit(dict(id=SCORING_AFTER_REVISION, base_url=self.scoring_base_url,
                api_key=self.scoring_key, models=[main.config.llm.local.model], priority=-1))
            phase.update(proof, status='CONFIRMED', source_id=archive.source_id, source_hash=archive.source_hash,
                canonical_revision=archive.disclosure['disclosure']['structure']['canonical_revision'],
                scoring_provider_id=SCORING_AFTER_REVISION, old_preview_terminal=wire(prior_terminal),
                exact_hidden_setup_turns=[original.turn_id, archive.turn_id])
            return phase
        except BaseException as error:
            phase['error_type'] = type(error).__name__
            if not observation_started and transport.queued is not None and not isinstance(error, asyncio.CancelledError):
                try:
                    await self.collect_turn(main, service, subject, transport.queued,
                        operational_text(self.batch, 'A', phase='before_selection'), directory=phase_dir)
                except Exception as observation_error:
                    phase['observation_error_type'] = type(observation_error).__name__
            raise
        finally:
            await transport.close()
            phase['fixture_http_requests'] = transport.attempts
            self.record(phase_dir / 'phase.json', phase)
