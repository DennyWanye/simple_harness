// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * missionsStore — 任务编排视图的界面投影（plans/2026-09-11-orchestrator-host-integration
 * §3.9；用户 Phase3 P3.1 §3.4）。
 *
 * 只是投影：正式状态在编排库，改动只经控制通道命令。
 *
 * 两个 seq 分开存（代码评审第 1 轮 P1-1）：
 * - `lastSeq`：`mission_changed` 推送里看到的最新 seq，只用来防倒退（迟到的旧推送不让
 *   状态回退）和判断有没有新事件；
 * - `eventCursor`：已合并事件的游标（已合并事件的最大 seq，或事件页的 `through_seq`，
 *   取大者），拉事件一律从它往后拉。推送不动它，所以推送之后的事件不会被跳过。
 *
 * 事件按 seq 合并去重；推送里出现未知 Mission 时标记列表需要重拉（P3.1-A05）。
 */
import { create } from "zustand";

export interface MissionRow {
  id: string;
  mission_id?: string;
  goal: string;
  status: string;
  stop_reason: string | null;
  created_at: number;
  pending_approvals: number;
  blocked?: boolean;
  /** P3.1 §3.4 界面状态词（received/queued/running/…），由后端投影给出。 */
  ui_state?: string;
}

export interface MissionEvent {
  seq: number;
  type: string;
  created_at: number;
  summary?: string;
  task_id?: string | null;
  attempt_id?: string | null;
  actor_type?: string | null;
}

export interface MissionChange {
  mission_id: string;
  status: string;
  last_seq: number;
}

export interface OrchestrationStatus {
  available: boolean;
  state: string;
  reason: string | null;
  orchestrator_version?: string;
  sdk_version?: string;
  model?: { provider_id: string; configured: string; requested: string; price: string } | null;
  active_missions?: number;
  allowed_tools?: string[];
  test_scenario?: string | null;
  /** 部署默认预算：表单留空的项由后端按它补齐（没有无上限的 Mission）。 */
  mission_budget_defaults?: { max_tokens: number; max_attempts: number } | null;
}

type Json = Record<string, unknown>;

/** 控制通道 JSON 的宽松读取：非对象一律当空对象。 */
export function asRecord(value: unknown): Json {
  return value != null && typeof value === "object" && !Array.isArray(value) ? (value as Json) : {};
}

export function asList(value: unknown): Json[] {
  return Array.isArray(value) ? value.map(asRecord) : [];
}

export function asText(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

/** 信封 `request_id`：配对请求与应答，也作幂等键。 */
export function newRequestKey(): string {
  const random = globalThis.crypto?.randomUUID?.();
  return random ?? `k-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 列表行归一：容忍只有 `mission_id` 的旧形态，缺 ui_state 就不带（界面回退显示 status）。 */
function toRow(value: unknown): MissionRow {
  const raw = asRecord(value);
  const row: MissionRow = {
    id: asText(raw.id) || asText(raw.mission_id),
    goal: asText(raw.goal),
    status: asText(raw.status),
    stop_reason: raw.stop_reason == null ? null : asText(raw.stop_reason),
    created_at: Number(raw.created_at) || 0,
    pending_approvals: Number(raw.pending_approvals) || 0,
    blocked: raw.blocked === true,
  };
  if (raw.mission_id != null) row.mission_id = asText(raw.mission_id);
  const uiState = asText(raw.ui_state);
  if (uiState) row.ui_state = uiState;
  return row;
}

interface MissionsState {
  status: OrchestrationStatus | null;
  missions: MissionRow[];
  events: Record<string, MissionEvent[]>;
  /** 推送里看到的最新 seq（防倒退 / 判断有没有新事件）。 */
  lastSeq: Record<string, number>;
  /** 已加载事件的游标；拉事件的 after_seq 只用它。 */
  eventCursor: Record<string, number>;
  eventsHasMore: Record<string, boolean>;
  /** 事件分页请求在途（在途时不显示「加载更多事件」）。 */
  eventsLoading: Record<string, boolean>;
  listStale: boolean;
  selectedId: string | null;
  detail: Record<string, unknown> | null;
  policy: Record<string, unknown> | null;
  error: string | null;
  setStatus: (status: OrchestrationStatus) => void;
  setMissions: (missions: ReadonlyArray<MissionRow | Json>) => void;
  appendEvents: (missionId: string, events: MissionEvent[], hasMore?: boolean, throughSeq?: number) => void;
  setEventsLoading: (missionId: string, loading: boolean) => void;
  clearEventsLoading: () => void;
  applyChange: (change: MissionChange) => void;
  select: (missionId: string | null) => void;
  setDetail: (detail: Record<string, unknown> | null) => void;
  setPolicy: (policy: Record<string, unknown> | null) => void;
  setError: (error: string | null) => void;
  pendingApprovalTotal: () => number;
  reset: () => void;
}

const initial = {
  status: null,
  missions: [],
  events: {},
  lastSeq: {},
  eventCursor: {},
  eventsHasMore: {},
  eventsLoading: {},
  listStale: false,
  selectedId: null,
  detail: null,
  policy: null,
  error: null,
};

export const useMissionsStore = create<MissionsState>((set, get) => ({
  ...initial,
  setStatus: (status) => set({ status }),
  setMissions: (missions) => set({ missions: missions.map(toRow).filter((row) => row.id), listStale: false }),
  appendEvents: (missionId, incoming, hasMore, throughSeq) =>
    set((state) => {
      const bySeq = new Map<number, MissionEvent>();
      for (const event of state.events[missionId] ?? []) bySeq.set(event.seq, event);
      for (const event of incoming) {
        const seq = Number(event?.seq);
        if (Number.isFinite(seq)) bySeq.set(seq, { ...event, seq });
      }
      const merged = [...bySeq.values()].sort((a, b) => a.seq - b.seq);
      const top = merged.length ? merged[merged.length - 1].seq : 0;
      const through = typeof throughSeq === "number" && Number.isFinite(throughSeq) ? throughSeq : 0;
      return {
        events: { ...state.events, [missionId]: merged },
        eventCursor: {
          ...state.eventCursor,
          [missionId]: Math.max(state.eventCursor[missionId] ?? 0, top, through),
        },
        eventsHasMore:
          hasMore === undefined
            ? state.eventsHasMore
            : { ...state.eventsHasMore, [missionId]: hasMore },
      };
    }),
  setEventsLoading: (missionId, loading) =>
    set((state) => ({ eventsLoading: { ...state.eventsLoading, [missionId]: loading } })),
  clearEventsLoading: () => set({ eventsLoading: {} }),
  applyChange: (change) =>
    set((state) => {
      const previous = state.lastSeq[change.mission_id] ?? 0;
      if (change.last_seq < previous) return {}; // a late, older push never moves back
      const lastSeq = { ...state.lastSeq, [change.mission_id]: change.last_seq };
      const known = state.missions.some((row) => row.id === change.mission_id);
      if (!known) return { listStale: true, lastSeq };
      return {
        missions: state.missions.map((row) => {
          if (row.id !== change.mission_id) return row;
          if (row.status === change.status) return row;
          // the status moved: the old ui_state word is stale until the list is refetched
          const next: MissionRow = { ...row, status: change.status };
          delete next.ui_state;
          return next;
        }),
        lastSeq,
      };
    }),
  select: (missionId) => set({ selectedId: missionId, detail: null, error: null }),
  setDetail: (detail) => set({ detail }),
  setPolicy: (policy) => set({ policy }),
  setError: (error) => set({ error }),
  pendingApprovalTotal: () =>
    get().missions.reduce((total, row) => total + (row.pending_approvals || 0), 0),
  reset: () => set({ ...initial }),
}));
