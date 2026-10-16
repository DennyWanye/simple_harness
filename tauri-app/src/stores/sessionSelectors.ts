// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { SessionStatus, SupervisorAlertEntry, SessionState } from "./sessionTypes";

// ----------------------------------------------------------------------
// Inbox selectors. Pure helpers — components call these from useMemo.
// ----------------------------------------------------------------------

export function count_unhandled_by_severity(
  sessions: Record<string, SessionState>,
  severity: "yellow" | "red",
): number {
  let n = 0;
  for (const s of Object.values(sessions)) {
    for (const a of s.supervisor_inbox ?? []) {
      if (a.severity === severity) n += 1;
    }
  }
  return n;
}

export interface InboxItem extends SupervisorAlertEntry {
  session_id: string;
  project_name: string;
}

export function collect_inbox(
  sessions: Record<string, SessionState>,
  severity: "yellow" | "red",
): InboxItem[] {
  const out: InboxItem[] = [];
  for (const s of Object.values(sessions)) {
    for (const a of s.supervisor_inbox ?? []) {
      if (a.severity === severity) {
        out.push({
          ...a,
          session_id: s.base_session_id,
          project_name: s.project_name || s.base_session_id,
        });
      }
    }
  }
  // Newest first
  out.sort((a, b) => b.received_at - a.received_at);
  return out;
}

// ----------------------------------------------------------------------
// P5-S3 — severity score + pet focus selectors.
//
// Pure functions over SessionState so they're trivial to unit-test and
// to memoize at call sites. Backend's watchdog also computes a similar
// score for telemetry but the pet UI's "which session looks worst" is
// frontend-derived (no need to round-trip ws for every event).
// ----------------------------------------------------------------------

/** Decompose severity for debug overlay display. */
export interface SeverityBreakdown {
  base: number;
  age: number;
  repeat: number;
  supervisor: number;
  iteration: number;
  total: number;
}

export const STATUS_BASE: Record<SessionStatus, number> = {
  idle: 0,
  thinking: 5,
  running: 10,
  permission: 25,
  error: 60,
};

export const SUPERVISOR_BOOST: Record<"green" | "yellow" | "red", number> = {
  green: 0,
  yellow: 20,
  red: 50,
};

/** Compute severity score breakdown for one session.
 * See spec D7 for the formula.
 * `now` is injectable for unit tests; defaults to Date.now(). */
export function severity_score_breakdown(
  s: SessionState,
  now: number = Date.now(),
): SeverityBreakdown {
  const base = STATUS_BASE[s.status] ?? 0;
  const age_seconds = Math.max(0, (now - (s.last_activity || now)) / 1000);
  // log2 of minutes-since-activity, clamped at 30 points (roughly 32 min
  // saturates the dial).
  const age = Math.min(30, Math.log2(Math.max(1, age_seconds / 60)) * 6);
  const repeat = Math.min(40, Math.max(0, s.tool_signature_repeat ?? 0) * 10);
  const supervisor = SUPERVISOR_BOOST[s.supervisor_severity ?? "green"] ?? 0;
  const cur = s.current_iteration ?? 0;
  const max = Math.max(1, s.max_iterations ?? 50);
  const iteration = (cur / max) * 10;
  const total = base + age + repeat + supervisor + iteration;
  return { base, age, repeat, supervisor, iteration, total };
}

/** Pure helper used by selectors + tests. */
export function severity_score(s: SessionState, now: number = Date.now()): number {
  return severity_score_breakdown(s, now).total;
}

/** Return the sid of the most-dangerous task session (highest score), or
 * null if the store has no sessions to evaluate. A session is eligible
 * when it has task metadata, an active run, or a supervisor alert.
 *
 * 2026-08-09：原先这里排除保留会话 `"default"`（免得桌宠为闲聊频道自我聚焦）。
 * 保留会话已移除，所有会话同构参与评分。 */
export function pet_focus_sid(
  sessions: Record<string, SessionState>,
  now: number = Date.now(),
): string | null {
  let best_sid: string | null = null;
  let best_score = -1;
  for (const [sid, s] of Object.entries(sessions)) {
    // Any durable task metadata, active run, or supervisor alert qualifies.
    const has_task_meta = !!(s.project_root || s.active_run_id);
    const has_active_alert = !!s.supervisor_alert;
    if (!has_task_meta && !has_active_alert) continue;
    const score = severity_score(s, now);
    if (score > best_score) {
      best_score = score;
      best_sid = sid;
    }
  }
  return best_sid;
}
