"""New C05 controls prepared only; actual runtime with deterministic setup Provider."""
from dataclasses import replace

import pytest

from deskpet.quality.corpus_c05 import SETUPS, SPECS, compile_c05_setup
from deskpet.quality.corpus_c05_prepare import TaskSetupProvider, prepare_scope_archive
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.execution.primary_dependencies import current_disclosure
from tests.execution.test_primary_create_new_runtime import fixture
from tests.execution.test_primary_foreground_runtime import Provider, build


def test_all_twenty_setup_mappings_preserve_input_and_outstanding_requirements():
    assert set(SETUPS) == set(SPECS) == {f'C05-{i:02}' for i in range(1, 21)}
    for key, (text, digest) in SETUPS.items():
        batch = compile_c05_setup(key, text)
        assert batch.setup_text == text and batch.setup_hash == digest
        assert len({s.label for s in batch.scopes}) == len(batch.scopes)
        with pytest.raises(ValueError, match='c05_exact_setup_required'):
            compile_c05_setup(key, text + '从评分答案补入的内容')
    assert SPECS['C05-03'][0].alias == '展览准备'
    assert SPECS['C05-08'][0].status == 'complete'
    assert all(s.status == 'paused' for s in SPECS['C05-09'])
    assert SPECS['C05-12'][1].owner == 'other'
    assert SPECS['C05-16'][0].title is None  # no title inferred from gold


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', ['C05-20', 'C05-04', 'C05-09', 'C05-14'])
async def test_actual_setup_routes_terminal_and_source_bound_readback(tmp_path, case_id):
    state, factory, service, configured, authority = await fixture(tmp_path)
    provider = TaskSetupProvider(target=Provider.target)
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
        binding_authority=authority, configured_root=configured)
    batch = compile_c05_setup(case_id, SETUPS[case_id][0])
    subject = local_owner_auth().subject
    archives = []
    try:
        for spec in batch.scopes:
            archive = await prepare_scope_archive(batch=batch, label=spec.label, subject=subject,
                service=service, provider=provider, drive=runtime._drive_once, stack=stack, path=state,
                policy=runtime.history_policy,
                disclosure_context=current_disclosure(run_id='fixture-reader', subject=subject,
                    request_id='fixture-reader'))
            archives.append(archive)
            assert archive.disclosure['disclosure']['fields']['title'] == spec.title
            assert archive.terminal.state == 'completed'
            assert 'scoring_history_isolation' in archive.remaining_requirements
        assert len({a.task_scope_id for a in archives}) == len(batch.scopes)
        if case_id == 'C05-20':
            import aiosqlite
            from deskpet.execution.primary_history import PrimaryHistoryStore
            from deskpet.quality.corpus_c05_history import SetupPrefixHistoryReader
            async with aiosqlite.connect(state) as db:
                async with db.execute('SELECT primary_conversation_id FROM foreground_turns WHERE turn_id=?',
                                      (archives[0].turn_id,)) as cursor:
                    primary_ref = (await cursor.fetchone())[0]
            delegate = PrimaryHistoryStore(state, settled_run_reader=stack.read_settled_primary_run,
                policy=runtime.history_policy)
            reader = await SetupPrefixHistoryReader.from_archives(path=state, subject=subject,
                primary_ref=primary_ref, archives=archives, stack=stack, delegate=delegate)
            disclosure = current_disclosure(run_id='fixture-reader', subject=subject, request_id='fixture-reader')
            assert await delegate.read(subject=subject, primary_ref=primary_ref,
                before_sequence=reader.last_sequence + 1, disclosure_context=disclosure)
            assert await reader.read(subject=subject, primary_ref=primary_ref,
                before_sequence=reader.last_sequence + 1, disclosure_context=disclosure) == ()
            # Only the context view is filtered. Source-bound task archives and
            # all original SDK terminal identities remain publicly readable.
            for archive in archives:
                assert stack.read_run_terminal_evidence(archive.sdk_run_id) == archive.terminal
            with pytest.raises(ValueError, match='c05_history_reader_scope_differs'):
                await reader.read(subject='foreign', primary_ref=primary_ref,
                    before_sequence=reader.last_sequence + 1, disclosure_context=disclosure)
            with pytest.raises(ValueError, match='c05_setup_is_not_exact_prefix'):
                await SetupPrefixHistoryReader.from_archives(path=state, subject=subject,
                    primary_ref=primary_ref, archives=archives[1:], stack=stack, delegate=delegate)
        # These are real source archives; the test does not claim scoring is ready.
        bad = replace(batch, setup_text=batch.setup_text + 'extra')
        with pytest.raises(ValueError, match='c05_exact_batch_required'):
            provider.arm(bad, batch.scopes[0].label)
    finally:
        await stack.close()


def test_missing_authored_title_is_not_filled_from_case_selection():
    provider = TaskSetupProvider(target=Provider.target)
    batch = compile_c05_setup('C05-18', SETUPS['C05-18'][0])
    with pytest.raises(ValueError, match='c05_source_title_missing'):
        provider.arm(batch, 'A')


def test_history_reader_injection_keeps_default_production_reader(tmp_path):
    from deskpet.execution.primary_context import PrimaryForegroundContextPort
    from deskpet.execution.primary_history import PrimaryHistoryStore
    path = tmp_path / 'not-opened.sqlite'
    default = PrimaryForegroundContextPort(path, subject='owner')
    assert isinstance(default._history, PrimaryHistoryStore)
    class Reader:
        def __bool__(self):
            return False
    reader = Reader()
    injected = PrimaryForegroundContextPort(path, subject='owner', history_reader=reader)
    assert injected._history is reader  # explicit None semantics, not truthiness


@pytest.mark.asyncio
async def test_actual_scoring_pages_survive_setup_prefix_and_late_suppression(tmp_path, monkeypatch):
    """NOT_RUN: real history/policy; deterministic setup and scoring Providers.

    The constructor override only supplies the approved optional reader. It
    does not replace a bound runtime authority or forge any stored message.
    """
    import aiosqlite
    from simple_harness_memory import SuppressionRequest, SuppressionScopeKind
    from deskpet.execution.primary_context import PrimaryForegroundContextPort
    from deskpet.execution.primary_history import PrimaryHistoryStore
    from deskpet.memory.human_memory_service import QueueTurnRequest
    from deskpet.quality.corpus_c05_history import SetupPrefixHistoryReader
    from tests.execution import test_primary_foreground_runtime as runtime_fixture

    archives, readers = [], []
    scoring = False

    class PhaseProvider:
        target = Provider.target
        def __init__(self):
            self.setup = TaskSetupProvider(target=self.target)
            self.scoring = Provider()
        async def invoke(self, request, *, cancel):
            target = self.scoring if scoring else self.setup
            return await target.invoke(request, cancel=cancel)

    class PhaseHistory:
        def __init__(self, path, kwargs):
            self.path, self.kwargs = path, kwargs
            self.delegate = PrimaryHistoryStore(path, policy=kwargs['policy'],
                settled_run_reader=kwargs['stack_getter']().read_settled_primary_run)
            self.scoring_reader = None
        async def read(self, **kwargs):
            if not scoring:
                return await self.delegate.read(**kwargs)
            if self.scoring_reader is None:
                self.scoring_reader = await SetupPrefixHistoryReader.from_archives(
                    path=self.path, subject=kwargs['subject'], primary_ref=kwargs['primary_ref'],
                    archives=archives, stack=self.kwargs['stack_getter'](), delegate=self.delegate)
            return await self.scoring_reader.read(**kwargs)

    def context(path, **kwargs):
        reader = PhaseHistory(path, kwargs)
        readers.append(reader)
        return PrimaryForegroundContextPort(path, history_reader=reader, **kwargs)

    monkeypatch.setattr(runtime_fixture, 'PrimaryForegroundContextPort', context)
    state, factory, service, configured, authority = await fixture(tmp_path)
    provider = PhaseProvider()
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
        binding_authority=authority, configured_root=configured)
    subject = local_owner_auth().subject
    batch = compile_c05_setup('C05-20', SETUPS['C05-20'][0])
    disclosure = current_disclosure(run_id='fixture-reader', subject=subject, request_id='fixture-reader')
    try:
        for spec in batch.scopes:
            archives.append(await prepare_scope_archive(batch=batch, label=spec.label,
                subject=subject, service=service, provider=provider.setup, drive=runtime._drive_once,
                stack=stack, path=state, policy=runtime.history_policy, disclosure_context=disclosure))
        scoring = True
        turns = []
        for index in range(3):
            turns.append(await service.enqueue_turn(QueueTurnRequest(None, f'score-{index}',
                f'Scoring-only message {index}')))
            await runtime._drive_once()
        assert len(provider.scoring.requests) == 3
        for request in provider.scoring.requests:
            assert batch.setup_text not in str(request.messages)
            assert 'Fixture setup complete.' not in str(request.messages)
        assert 'Scoring-only message 0' in str(provider.scoring.requests[1].messages)
        reader = readers[0].scoring_reader
        common = dict(subject=subject, primary_ref=reader.primary_ref,
                      completed_only=True, disclosure_context=disclosure)
        page = await reader.read(**common, before_sequence=turns[-1]['enqueue_sequence'] + 1, limit=2)
        assert [g['turn_id'] for g in page] == [t['turn_ref'] for t in turns[1:]]
        earlier = await reader.read(**common, before_sequence=turns[1]['enqueue_sequence'], limit=2)
        assert [g['turn_id'] for g in earlier] == [turns[0]['turn_ref']]
        # Public suppression of the second scoring USER also hides the third
        # turn that actually inherited it; the first scoring turn is still read.
        async with aiosqlite.connect(state) as db:
            async with db.execute('SELECT evidence_id FROM foreground_turns WHERE turn_id=?',
                                  (turns[1]['turn_ref'],)) as cursor:
                evidence_id = (await cursor.fetchone())[0]
        manager = await runtime.history_memory.manager()
        await manager.backend.suppress(SuppressionRequest('c05-late-scoring-forget', subject,
            SuppressionScopeKind.EVIDENCE, evidence_id, 'user_forget', 20.0),
            principal=runtime.history_memory.principal())
        visible = await reader.read(**common, before_sequence=turns[-1]['enqueue_sequence'] + 1, limit=2)
        assert [g['turn_id'] for g in visible] == [turns[0]['turn_ref']]
        # Filtering never destroys the original TaskScope SDK terminal archive.
        for archive in archives:
            assert stack.read_run_terminal_evidence(archive.sdk_run_id) == archive.terminal
    finally:
        await stack.close()
