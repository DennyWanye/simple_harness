export interface LiveCursor {
  stream_epoch: string;
  live_seq: number;
}

export interface RunEventEnvelope {
  event_id: string;
  run_id: string;
  kind: string;
  status: string;
  durable_seq?: number | null;
  live_cursor?: LiveCursor | null;
  payload?: unknown;
}

export interface DurableRunState {
  lastSeq: number;
  seenEventIds: ReadonlySet<string>;
  events: readonly RunEventEnvelope[];
}

export interface LiveRunState {
  currentEpoch: string | null;
  lastSeq: number;
  retiredEpochs: ReadonlySet<string>;
  seenEventIds: ReadonlySet<string>;
  events: readonly RunEventEnvelope[];
}

export const EMPTY_DURABLE_RUN_STATE: DurableRunState = {
  lastSeq: 0,
  seenEventIds: new Set<string>(),
  events: [],
};

export const EMPTY_LIVE_RUN_STATE: LiveRunState = {
  currentEpoch: null,
  lastSeq: 0,
  retiredEpochs: new Set<string>(),
  seenEventIds: new Set<string>(),
  events: [],
};

function hasText(value: string): boolean {
  return value.trim().length > 0;
}

function validEnvelope(event: RunEventEnvelope): boolean {
  return hasText(event.event_id) && hasText(event.run_id) && hasText(event.kind);
}

/**
 * Reduce persisted lifecycle events only. Hydration and a live websocket may
 * deliver the same event, so identity and the monotonic durable sequence are
 * both checked. Live token/PCM cursors are deliberately rejected here.
 */
export function reduceDurableRunEvent(
  state: DurableRunState,
  event: RunEventEnvelope,
): DurableRunState {
  if (!validEnvelope(event) || event.live_cursor != null) return state;
  if (!Number.isSafeInteger(event.durable_seq) || Number(event.durable_seq) <= 0) return state;
  if (state.seenEventIds.has(event.event_id) || Number(event.durable_seq) <= state.lastSeq) return state;

  const seenEventIds = new Set(state.seenEventIds);
  seenEventIds.add(event.event_id);
  return {
    lastSeq: Number(event.durable_seq),
    seenEventIds,
    events: [...state.events, event],
  };
}

/**
 * Reduce non-durable token/PCM events. A reconnect starts a new epoch at seq
 * 1; the previous epoch is retired so a late frame cannot overwrite the new
 * stream. Durable progress/final events never participate in this ordering.
 */
export function reduceLiveStreamEvent(
  state: LiveRunState,
  event: RunEventEnvelope,
): LiveRunState {
  const cursor = event.live_cursor;
  if (!validEnvelope(event) || event.durable_seq != null || cursor == null) return state;
  if (!hasText(cursor.stream_epoch) || !Number.isSafeInteger(cursor.live_seq) || cursor.live_seq <= 0) return state;
  if (state.seenEventIds.has(event.event_id) || state.retiredEpochs.has(cursor.stream_epoch)) return state;

  let currentEpoch = state.currentEpoch;
  let lastSeq = state.lastSeq;
  const retiredEpochs = new Set(state.retiredEpochs);

  if (currentEpoch == null) {
    if (cursor.live_seq !== 1) return state;
    currentEpoch = cursor.stream_epoch;
    lastSeq = 0;
  } else if (cursor.stream_epoch !== currentEpoch) {
    if (cursor.live_seq !== 1) return state;
    retiredEpochs.add(currentEpoch);
    currentEpoch = cursor.stream_epoch;
    lastSeq = 0;
  }

  if (cursor.live_seq <= lastSeq) return state;
  const seenEventIds = new Set(state.seenEventIds);
  seenEventIds.add(event.event_id);
  return {
    currentEpoch,
    lastSeq: cursor.live_seq,
    retiredEpochs,
    seenEventIds,
    events: [...state.events, event],
  };
}
