"""Explicit fixture setup-prefix isolation; canonical evidence is never erased."""
import aiosqlite
import json
from deskpet.execution.terminal_identity import read_primary_terminal_identity_tx
from deskpet.quality.corpus_c05 import SETUPS, compile_c05_setup, operational_text


class SetupPrefixHistoryReader:
    def __init__(self, *, path, delegate, subject, primary_ref, turn_ids, last_sequence):
        self.path = path
        self.delegate = delegate
        self.subject, self.primary_ref = subject, primary_ref
        self.turn_ids, self.last_sequence = frozenset(turn_ids), last_sequence

    @classmethod
    async def from_archives(cls, *, path, subject, primary_ref, archives, stack, delegate):
        return await cls._from_archives(path=path, subject=subject, primary_ref=primary_ref,
            archives=archives, stack=stack, delegate=delegate, require_prefix=True)

    @classmethod
    async def _from_archives(cls, *, path, subject, primary_ref, archives, stack, delegate, require_prefix):
        if not archives or any(a.subject != subject for a in archives):
            raise ValueError('c05_setup_archive_subject_invalid')
        indexed = {a.turn_id: a for a in archives}
        if len(indexed) != len(archives):
            raise ValueError('c05_duplicate_setup_turn')
        # Host-owned authority read only; no SDK-private SQL or synthetic rows.
        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            placeholders = ','.join('?' for _ in indexed)
            selection = ('AND t.enqueue_sequence<=(SELECT MAX(enqueue_sequence) FROM foreground_turns '
                         f'WHERE subject=? AND primary_conversation_id=? AND turn_id IN ({placeholders})) '
                         if require_prefix else f'AND t.turn_id IN ({placeholders}) ')
            selection_args = (subject, primary_ref, *indexed) if require_prefix else tuple(indexed)
            async with db.execute(
                'SELECT t.turn_id,t.enqueue_sequence,b.sdk_run_id,f.terminal_state,r.host_run_id,t.turn_json '
                'FROM foreground_turns t LEFT JOIN foreground_runs r ON r.turn_id=t.turn_id '
                'LEFT JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id '
                'LEFT JOIN foreground_terminal_receipts f ON f.host_run_id=r.host_run_id AND f.sdk_run_id=b.sdk_run_id '
                'WHERE t.subject=? AND t.primary_conversation_id=? '
                + selection +
                'ORDER BY t.enqueue_sequence LIMIT ?',
                (subject, primary_ref, *selection_args, len(indexed) + 1),
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                if row['turn_id'] not in indexed:
                    continue
                archive = indexed[row['turn_id']]
                original = json.loads(row['turn_json'])['payload']['text']
                batch = compile_c05_setup(archive.case_id, SETUPS[archive.case_id][0])
                if (batch.setup_hash != archive.setup_hash
                        or original != operational_text(batch, archive.label, phase=archive.phase)):
                    raise ValueError('c05_setup_original_hash_differs')
                identity = await read_primary_terminal_identity_tx(db, subject=subject,
                    primary_ref=primary_ref, host_run_id=row['host_run_id'], sdk_run_id=row['sdk_run_id'])
                if identity is None:
                    raise ValueError('c05_setup_terminal_missing')
                identity.verify_sdk_terminal(indexed[row['turn_id']].terminal)
        sequences = [r[1] for r in rows if r[0] in indexed]
        if len(sequences) != len(indexed):
            raise ValueError('c05_setup_turn_not_bound')
        last = max(sequences)
        prefix = [r for r in rows if r[1] <= last]
        if {r[0] for r in prefix} != set(indexed):
            raise ValueError('c05_setup_is_not_exact_prefix')
        for turn, _, sdk_run, state, _, _ in prefix:
            archive = indexed[turn]
            if sdk_run != archive.sdk_run_id or state != 'COMPLETED':
                raise ValueError('c05_setup_terminal_binding_differs')
            if stack.read_run_terminal_evidence(sdk_run) != archive.terminal:
                raise ValueError('c05_setup_terminal_identity_differs')
        return cls(path=path, delegate=delegate, subject=subject, primary_ref=primary_ref,
                   turn_ids=indexed, last_sequence=last if require_prefix else 0)

    async def read(self, *, subject, primary_ref, before_sequence, limit=10,
                   completed_only=False, disclosure_context=None):
        if (subject, primary_ref) != (self.subject, self.primary_ref):
            raise ValueError('c05_history_reader_scope_differs')
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError('primary_history_limit_invalid')
        if type(before_sequence) is not int or before_sequence < 1:
            raise ValueError('primary_history_sequence_invalid')
        result = []
        cursor_before = before_sequence
        # Select only page boundaries from Host metadata. Actual messages,
        # authority and suppression are always read by the production reader.
        # No larger request or fabricated history fills a filtered page.
        while True:
            remaining = limit - len(result)
            async with aiosqlite.connect(self.path) as db:
                async with db.execute(
                    'SELECT t.turn_id,t.enqueue_sequence FROM foreground_runs r '
                    'JOIN foreground_turns t ON t.turn_id=r.turn_id '
                    'JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id '
                    'JOIN foreground_terminal_receipts f ON f.host_run_id=r.host_run_id AND f.sdk_run_id=b.sdk_run_id '
                    'WHERE r.subject=? AND r.primary_conversation_id=? AND t.enqueue_sequence<? '
                    "AND (?=0 OR f.terminal_state='COMPLETED') "
                    'ORDER BY t.enqueue_sequence DESC LIMIT ?',
                    (subject, primary_ref, cursor_before, int(completed_only), remaining),
                ) as cursor:
                    rows = await cursor.fetchall()
            groups = await self.delegate.read(subject=subject, primary_ref=primary_ref,
                before_sequence=cursor_before, limit=remaining, completed_only=completed_only,
                disclosure_context=disclosure_context)
            page_ids = {row[0] for row in rows}
            if (len({group['turn_id'] for group in groups}) != len(groups)
                    or any(group['turn_id'] not in page_ids for group in groups)):
                raise RuntimeError('c05_history_page_binding_differs')
            kept = [group for group in groups if group['turn_id'] not in self.turn_ids]
            result = kept + result
            if len(result) == limit or len(rows) < remaining:
                return tuple(result)
            cursor_before = rows[-1][1]
            if cursor_before <= self.last_sequence:
                return tuple(result)


class SetupPhaseHistoryReader(SetupPrefixHistoryReader):
    """Exact proven setup turns, including C18's between-turn mutation Run.

    Unlike the prefix reader, no sequence cutoff discards earlier scoring
    turns. Every page still delegates original integrity/privacy checks.
    """
    @classmethod
    async def from_archives(cls, *, path, subject, primary_ref, archives, stack, delegate):
        return await cls._from_archives(path=path, subject=subject, primary_ref=primary_ref,
            archives=archives, stack=stack, delegate=delegate, require_prefix=False)


class C05PhaseHistoryReader:
    """Construct once with the real delegate; freeze only proven setup IDs.

    This changes fixture view selection, never a bound runtime authority or
    current disclosure policy. In-flight reads cannot cross a phase change.
    """
    def __init__(self, *, path, subject, delegate):
        self.path, self.subject, self.delegate = path, subject, delegate
        self._scoring_reader = None
        self._reads = 0
        self._changing = False
        self._archives = ()

    def completed_setup_archives(self):
        """Only the exact previously verified fixture records, while idle."""
        if self._scoring_reader is None or self._changing or self._reads:
            raise ValueError('c05_history_phase_not_idle')
        return self._archives

    async def freeze(self, *, archives, stack, primary_ref):
        if self._scoring_reader is not None or self._changing or self._reads:
            raise ValueError('c05_history_phase_change_not_idle')
        self._changing = True
        try:
            reader = await SetupPrefixHistoryReader.from_archives(path=self.path,
                subject=self.subject, primary_ref=primary_ref, archives=archives,
                stack=stack, delegate=self.delegate)
            self._scoring_reader = reader
            self._archives = tuple(archives)
        finally:
            self._changing = False

    async def add_completed_phase(self, *, archive, stack, primary_ref):
        if self._scoring_reader is None or self._changing or self._reads:
            raise ValueError('c05_history_phase_change_not_idle')
        if (archive.case_id != 'C05-18' or archive.phase != 'before_selection'
                or any(a.phase == 'before_selection' for a in self._archives)
                or any(a.case_id != archive.case_id for a in self._archives)):
            raise ValueError('c05_history_phase_not_declared')
        self._changing = True
        try:
            archives = (*self._archives, archive)
            reader = await SetupPhaseHistoryReader.from_archives(path=self.path,
                subject=self.subject, primary_ref=primary_ref, archives=archives,
                stack=stack, delegate=self.delegate)
            self._scoring_reader, self._archives = reader, archives
        finally:
            self._changing = False

    async def read(self, **kwargs):
        if self._changing or kwargs.get('subject') != self.subject:
            raise ValueError('c05_history_phase_read_rejected')
        self._reads += 1
        try:
            reader = self.delegate if self._scoring_reader is None else self._scoring_reader
            return await reader.read(**kwargs)
        finally:
            self._reads -= 1
