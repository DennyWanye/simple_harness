"""Pure reference for frozen, bounded rank scanning (Q02/Q12).

Storage/ACL/receipts are NOT implemented here. The caller supplies a frozen
source sequence and keeps the returned state under the production cursor CAS.
No provisional ranked items are exposed before every frozen row was scanned.
"""
from dataclasses import dataclass
from .runtime_rules import RuleError, digest, rrf

CHANNELS = ('exact', 'words', 'trigram', 'vector')

@dataclass(frozen=True)
class ScoredRow:
    rowid: int
    chunk_id: str
    channel_scores: tuple[tuple[str, float], ...]

@dataclass(frozen=True)
class ScanState:
    snapshot_hash: str
    row_count: int
    after_offset: int
    heaps: tuple[tuple[str, tuple[tuple[str, float], ...]], ...]
    phase: str
    results: tuple[tuple[str, float], ...]

def freeze(rows: tuple[ScoredRow, ...]) -> ScanState:
    import math
    if any(r.rowid < 1 for r in rows) or tuple(sorted(r.rowid for r in rows)) != tuple(r.rowid for r in rows):
        raise RuleError('STATE_COMBINATION_INVALID')
    if len({r.rowid for r in rows}) != len(rows) or len({r.chunk_id for r in rows}) != len(rows):
        raise RuleError('DUPLICATE_ITEM')
    for r in rows:
        if len({n for n, _ in r.channel_scores}) != len(r.channel_scores):
            raise RuleError('DUPLICATE_ITEM')
        if any(n not in CHANNELS or type(v) not in (int, float) or not math.isfinite(v) for n, v in r.channel_scores):
            raise RuleError('STATE_COMBINATION_INVALID')
    body = [[r.rowid, r.chunk_id, r.channel_scores] for r in rows]
    return ScanState(digest(body), len(rows), 0, (), 'SCANNING', ())

def advance(state: ScanState, rows: tuple[ScoredRow, ...], *, page_rows: int, top_k: int) -> ScanState:
    if type(page_rows) is not int or not 1 <= page_rows <= 256 or type(top_k) is not int or not 1 <= top_k <= 512:
        raise RuleError('ARRAY_LIMIT')
    expected = freeze(rows)
    if state.snapshot_hash != expected.snapshot_hash or state.row_count != expected.row_count:
        raise RuleError('CURSOR_STALE')
    if state.phase == 'RESULTS':
        return state
    if state.phase != 'SCANNING' or not 0 <= state.after_offset <= len(rows):
        raise RuleError('STATE_COMBINATION_INVALID')
    heaps = {name: list(items) for name, items in state.heaps}
    end = min(len(rows), state.after_offset + page_rows)
    for row in rows[state.after_offset:end]:
        for channel, score in row.channel_scores:
            heaps.setdefault(channel, []).append((row.chunk_id, score))
    for channel in heaps:
        heaps[channel] = sorted(heaps[channel], key=lambda x: (-x[1], x[0]))[:top_k]
    done = end == len(rows)
    return ScanState(state.snapshot_hash, len(rows), end,
                     tuple((key, tuple(heaps[key])) for key in sorted(heaps)),
                     'RESULTS' if done else 'SCANNING',
                     tuple(rrf(heaps, top_k)) if done else ())
