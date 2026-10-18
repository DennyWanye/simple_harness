// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Multi-session zustand store.
 *
 * Single source of truth for task sessions rendered by the companion
 * message UI. Events stamped with `payload.session_id` get fanned out
 * to the matching slice here.
 *
 * Design note: we deliberately keep this minimal. Session state lives
 * in this in-memory store + on backend's SessionDB; we don't try to
 * persist scrollback to localStorage (browser quota) — backend's
 * memory hierarchy already handles long-term retrieval.
 */
import { create } from "zustand";
import { canonicalJson } from "../context/contextAuthority";
import { dedupeById, projectScopeKey } from "../types/projectSessions";
import { isTerminalTaskRunStatus } from "./sessionTypes";
import type {
  Message,
  SessionStatus,
  SessionState,
  TaskRunProjectionState,
  SessionsStore,
} from "./sessionTypes";
import {
  dedupe_ppt_outline_messages,
  reconcile_live_messages_with_history,
} from "./historyReconcile";
import { WORKFLOW_CARD_EVENTS } from "./workflowProgressParse";
import { applyWorkflowEvent } from "./workflowProgress";
import {
  reduce_companion_events,
  retract_companion_events,
  companion_stream_key,
} from "./companionEvents";

export type { ContextUsageSnapshot } from "../types/messages";

const newId = (): string =>
  globalThis.crypto?.randomUUID?.() ?? `m-${Math.random().toString(36).slice(2, 10)}`;
const blank_session = (sid: string): SessionState => ({
  base_session_id: sid,
  project_root: null,
  project_name: "(untitled)",
  messages: [],
  todos: [],
  token_usage: { prompt: 0, completion: 0 },
  context_usage: null,
  status: "idle",
  last_activity: Date.now(),
  inflight: false,
  active_run_id: null,
  selected_run_id: null,
  run_projections: {},
  companion_events: [],
  current_iteration: 0,
  max_iterations: 50,
  tool_signature_repeat: 0,
  supervisor_severity: "green",
  supervisor_alert: null,
  supervisor_inbox: [],
  // multi-provider-management Phase 5: default = no binding ⇒ "Global Chain".
  provider_id: null,
  preferred_model: null,
  // code-session-model-params S2: no params ⇒ provider defaults.
  model_params: null,
  provider_incarnation_id: null,
  provider_config_revision: null,
  binding_epoch: 0,
});
export const useSessionsStore = create<SessionsStore>((set) => ({
  // 2026-08-09：不再预置保留会话 `default`。启动时零会话、无选中会话；
  // 首个会话由用户新建，或由输入框空态直发（chat_v2 + new_session）产生。
  active_sid: "",
  sessions: {},
  companion_owner: null,
  companion_provisional_streams: {},
  inflight_count: 0,
  inflight_max: 2,
  project_catalog: {
    revision: null,
    projects: [],
    next_cursor: null,
    loading: false,
    error: null,
  },
  project_session_pages: {},
  project_inspections: {},

  begin_project_catalog(append = false) {
    set((state) => ({
      project_catalog: {
        ...(append
          ? state.project_catalog
          : { ...state.project_catalog, projects: [], next_cursor: null }),
        loading: true,
        error: null,
      },
    }));
  },

  apply_project_catalog_page(page, append = false) {
    set((state) => {
      if (
        state.project_catalog.revision !== null &&
        page.catalog_revision < state.project_catalog.revision
      )
        return state;
      const sameRevision = state.project_catalog.revision === page.catalog_revision;
      const base = append && sameRevision ? state.project_catalog.projects : [];
      return {
        project_catalog: {
          revision: page.catalog_revision,
          projects: dedupeById([...base, ...page.items], page.pinned, (p) => p.project_id),
          next_cursor: page.next_cursor,
          loading: false,
          error: null,
        },
      };
    });
  },

  fail_project_catalog(error) {
    set((state) => ({ project_catalog: { ...state.project_catalog, loading: false, error } }));
  },

  begin_project_sessions(scopeKey) {
    set((state) => ({
      project_session_pages: {
        ...state.project_session_pages,
        [scopeKey]: {
          ...(state.project_session_pages[scopeKey] ?? {
            revision: null,
            sessions: [],
            next_cursor: null,
          }),
          loading: true,
          error: null,
        },
      },
    }));
  },

  apply_project_session_page(page, append = false) {
    const scopeKey = projectScopeKey({
      scope_kind: page.scope.kind,
      project_id: page.scope.project_id,
    } as Parameters<typeof projectScopeKey>[0]);
    set((state) => {
      const current = state.project_session_pages[scopeKey];
      if (
        current?.revision !== null &&
        current?.revision !== undefined &&
        page.catalog_revision < current.revision
      ) {
        return state;
      }
      const sameRevision = current?.revision === page.catalog_revision;
      const base = append && sameRevision ? current.sessions : [];
      return {
        project_session_pages: {
          ...state.project_session_pages,
          [scopeKey]: {
            revision: page.catalog_revision,
            sessions: dedupeById([...base, ...page.items], page.pinned, (s) => s.session_id),
            next_cursor: page.next_cursor,
            loading: false,
            error: null,
          },
        },
      };
    });
  },

  fail_project_sessions(scopeKey, error) {
    set((state) => ({
      project_session_pages: {
        ...state.project_session_pages,
        [scopeKey]: {
          ...(state.project_session_pages[scopeKey] ?? {
            revision: null,
            sessions: [],
            next_cursor: null,
          }),
          loading: false,
          error,
        },
      },
    }));
  },

  apply_project_inspection(inspection) {
    set((state) => ({
      project_inspections: {
        ...state.project_inspections,
        [inspection.project.project_id]: inspection,
      },
    }));
  },

  set_active(sid) {
    // Auto-create slot if frontend asks to switch to an unknown sid
    // (e.g. dashboard tile before WS event landed).
    set((state) => {
      if (!state.sessions[sid]) {
        return {
          active_sid: sid,
          sessions: { ...state.sessions, [sid]: blank_session(sid) },
        };
      }
      return { active_sid: sid };
    });
  },

  ensure(sid, init) {
    set((state) => {
      if (state.sessions[sid]) return state;
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...blank_session(sid), ...init },
        },
      };
    });
  },

  upsert(sid, patch) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, ...patch, last_activity: Date.now() },
        },
      };
    });
  },

  upsert_context_usage(snapshot) {
    let accepted = false;
    set((state) => {
      const sid = typeof snapshot.session_id === "string" ? snapshot.session_id.trim() : "";
      const incomingVersion = snapshot.version;
      if (!sid || !Number.isInteger(incomingVersion) || (incomingVersion as number) < 0) {
        return state;
      }
      const cur = state.sessions[sid] ?? blank_session(sid);
      const previous = cur.context_usage;
      const previousVersion = previous?.version;
      if (previous && Number.isInteger(previousVersion)) {
        if ((incomingVersion as number) < (previousVersion as number)) return state;
        if ((incomingVersion as number) === (previousVersion as number)) {
          if (canonicalJson(previous) !== canonicalJson(snapshot)) return state;
          accepted = true;
          return state;
        }
      }
      accepted = true;
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            context_usage: snapshot,
            last_activity: Date.now(),
          },
        },
      };
    });
    return accepted;
  },

  push_message(sid, msg) {
    const id = msg.id ?? newId();
    const ts = msg.ts ?? Date.now();
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      const nextMessages = dedupe_ppt_outline_messages([...cur.messages, { ...msg, id, ts }]);
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            messages: nextMessages,
            last_activity: ts,
          },
        },
      };
    });
  },

  set_continuation_status(sid, requestId, status, error) {
    if (!requestId) return;
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      let changed = false;
      const messages = cur.messages.map((message) => {
        if (message.role !== "user" || message.request_id !== requestId) {
          return message;
        }
        changed = true;
        return {
          ...message,
          continuation_status: status,
          continuation_error: error || undefined,
        };
      });
      if (!changed) return {};
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            messages,
            last_activity: Date.now(),
          },
        },
      };
    });
  },

  upsert_run_projection(sid, runId, patch) {
    if (!runId) return;
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      const now = Date.now();
      const currentProjections = cur.run_projections ?? {};
      const existing = currentProjections[runId];
      const staleNonTerminalLifecycle =
        isTerminalTaskRunStatus(existing?.status) &&
        patch.status !== undefined &&
        !isTerminalTaskRunStatus(patch.status);
      if (staleNonTerminalLifecycle) {
        // Ignore the whole stale lifecycle frame, not just its status. Its
        // timestamps/version/scope belong to an older observation and must
        // not mutate the authoritative terminal projection or its duration.
        return state;
      }
      // A durable Run is immutable after reaching a terminal state. WebSocket
      // delivery can legitimately reorder a terminal lifecycle snapshot and an
      // older permission/progress event, so never let that older event reopen
      // an already completed, failed, or cancelled Run in the UI.
      const lifecycleStatus = isTerminalTaskRunStatus(existing?.status)
        ? existing!.status
        : (patch.status ?? existing?.status ?? "starting");
      const nextProjection: TaskRunProjectionState = {
        ...existing,
        ...patch,
        run_id: runId,
        task_scope_id: patch.task_scope_id ?? existing?.task_scope_id ?? "",
        projection_id: patch.projection_id ?? existing?.projection_id,
        version: patch.version ?? existing?.version ?? 0,
        conversation_boundary_ref:
          patch.conversation_boundary_ref ?? existing?.conversation_boundary_ref,
        conversation_boundary_version:
          patch.conversation_boundary_version ?? existing?.conversation_boundary_version,
        request_id: patch.request_id ?? existing?.request_id,
        turn_id: patch.turn_id ?? existing?.turn_id,
        status: lifecycleStatus,
        inflight: isTerminalTaskRunStatus(lifecycleStatus)
          ? false
          : (patch.inflight ?? existing?.inflight ?? true),
        ui_state: patch.ui_state ?? existing?.ui_state ?? "open",
        started_at: patch.started_at ?? existing?.started_at ?? now,
        last_activity: patch.last_activity ?? now,
      };
      const projections = {
        ...currentProjections,
        [runId]: nextProjection,
      };
      const requestId = nextProjection.request_id;
      const messages = requestId
        ? cur.messages.map((message) =>
            message.request_id === requestId && !message.run_id
              ? {
                  ...message,
                  run_id: runId,
                  task_scope_id: nextProjection.task_scope_id || message.task_scope_id,
                  conversation_boundary_ref:
                    nextProjection.conversation_boundary_ref ?? message.conversation_boundary_ref,
                }
              : message,
          )
        : cur.messages;
      const selectedRunId = cur.selected_run_id ?? runId;
      const running = Object.values(projections).filter((item) => item.inflight);
      const waiting = Object.values(projections).filter(
        (item) => item.status === "waiting" && item.ui_state !== "closed",
      );
      const activeRunId = projections[selectedRunId]?.inflight
        ? selectedRunId
        : (running[0]?.run_id ?? null);
      const selectedProjection = projections[selectedRunId];
      const sessionStatus: SessionStatus =
        running.length > 0
          ? "running"
          : waiting.length > 0
            ? cur.status === "permission"
              ? "permission"
              : "running"
            : selectedProjection?.status === "failed"
              ? "error"
              : "idle";
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            messages,
            run_projections: projections,
            selected_run_id: selectedRunId,
            active_run_id: activeRunId,
            inflight: running.length > 0,
            status: sessionStatus,
            last_activity: now,
          },
        },
      };
    });
  },

  select_run_projection(sid, runId) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      const currentProjections = cur.run_projections ?? {};
      if (runId !== null && !currentProjections[runId]) return state;
      const projections = Object.fromEntries(
        Object.entries(currentProjections).map(([id, item]) => [
          id,
          {
            ...item,
            ui_state:
              id === runId ? "open" : item.ui_state === "open" ? "background" : item.ui_state,
          },
        ]),
      ) as Record<string, TaskRunProjectionState>;
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            run_projections: projections,
            selected_run_id: runId,
            active_run_id:
              runId && projections[runId]?.inflight
                ? runId
                : (Object.values(projections).find((item) => item.inflight)?.run_id ?? null),
            last_activity: Date.now(),
          },
        },
      };
    });
  },

  close_run_projection(sid, runId) {
    set((state) => {
      const cur = state.sessions[sid];
      const currentProjections = cur?.run_projections ?? {};
      const target = currentProjections[runId];
      if (!cur || !target) return state;
      const projections = {
        ...currentProjections,
        [runId]: {
          ...target,
          ui_state: "closed" as const,
          last_activity: Date.now(),
        },
      };
      const nextSelected =
        cur.selected_run_id === runId
          ? (Object.values(projections).find((item) => item.ui_state !== "closed")?.run_id ?? null)
          : (cur.selected_run_id ?? null);
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            run_projections: projections,
            selected_run_id: nextSelected,
            active_run_id:
              nextSelected && projections[nextSelected]?.inflight
                ? nextSelected
                : (Object.values(projections).find((item) => item.inflight)?.run_id ?? null),
            last_activity: Date.now(),
          },
        },
      };
    });
  },

  set_messages(sid, messages) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      // Plan / PPT cards are ephemeral frontend projections. Preserve only
      // their durable decision envelopes while history is reloaded.
      const awaitingCards = cur.messages.filter(
        (m) =>
          (m.role === "ppt_outline" && m.ppt_outline_awaiting) ||
          (m.role === "plan" && m.plan_awaiting_confirm),
      );
      const reloadedIds = new Set(messages.map((m) => m.id));
      const preserved = awaitingCards.filter((m) => !reloadedIds.has(m.id));
      const nextMessages = dedupe_ppt_outline_messages([...messages, ...preserved]);
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            messages: nextMessages,
            last_activity: Date.now(),
          },
        },
      };
    });
  },

  resolve_plan(sid, msgId) {
    set((state) => {
      const cur = state.sessions[sid];
      if (!cur) return {};
      let cleared = false;
      // clear by id, or (no id) the last still-awaiting plan
      const next = [...cur.messages];
      for (let i = next.length - 1; i >= 0; i--) {
        const m = next[i];
        if (m.role !== "plan" || !m.plan_awaiting_confirm) continue;
        if (msgId && m.id !== msgId) continue;
        next[i] = { ...m, plan_awaiting_confirm: false };
        cleared = true;
        if (!msgId) break; // no id → only the latest one
        break;
      }
      if (!cleared) return {};
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, messages: next },
        },
      };
    });
  },

  reduce_workflow_event(sid, event) {
    let handled = false;
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      const reduced = applyWorkflowEvent(cur.messages, event);
      handled = reduced.handled;
      if (!reduced.handled || reduced.messages === cur.messages) return state;
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            messages: reduced.messages,
            last_activity: Date.now(),
          },
        },
      };
    });
    return handled;
  },

  merge_history_messages(sid, messages, workflowEvents) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      const consumedEventIds = new Set(
        workflowEvents
          .filter((event) => WORKFLOW_CARD_EVENTS.has(String(event.event_type || "")))
          .map((event) => String(event.event_id || ""))
          .filter(Boolean),
      );
      const byId = new Map<string, Message>();
      const reconciled = reconcile_live_messages_with_history(cur.messages, messages);
      for (const message of reconciled) {
        if (consumedEventIds.has(message.id) && message.role !== "workflow_progress") {
          continue;
        }
        if (!byId.has(message.id)) byId.set(message.id, message);
      }
      let merged = [...byId.values()]
        .map((message, index) => ({ message, index }))
        .sort((a, b) => a.message.ts - b.message.ts || a.index - b.index)
        .map(({ message }) => message);
      const orderedEvents = workflowEvents
        .map((event, index) => ({ event, index }))
        .sort((a, b) => {
          const run = String(a.event.run_id || "").localeCompare(String(b.event.run_id || ""));
          return run || Number(a.event.seq || 0) - Number(b.event.seq || 0) || a.index - b.index;
        });
      for (const { event } of orderedEvents) {
        merged = applyWorkflowEvent(merged, event).messages;
      }
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            messages: dedupe_ppt_outline_messages(merged),
            last_activity: Date.now(),
          },
        },
      };
    });
  },

  set_companion_owner(owner) {
    set((state) => {
      const previous = state.companion_owner;
      if (
        previous?.profile_id === owner?.profile_id &&
        previous?.profile_generation === owner?.profile_generation
      ) {
        return state;
      }
      const sessions = Object.fromEntries(
        Object.entries(state.sessions).map(([sid, session]) => [
          sid,
          {
            ...session,
            companion_events: [],
          },
        ]),
      );
      return {
        companion_owner: owner,
        companion_provisional_streams: {},
        sessions,
      };
    });
  },

  reduce_companion_event(event) {
    let handled = false;
    set((state) => {
      const sid = event.session_id || state.active_sid;
      const session = state.sessions[sid] ?? blank_session(sid);
      const occurrences = Object.entries(state.sessions).flatMap(([candidateSid, candidate]) =>
        (candidate.companion_events ?? [])
          .filter((item) => item.event_id === event.event_id)
          .map((item) => ({ sid: candidateSid, event: item })),
      );
      const previous = occurrences
        .map((item) => item.event)
        .sort(
          (left, right) =>
            (right.route_version ?? 0) - (left.route_version ?? 0) || right.seq - left.seq,
        )[0];
      const targetEvents = session.companion_events ?? [];
      const reducerInput =
        previous && !targetEvents.some((item) => item.event_id === event.event_id)
          ? [...targetEvents, previous]
          : targetEvents;
      const reduced = reduce_companion_events(reducerInput, event, state.companion_owner);
      handled = reduced.handled;
      const needsMove = occurrences.length > 1 || occurrences.some((item) => item.sid !== sid);
      if (!reduced.handled || (reduced.events === targetEvents && !needsMove)) {
        return state;
      }
      const streamPrefix =
        event.decision?.kind === "action_confirmation" ? `${event.decision.run_id}\u001f` : "";
      const companion_provisional_streams = streamPrefix
        ? Object.fromEntries(
            Object.entries(state.companion_provisional_streams).filter(
              ([key]) => !key.startsWith(streamPrefix),
            ),
          )
        : state.companion_provisional_streams;
      const sessions = Object.fromEntries(
        Object.entries(state.sessions).map(([candidateSid, candidate]) => {
          const filtered = (candidate.companion_events ?? []).filter(
            (item) => item.event_id !== event.event_id,
          );
          return [
            candidateSid,
            {
              ...candidate,
              companion_events: candidateSid === sid ? reduced.events : filtered,
              ...(candidateSid === sid ? { last_activity: Date.now() } : {}),
            },
          ];
        }),
      );
      if (!sessions[sid]) {
        sessions[sid] = {
          ...session,
          companion_events: reduced.events,
          last_activity: Date.now(),
        };
      }
      return {
        companion_provisional_streams,
        sessions,
      };
    });
    return handled;
  },

  retract_companion_projection(event) {
    let handled = false;
    set((state) => {
      const sid = event.session_id || state.active_sid;
      const session = state.sessions[sid] ?? blank_session(sid);
      const occurrences = Object.entries(state.sessions).flatMap(([candidateSid, candidate]) =>
        (candidate.companion_events ?? [])
          .filter((item) => item.event_id === event.event_id)
          .map((item) => ({ sid: candidateSid, event: item })),
      );
      const previous = occurrences
        .map((item) => item.event)
        .sort(
          (left, right) =>
            (right.redaction_version ?? 0) - (left.redaction_version ?? 0) ||
            (right.route_version ?? 0) - (left.route_version ?? 0) ||
            right.seq - left.seq,
        )[0];
      const targetEvents = session.companion_events ?? [];
      const reducerInput =
        previous && !targetEvents.some((item) => item.event_id === event.event_id)
          ? [...targetEvents, previous]
          : targetEvents;
      const reduced = retract_companion_events(reducerInput, event, state.companion_owner);
      handled = reduced.handled;
      const needsMove = occurrences.length > 1 || occurrences.some((item) => item.sid !== sid);
      if (!reduced.handled || (reduced.events === targetEvents && !needsMove)) {
        return state;
      }
      const sessions = Object.fromEntries(
        Object.entries(state.sessions).map(([candidateSid, candidate]) => {
          return [
            candidateSid,
            {
              ...candidate,
              companion_events:
                candidateSid === sid
                  ? reduced.events
                  : (candidate.companion_events ?? []).filter(
                      (item) => item.event_id !== event.event_id,
                    ),
              ...(candidateSid === sid ? { last_activity: Date.now() } : {}),
            },
          ];
        }),
      );
      if (!sessions[sid]) {
        sessions[sid] = {
          ...session,
          companion_events: reduced.events,
          last_activity: Date.now(),
        };
      }
      return { sessions };
    });
    return handled;
  },

  upsert_companion_provisional(runId, invocationId, streamEpoch, content) {
    if (!runId || !invocationId || !streamEpoch) return;
    const key = companion_stream_key(runId, invocationId, streamEpoch);
    set((state) => ({
      companion_provisional_streams: {
        ...state.companion_provisional_streams,
        [key]: (state.companion_provisional_streams[key] || "") + content,
      },
    }));
  },

  clear_companion_provisional(runId, invocationId, streamEpoch) {
    set((state) => {
      if (!runId && !invocationId && !streamEpoch) {
        return { companion_provisional_streams: {} };
      }
      const exact =
        runId && invocationId && streamEpoch
          ? companion_stream_key(runId, invocationId, streamEpoch)
          : null;
      return {
        companion_provisional_streams: Object.fromEntries(
          Object.entries(state.companion_provisional_streams).filter(([key]) => {
            if (exact) return key !== exact;
            const [run, invocation, epoch] = key.split("\u001f");
            return !(
              (!runId || run === runId) &&
              (!invocationId || invocation === invocationId) &&
              (!streamEpoch || epoch === streamEpoch)
            );
          }),
        ),
      };
    });
  },

  resolve_ppt_outline(sid, outlineId, decisionStatus) {
    set((state) => {
      const cur = state.sessions[sid];
      if (!cur) return {};
      let cleared = false;
      const next = cur.messages.map((m) => {
        if (
          m.role !== "ppt_outline" ||
          m.outline_id !== outlineId ||
          (!m.ppt_outline_awaiting && !decisionStatus)
        ) {
          return m;
        }
        cleared = true;
        return {
          ...m,
          ppt_outline_awaiting: false,
          ppt_outline_decision_status: decisionStatus ?? m.ppt_outline_decision_status,
        };
      });
      if (!cleared) return {};
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, messages: next },
        },
      };
    });
  },

  upsert_todos(sid, todos) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, todos, last_activity: Date.now() },
        },
      };
    });
  },

  remove(sid) {
    set((state) => {
      if (!state.sessions[sid]) return state;
      const next = { ...state.sessions };
      delete next[sid];
      // 删掉当前会话就落到剩下的任意一条；一条不剩即空态（""），
      // 不再回落到保留会话。
      const active = state.active_sid === sid ? (Object.keys(next)[0] ?? "") : state.active_sid;
      return { sessions: next, active_sid: active };
    });
  },

  set_inflight(delta) {
    set((state) => ({
      inflight_count: Math.max(0, state.inflight_count + delta),
    }));
  },

  apply_supervisor_alert(sid, alert) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      const prev_inbox = cur.supervisor_inbox ?? [];
      // Dedup by alert_id — if the same alert lands twice (ws reconnect
      // replays, double broadcast), don't grow the badge.
      const filtered = prev_inbox.filter((a) => a.alert_id !== alert.alert_id);
      const next_inbox =
        alert.severity === "yellow" || alert.severity === "red"
          ? [alert, ...filtered].slice(0, 50)
          : filtered;
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            supervisor_severity: alert.severity,
            supervisor_alert: alert,
            supervisor_inbox: next_inbox,
            last_activity: Date.now(),
          },
        },
      };
    });
  },

  clear_supervisor_alert(sid) {
    set((state) => {
      const cur = state.sessions[sid];
      if (!cur) return state;
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, supervisor_alert: null, supervisor_severity: "green" },
        },
      };
    });
  },

  dismiss_alert(sid, alert_id) {
    set((state) => {
      const cur = state.sessions[sid];
      if (!cur) return state;
      const next_inbox = (cur.supervisor_inbox ?? []).filter((a) => a.alert_id !== alert_id);
      const next_alert =
        cur.supervisor_alert && cur.supervisor_alert.alert_id === alert_id
          ? null
          : (cur.supervisor_alert ?? null);
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            supervisor_inbox: next_inbox,
            supervisor_alert: next_alert,
            // Severity downgrades to green only when nothing pending.
            supervisor_severity: next_inbox.length === 0 ? "green" : cur.supervisor_severity,
          },
        },
      };
    });
  },

  dismiss_all_alerts(severity) {
    set((state) => {
      const next: Record<string, SessionState> = {};
      for (const [sid, s] of Object.entries(state.sessions)) {
        const remaining = (s.supervisor_inbox ?? []).filter((a) => a.severity !== severity);
        const cur_alert_dismissed = s.supervisor_alert && s.supervisor_alert.severity === severity;
        next[sid] = {
          ...s,
          supervisor_inbox: remaining,
          supervisor_alert: cur_alert_dismissed ? null : (s.supervisor_alert ?? null),
          supervisor_severity: remaining.length === 0 ? "green" : s.supervisor_severity,
        };
      }
      return { sessions: next };
    });
  },
}));
// ----------------------------------------------------------------------
// Concurrency limiter — wrap outbound chat sends so the relay doesn't
// see N parallel chat_v2 messages from N tiles all at once. Default
// max is 2 simultaneous in-flight LLM round-trips; the rest queue.
// ----------------------------------------------------------------------

export class ConcurrencyLimiter {
  private inflight = 0;
  private queue: (() => void)[] = [];
  private max: number;

  constructor(max: number) {
    this.max = max;
  }

  async run<T>(fn: () => Promise<T>): Promise<T> {
    if (this.inflight >= this.max) {
      await new Promise<void>((resolve) => this.queue.push(resolve));
    }
    this.inflight++;
    useSessionsStore.getState().set_inflight(+1);
    try {
      return await fn();
    } finally {
      this.inflight--;
      useSessionsStore.getState().set_inflight(-1);
      const next = this.queue.shift();
      if (next) next();
    }
  }

  get_max() {
    return this.max;
  }
}
export const chatLimiter = new ConcurrencyLimiter(2);
