// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * MissionsView — 任务编排视图（plans/2026-09-11-orchestrator-host-integration §3.9；
 * 用户 Phase3 P3.1）。
 *
 * 通过控制通道收发 `mission_*` / `orchestration_*` 消息；界面只是投影，正式状态在编排库。
 * 状态与列表由 App 层常驻的 `useMissionsFeed` 负责（代码评审 P2-5）；这里只处理详情、
 * 事件、各命令的应答，以及选中 Mission 的刷新。
 *
 * 事件（代码评审 P1-1 / P2-8）：选中时先 `mission_get`，再从事件游标（`eventCursor`）
 * 按 200 条一页连续分页，一次最多连拉 20 页，超出显示「加载更多事件」；推送的 seq
 * 超过游标就从游标接着拉。同一 Mission 同时只有一页在途，在途时来的推送等这一页回来
 * 后再补拉。时间线只渲染最近 50 条。
 *
 * 模型写的文字一律标注「模型生成，未核实」；验证层按记录原样显示（NOT_REQUIRED 用中性色，
 * 不画成通过）；金额没有价目时显示「未计价」。可访问名称是原生 AX 验收的定位点，改名要同步
 * 改验收脚本。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import { MissionDocument, SourceDrafts, type SourceDraft } from "./MissionDocument";
import { MissionSearch } from "./MissionSearch";
import {
  asList as list,
  asRecord as record,
  asText as text,
  newRequestKey as newKey,
  useMissionsStore,
  type MissionEvent,
  type MissionsChannel,
} from "../stores/missionsStore";

export interface MissionsViewProps {
  channel: MissionsChannel | null;
}

type Json = Record<string, unknown>;

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
/** P3.1 §3.4 界面状态词汇；ui_state 由后端投影给出，界面不自己推断，缺失时回退原始 status。 */
const UI_STATE_LABEL: Record<string, string> = {
  received: "请求已接收",
  queued: "排队",
  running: "运行",
  verifying: "待验证",
  waiting_person: "待人",
  unknown: "UNKNOWN（结果未知）",
  delivered: "正式交付",
  failed: "失败",
  cancelled: "已取消",
};
const LAYER_LABEL: Record<string, string> = {
  PASS: "通过",
  FAIL: "未通过",
  ERROR: "错误",
  NOT_REQUIRED: "不需要",
  SKIPPED: "跳过",
  NEEDS_HUMAN: "待人工",
  SUSPENDED: "待人工",
};
const REJECTION_LABEL: Record<string, string> = {
  stale_source: "来源已失效",
  source_unavailable: "来源不可用",
  used_knowledge_stale: "引用的知识已失效",
  revoked: "已撤销",
  superseded: "已替代",
  not_current: "非当前版本",
};
const WAIT_LABEL: Record<string, string> = {
  source_change: "来源变更审批",
  review: "人工复核",
  action: "动作审批",
  arbitration: "仲裁",
};
const ERROR_TEXT: Record<string, string> = {
  local_tests_disabled: "本机执行测试代码已关闭：成功条件不能使用 pytest:",
  action_criteria_disabled: "这个部署没有启用真实动作：成功条件不能使用 action:",
  secret_rejected: "内容里有像密钥的文本，未接受",
  orchestration_unavailable: "编排服务不可用",
  not_found: "找不到这个对象",
  conflict: "同一个请求键已经对应另一个不同的请求",
  test_scenario_single_mission: "测试场景只允许一个 Mission",
  integrity_error: "产物内容与记录的哈希不一致",
};
/** 视图自己发、自己处理应答的消息类型；其余应答属于常驻订阅或别的视图。 */
const OWN_RESPONSES = new Set([
  "mission_get_response",
  "mission_events_response",
  "orchestration_policy_status_response",
  "mission_create_response",
  "mission_create_with_sources_response",
  "mission_cancel_response",
  "mission_approval_decide_response",
  "mission_takeover_response",
  "mission_comment_response",
  "mission_artifact_read_response",
]);
const EVENT_PAGE_LIMIT = 200;
const MAX_AUTO_PAGES = 20;
const TIMELINE_SIZE = 50;

/** 一个 Mission 的请求在途状态（只在回调里读写）。 */
interface Flight {
  get: boolean;
  getAgain: boolean;
  events: boolean;
  pages: number;
  gap: boolean;
}

function stateLabel(uiState: unknown, status: unknown): string {
  const ui = text(uiState);
  return ui ? UI_STATE_LABEL[ui] ?? ui : text(status);
}

/** 时间戳（秒 / 毫秒 / ISO 字符串）→ 本地 HH:MM:SS；认不出就返回 null。 */
function clock(value: unknown): string | null {
  let ms: number;
  if (typeof value === "number") ms = value < 1e12 ? value * 1000 : value;
  else if (typeof value === "string" && value.trim()) {
    const n = Number(value);
    ms = Number.isFinite(n) ? (n < 1e12 ? n * 1000 : n) : Date.parse(value);
  } else return null;
  if (!Number.isFinite(ms)) return null;
  const d = new Date(ms);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, "0")).join(":");
}

function formatBytes(value: unknown): string {
  const n = Number(value);
  if (value == null || !Number.isFinite(n) || n < 0) return "大小未知";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function shortHash(value: unknown): string {
  return text(value).replace(/^[a-z0-9]+:/i, "").slice(0, 12);
}

function show(value: unknown): string {
  return value != null && typeof value === "object" ? JSON.stringify(value) : text(value);
}

/** `{text, source}` 或旧的纯字符串 → 文字。 */
function plain(value: unknown): string {
  return text(record(value).text ?? value);
}

function waitLine(item: unknown): string {
  if (typeof item === "string") return `等待：${item}`;
  const wait = record(item);
  const kind = text(wait.kind);
  const since = clock(wait.since);
  return `等待：${WAIT_LABEL[kind] ?? (kind || "未知")}${since ? `（自 ${since}）` : ""}`;
}

function eventLine(event: MissionEvent): string {
  const head = `#${event.seq} ${event.type}`;
  if (event.type === "HumanCommentAdded" && event.summary != null) return `${head}：${plain(event.summary)}`;
  return head;
}

const box: React.CSSProperties = {
  background: dark.inset,
  border: `1px solid ${tokens.color.surface.hairline}`,
  borderRadius: tokens.radius.md,
  padding: tokens.space.md,
};

const button: React.CSSProperties = {
  height: tokens.controlHeight,
  padding: `0 ${tokens.space.md}px`,
  borderRadius: tokens.radius.md,
  border: `1px solid ${tokens.color.surface.hairline}`,
  background: dark.panel,
  color: dark.text,
  fontFamily: tokens.font.ui,
  fontSize: tokens.text.base.size,
  cursor: "pointer",
};

const field: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  minHeight: 64,
  padding: tokens.space.sm,
  borderRadius: tokens.radius.md,
  border: `1px solid ${tokens.color.surface.hairline}`,
  background: dark.inset,
  color: dark.text,
  fontFamily: tokens.font.ui,
  fontSize: tokens.text.base.size,
};

const muted: React.CSSProperties = { color: dark.textMuted, fontSize: tokens.text.xs.size };
const heading: React.CSSProperties = { fontWeight: tokens.weight.semibold };

const ModelText: React.FC<{ value: unknown }> = ({ value }) => (
  <span>
    {plain(value)}
    <span style={{ marginLeft: tokens.space.xs, color: dark.textMuted, fontSize: tokens.text.xs.size }}>
      （模型生成，未核实）
    </span>
  </span>
);

/** 验证层 summary：只有 source=model 才标注；source=system 或旧的纯字符串只显示文字。 */
const LayerSummary: React.FC<{ value: unknown }> = ({ value }) =>
  text(record(value).source) === "model" ? <ModelText value={value} /> : <span>{plain(value)}</span>;

const ArtifactPanel: React.FC<{ artifact: Json }> = ({ artifact }) => {
  const encoding = text(artifact.encoding);
  const content = typeof artifact.content === "string" ? artifact.content : null;
  const readable = encoding !== "binary" && content !== null;
  return (
    <section aria-label="产物内容" style={box}>
      <div style={heading}>产物：{text(artifact.path)}</div>
      <div style={muted}>
        {`hash：${text(artifact.content_hash)} · 大小：${formatBytes(artifact.size_bytes)}`}
      </div>
      {readable ? (
        <>
          <div style={muted}>
            （模型生成，未核实）{artifact.truncated === true ? " · 内容过长，已截断" : ""}
          </div>
          <pre
            style={{
              margin: `${tokens.space.sm}px 0 0`,
              maxHeight: 360,
              overflow: "auto",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              fontSize: tokens.text.xs.size,
            }}
          >
            {content}
          </pre>
        </>
      ) : (
        <div style={{ ...muted, marginTop: tokens.space.sm }}>
          {encoding === "binary" ? "二进制内容，未显示" : "没有返回内容"}
        </div>
      )}
    </section>
  );
};

/** P3.2 P32-15：一个动作在世界上到底发生了什么。已发布时显示实际路径与回读的内容
 * hash；UNKNOWN 说成“核对中”，因为系统自己也还不知道，绝不写成失败或成功。 */
function ActionOutcome({ action }: { action: Record<string, unknown> }): React.JSX.Element | null {
  const state = text(action.state);
  const path = text(action.published_path);
  const hash = text(action.published_hash);
  if (!state) return null;
  const label =
    state === "SUCCEEDED" ? "已发布" : state === "UNKNOWN" ? "核对中" : "已生成，未发布";
  return (
    <div style={muted} data-testid="action-outcome" data-action-state={state}>
      {label}
      {path ? ` · ${path}` : ""}
      {hash ? ` · 内容 ${shortHash(hash)}` : ""}
    </div>
  );
}

/** Task 已经结束的状态：这些 Task 不再提供接管。 */
const TASK_ENDED = new Set(["DONE", "COMPLETED", "FAILED", "CANCELLED", "SKIPPED"]);

/**
 * 代码评审第 1 轮 P1-5⑤：卡住的 Task 不一定被判为 blocked（例如一直在跑、在排队），
 * 人也要能接管。依据必填，与「回合结果未知」卡片用同一套命令与可访问名称。
 */
const TakeoverBox: React.FC<{ taskId: string; send: (type: string, payload?: Json) => unknown }> = ({ taskId, send }) => {
  const [open, setOpen] = useState(false);
  const [basis, setBasis] = useState("");
  if (!open) {
    return (
      <button type="button" style={{ ...button, marginLeft: tokens.space.sm }} onClick={() => setOpen(true)}>
        接管这个 Task
      </button>
    );
  }
  return (
    <div style={{ ...box, marginTop: tokens.space.xs }}>
      <textarea aria-label="接管依据" style={field} value={basis} onChange={(e) => setBasis(e.target.value)} />
      <div style={{ display: "flex", gap: tokens.space.sm }}>
        <button type="button" style={button} disabled={!basis.trim()} onClick={() => send("mission_takeover", { task_id: taskId, action: "retry_with_note", basis })}>接管：重试</button>
        <button type="button" style={button} disabled={!basis.trim()} onClick={() => send("mission_takeover", { task_id: taskId, action: "stop", basis })}>接管：停止</button>
      </div>
    </div>
  );
};

export const MissionsView: React.FC<MissionsViewProps> = ({ channel }) => {
  const store = useMissionsStore();
  const [creating, setCreating] = useState(false);
  const [goal, setGoal] = useState("");
  const [criteria, setCriteria] = useState("");
  const [domain, setDomain] = useState("code");
  const [searchPolicy, setSearchPolicy] = useState("");
  const [sources, setSources] = useState<SourceDraft[]>([]);
  const [sourceImporting, setSourceImporting] = useState(false);
  const [createPending, setCreatePending] = useState(false);
  const createRequest = useRef<string | null>(null);
  const createRetry = useRef<{ fingerprint: string; key: string } | null>(null);
  const createTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [maxTokens, setMaxTokens] = useState("");
  const [maxAttempts, setMaxAttempts] = useState("");
  const [conflictReserve, setConflictReserve] = useState("");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [bases, setBases] = useState<Record<string, string>>({});
  const [comment, setComment] = useState("");
  const [artifact, setArtifact] = useState<Json | null>(null);
  const artifactRequest = useRef<{ requestId: string; missionId: string | null; artifactId: string } | null>(null);
  const selectedRef = useRef<string | null>(null);
  const flights = useRef<Record<string, Flight>>({});
  /** request_id → mission_id（mission_get / mission_events 的应答按它归属）。 */
  const requests = useRef(new Map<string, string>());
  useEffect(() => {
    selectedRef.current = store.selectedId;
  }, [store.selectedId]);

  const send = useCallback(
    (type: string, payload: Json = {}): string => {
      // the envelope id stays at the top level; payload fields keep their own names
      const requestId = newKey();
      if (type === "mission_list") useMissionsStore.getState().setListRequest(requestId);
      const message: ControlMessage = { type, request_id: requestId, payload };
      channel?.send(message);
      return requestId;
    },
    [channel],
  );

  const flightOf = useCallback((missionId: string): Flight => {
    let flight = flights.current[missionId];
    if (!flight) {
      flight = { get: false, getAgain: false, events: false, pages: 0, gap: false };
      flights.current[missionId] = flight;
    }
    return flight;
  }, []);

  /** 刷新快照；在途时只记一笔，等这次回来再补一次。 */
  const fetchDetail = useCallback(
    (missionId: string) => {
      const flight = flightOf(missionId);
      if (flight.get) {
        flight.getAgain = true;
        return;
      }
      flight.get = true;
      requests.current.set(send("mission_get", { mission_id: missionId }), missionId);
    },
    [flightOf, send],
  );

  /** 从事件游标往后拉一页；fresh 开始新一轮（重新计页）。在途时只记缺口。 */
  const fetchEvents = useCallback(
    (missionId: string, fresh: boolean) => {
      const flight = flightOf(missionId);
      if (flight.events) {
        flight.gap = true;
        return;
      }
      if (fresh) flight.pages = 0;
      flight.events = true;
      flight.gap = false;
      flight.pages += 1;
      const state = useMissionsStore.getState();
      const after = state.eventCursor[missionId] ?? 0;
      requests.current.set(
        send("mission_events", { mission_id: missionId, after_seq: after, limit: EVENT_PAGE_LIMIT }),
        missionId,
      );
      state.setEventsLoading(missionId, true);
    },
    [flightOf, send],
  );

  const refreshSelected = useCallback(
    (missionId: string) => {
      fetchDetail(missionId);
      fetchEvents(missionId, true);
    },
    [fetchDetail, fetchEvents],
  );

  useEffect(() => {
    if (!channel) return undefined;
    // a new subscription: whatever was in flight on the old one will never answer
    flights.current = {};
    requests.current.clear();
    artifactRequest.current = null;
    setArtifact(null);
    if (createRequest.current) useMissionsStore.getState().setError("连接已变化，创建结果未知；可以使用原请求键重试");
    createRequest.current = null;
    setCreatePending(false);
    useMissionsStore.getState().clearEventsLoading();

    const off = channel.onMessage((incoming: IncomingMessage) => {
      const message = incoming as unknown as { type?: unknown; payload?: unknown };
      const type = text(message.type);
      const payload = record(message.payload);
      const state = useMissionsStore.getState();

      if (type === "mission_changed") {
        // the resident feed applies the change to the list; here only the open detail
        const missionId = text(payload.mission_id);
        if (!missionId || missionId !== selectedRef.current) return;
        fetchDetail(missionId);
        const cursor = state.eventCursor[missionId] ?? 0;
        if ((Number(payload.last_seq) || 0) > cursor) fetchEvents(missionId, true);
        return;
      }
      if (!OWN_RESPONSES.has(type)) return;

      const ok = payload.ok === true;
      const data = record(payload.data);
      const requestId = text(payload.request_id);
      if ((type === "mission_create_response" || type === "mission_create_with_sources_response") && requestId !== createRequest.current) return;
      const tracked = requests.current.get(requestId);
      if ((type === "mission_get_response" || type === "mission_events_response") && tracked === undefined) return;
      if (type === "mission_artifact_read_response") {
        const pending = artifactRequest.current;
        if (!pending || pending.requestId !== requestId || pending.missionId !== selectedRef.current) return;
        if (ok && data.artifact_id != null && data.artifact_id !== pending.artifactId) return;
        artifactRequest.current = null;
      }
      if (tracked !== undefined) requests.current.delete(requestId);
      if (payload.ok === false && (tracked === undefined || tracked === selectedRef.current)) {
        const code = text(payload.error_code);
        state.setError(ERROR_TEXT[code] ?? (text(payload.error) || code || "请求失败"));
      }

      switch (type) {
        case "mission_get_response": {
          const shown = text(record(data.mission).id);
          const missionId = tracked ?? (shown || selectedRef.current || "");
          const flight = flightOf(missionId);
          flight.get = false;
          // a late snapshot of a Mission no longer selected never replaces the open one
          if (ok && shown === missionId && missionId === selectedRef.current) state.setDetail(data);
          if (flight.getAgain) {
            flight.getAgain = false;
            if (missionId === selectedRef.current) fetchDetail(missionId);
          }
          break;
        }
        case "mission_events_response": {
          const missionId = tracked ?? (text(data.mission_id) || selectedRef.current || "");
          const flight = flightOf(missionId);
          flight.events = false;
          const selected = missionId === selectedRef.current;
          const hasMore = ok && data.has_more === true;
          if (ok) {
            state.appendEvents(
              missionId,
              list(data.events) as unknown as MissionEvent[],
              hasMore,
              typeof data.through_seq === "number" ? data.through_seq : undefined,
            );
          }
          if (selected && hasMore && flight.pages < MAX_AUTO_PAGES) {
            fetchEvents(missionId, false);
          } else if (selected && flight.gap) {
            fetchEvents(missionId, true);
          } else {
            flight.gap = false;
            state.setEventsLoading(missionId, false);
            // P3.1 §3.4: events went past the snapshot's cursor → take a fresh snapshot
            const snapshot = useMissionsStore.getState().detail;
            const through = snapshot?.through_seq;
            const cursor = useMissionsStore.getState().eventCursor[missionId] ?? 0;
            if (ok && selected && typeof through === "number" && through < cursor) fetchDetail(missionId);
          }
          break;
        }
        case "orchestration_policy_status_response":
          if (ok) state.setPolicy(data);
          break;
        case "mission_create_response":
        case "mission_create_with_sources_response":
          createRequest.current = null;
          setCreatePending(false);
          if (createTimer.current) clearTimeout(createTimer.current);
          if (ok) {
            setCreating(false);
            setGoal("");
            setCriteria("");
            setConflictReserve("");
            setSources([]);
            setDomain("code");
            createRetry.current = null;
            state.setError(null);
            send("mission_list");
            const missionId = text(data.mission_id);
            if (missionId) {
              state.select(missionId);
              selectedRef.current = missionId;
              refreshSelected(missionId);
            }
          }
          break;
        case "mission_comment_response":
          if (ok) setComment("");
          if (ok && selectedRef.current) refreshSelected(selectedRef.current);
          break;
        case "mission_cancel_response":
        case "mission_approval_decide_response":
        case "mission_takeover_response":
          if (ok && selectedRef.current) refreshSelected(selectedRef.current);
          break;
        case "mission_artifact_read_response":
          setArtifact(ok ? data : null);
          break;
        default:
          break;
      }
    });
    send("orchestration_policy_status");
    // coming back to the view (or reconnecting) with a Mission open: refresh it
    const open = useMissionsStore.getState().selectedId;
    if (open) {
      selectedRef.current = open;
      refreshSelected(open);
    }
    const offState = channel.onStateChange?.((connection) => {
      flights.current = {}; requests.current.clear(); artifactRequest.current = null; setArtifact(null);
      useMissionsStore.getState().clearEventsLoading();
      if (createRequest.current) useMissionsStore.getState().setError("连接已变化，创建结果未知；可以使用原请求键重试");
      createRequest.current = null; setCreatePending(false);
      if (createTimer.current) clearTimeout(createTimer.current);
      if (connection === "connected") {
        send("orchestration_policy_status");
        if (selectedRef.current) refreshSelected(selectedRef.current);
      }
    });
    return () => { off(); offState?.(); if (createTimer.current) clearTimeout(createTimer.current); };
  }, [channel, send, flightOf, fetchDetail, fetchEvents, refreshSelected]);

  const status = store.status;
  const domains = record(record(record(status?.deployment_manifest).features).domains);
  const canCreateDocument = domains.atomic_source_create === true && list(domains.items).some((item) => item.id === "doc-research-v1");
  const searchPolicies = list(store.policy?.eligible_search_policies);
  const reserveTokens = Number(conflictReserve);
  const totalTokens = maxTokens.trim() ? Number(maxTokens) : status?.mission_budget_defaults?.max_tokens;
  const validReserve = !conflictReserve.trim() || (Number.isSafeInteger(reserveTokens) && reserveTokens >= 0 &&
    (totalTokens == null || reserveTokens <= totalTokens));
  const submittable = validReserve && !createPending && !sourceImporting && !!channel && goal.trim().length > 0 && criteria.split("\n").some((line) => line.trim().length > 0) &&
    (!searchPolicy || searchPolicies.some((policy) => policy.version_id === searchPolicy)) &&
    (domain === "code" || (canCreateDocument && sources.length > 0 && sources.every((source) => source.path.trim().length > 0 && source.path !== "sources/" && source.content.length > 0)));

  const submit = () => {
    if (!submittable) return;
    const budget: Json = {};
    if (Number(maxTokens) > 0) budget.max_tokens = Math.floor(Number(maxTokens));
    if (Number(maxAttempts) > 0) budget.max_attempts = Math.floor(Number(maxAttempts));
    const spec = {
      goal: goal.trim(),
      success_criteria: criteria.split("\n").map((line) => line.trim()).filter(Boolean),
      ...(Object.keys(budget).length ? { budget } : {}),
      ...(conflictReserve.trim() ? { conflict_reserve_tokens: reserveTokens } : {}),
      ...(searchPolicy ? { search_policy_version_id: searchPolicy } : {}),
    };
    const fingerprint = JSON.stringify([domain, spec, domain === "code" ? [] : sources]);
    if (createRetry.current?.fingerprint !== fingerprint) createRetry.current = { fingerprint, key: newKey() };
    const missionSpec = { ...spec, idempotency_key: createRetry.current.key };
    const requestId = newKey();
    createRequest.current = requestId;
    setCreatePending(true);
    store.setError(null);
    const accepted = channel?.send({
      type: domain === "code" ? "mission_create" : "mission_create_with_sources", request_id: requestId,
      payload: domain === "code" ? missionSpec : { mission: { ...missionSpec, domain }, sources },
    });
    if (!accepted) { createRequest.current = null; setCreatePending(false); store.setError("连接不可用，创建请求未发送"); return; }
    createTimer.current = setTimeout(() => {
      if (createRequest.current === requestId) { createRequest.current = null; setCreatePending(false); useMissionsStore.getState().setError("创建超时，结果未知；可以使用原请求键重试"); }
    }, 30000);
  };

  const open = (missionId: string) => {
    setCreating(false);
    setArtifact(null);
    artifactRequest.current = null;
    setComment("");
    store.select(missionId);
    selectedRef.current = missionId;
    refreshSelected(missionId);
  };

  const detail = store.detail;
  const mission = record(detail?.mission);
  const selectedId = store.selectedId;
  const events = selectedId ? store.events[selectedId] ?? [] : [];
  const recentEvents = events.slice(-TIMELINE_SIZE);
  const showMore = selectedId ? store.eventsHasMore[selectedId] === true && store.eventsLoading[selectedId] !== true : false;
  const pendingApprovals = useMemo(
    () => list(detail?.approvals).filter((approval) => text(approval.state) === "PENDING"),
    [detail],
  );
  const waiting: unknown[] = Array.isArray(detail?.waiting_on) ? (detail.waiting_on as unknown[]) : [];
  const artifacts = list(detail?.artifacts);
  const drift = list(store.policy?.drift);

  if (status && !status.available) {
    return (
      <section data-testid="view-missions" aria-label="任务编排" style={{ flex: 1, padding: tokens.space.lg, color: dark.text }}>
        <div role="alert" style={box}>
          编排服务不可用：{status.reason ?? status.state}
        </div>
      </section>
    );
  }

  return (
    <section
      data-testid="view-missions"
      aria-label="任务编排"
      style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex", color: dark.text, fontFamily: tokens.font.ui }}
    >
      <aside
        style={{
          width: 280,
          flexShrink: 0,
          borderRight: `1px solid ${tokens.color.surface.hairline}`,
          padding: tokens.space.md,
          display: "flex",
          flexDirection: "column",
          gap: tokens.space.sm,
          overflowY: "auto",
        }}
      >
        {status?.test_scenario ? (
          <div role="status" style={{ ...box, borderColor: tokens.color.warning.bg }}>测试场景：{status.test_scenario}</div>
        ) : null}
        <button type="button" style={button} onClick={() => { setCreating(true); store.select(null); selectedRef.current = null; setArtifact(null); }}>
          新建 Mission
        </button>
        {store.missions.length === 0 ? (
          <div style={{ color: dark.textMuted }}>还没有 Mission。点「新建 Mission」开始。</div>
        ) : (
          store.missions.map((row) => (
            <button
              key={row.id}
              type="button"
              data-testid={`mission-row-${row.id}`}
              data-status={row.status}
              data-ui-state={row.ui_state || undefined}
              aria-current={selectedId === row.id ? "true" : undefined}
              onClick={() => open(row.id)}
              style={{ ...button, height: "auto", padding: tokens.space.sm, textAlign: "left", display: "flex", flexDirection: "column", gap: 2 }}
            >
              <span>{row.goal}</span>
              <span style={muted}>
                {stateLabel(row.ui_state, row.status)}
                {row.pending_approvals ? ` · 待审批：${row.pending_approvals}` : ""}
                {row.blocked && row.ui_state !== "unknown" ? " · 结果未知" : ""}
              </span>
            </button>
          ))
        )}
        <div style={{ ...box, marginTop: "auto" }} aria-label="策略状态">
          <div style={heading}>策略（只读）</div>
          <div style={muted}>当前版本：{text(store.policy?.active_version_id) || "—"}</div>
          {drift.map((item) => (
            <div
              key={text(item.name)}
              role="status"
              style={{ ...box, borderColor: tokens.color.warning.bg, padding: tokens.space.sm, marginTop: tokens.space.sm, fontSize: tokens.text.xs.size }}
            >
              {`配置与生效策略不一致：${text(item.name)} 配置 ${show(item.config)}，生效 ${show(item.active)}（修改要经策略晋级才生效）`}
            </div>
          ))}
        </div>
      </aside>

      <div style={{ flex: 1, minWidth: 0, overflowY: "auto", padding: tokens.space.lg, display: "flex", flexDirection: "column", gap: tokens.space.md }}>
        {store.error ? (
          <div role="alert" style={{ ...box, borderColor: tokens.color.danger.bg }}>{store.error}</div>
        ) : null}

        {creating ? (
          <div style={{ display: "flex", flexDirection: "column", gap: tokens.space.sm }}>
            <label>Mission 领域<select aria-label="Mission 领域" style={{ ...field, minHeight: 36 }} disabled={createPending} value={domain} onChange={(e) => setDomain(e.target.value)}>
              <option value="code">代码</option>{canCreateDocument && <option value="doc-research-v1">文档研究</option>}
            </select></label>
            <label htmlFor="mission-goal">Mission 目标</label>
            <textarea id="mission-goal" aria-label="Mission 目标" style={field} disabled={createPending} value={goal} onChange={(e) => setGoal(e.target.value)} />
            <label htmlFor="mission-criteria">成功条件（每行一条）</label>
            <textarea id="mission-criteria" aria-label="成功条件" style={field} disabled={createPending} value={criteria} onChange={(e) => setCriteria(e.target.value)} />
            {searchPolicies.length > 0 && <label style={muted}>执行方式
              <select aria-label="执行方式" style={field} value={searchPolicy} disabled={createPending} onChange={(e) => setSearchPolicy(e.target.value)}>
                <option value="">首个通过即交付</option>
                {searchPolicies.map((policy) => <option key={text(policy.version_id)} value={text(policy.version_id)}>
                  比较最多 {text(record(policy.policy).max_candidates)} 个候选后综合（已批准）
                </option>)}
              </select>
            </label>}
            {domain === "doc-research-v1" && <SourceDrafts sources={sources} onChange={setSources} disabled={createPending} onBusy={setSourceImporting} />}
            <div style={{ display: "flex", gap: tokens.space.sm }}>
              <input aria-label="Token 上限" placeholder={status?.mission_budget_defaults ? `Token 上限（留空=${status.mission_budget_defaults.max_tokens}）` : "Token 上限（可选）"} style={{ ...field, minHeight: 0, height: tokens.controlHeight }} value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} />
              <input aria-label="尝试次数上限" placeholder={status?.mission_budget_defaults ? `尝试次数上限（留空=${status.mission_budget_defaults.max_attempts}）` : "尝试次数上限（可选）"} style={{ ...field, minHeight: 0, height: tokens.controlHeight }} value={maxAttempts} onChange={(e) => setMaxAttempts(e.target.value)} />
            </div>
            <input aria-label="冲突核对预留 Token" aria-describedby="conflict-reserve-help" inputMode="numeric" placeholder="冲突核对预留 Token（可选）" style={{ ...field, minHeight: 0, height: tokens.controlHeight }} value={conflictReserve} onChange={(e) => setConflictReserve(e.target.value)} />
            <div id="conflict-reserve-help" style={muted}>从总预算中预留，出现冲突时用于独立核对；留空不预留。</div>
            {!validReserve && <div role="alert" style={{ color: dark.danger }}>冲突核对预留必须是非负整数，且不超过总 Token 上限。</div>}
            <button type="button" style={button} disabled={!submittable} onClick={submit}>
              提交 Mission
            </button>
            {createPending && <div role="status">正在创建 Mission…</div>}
          </div>
        ) : null}

        {!creating && selectedId && detail ? (
          <>
            <div style={box}>
              <div style={{ fontSize: tokens.text.lg.size, fontWeight: tokens.weight.semibold }}>{text(mission.goal)}</div>
              <div
                data-testid="mission-state"
                data-status={text(mission.status)}
                data-ui-state={text(mission.ui_state) || undefined}
                style={{ color: dark.textMuted }}
              >
                {`状态：${stateLabel(mission.ui_state, mission.status)}`}
                {mission.stop_reason ? ` · 停止原因：${text(mission.stop_reason)}` : ""}
                {` · 策略版本：${text(record(detail.mission_policy).version_id) || "—"}`}
              </div>
              <div style={muted}>
                Token 已结算 {text(record(detail.usage).settled_tokens) || "未知"} · 当前预留 {text(record(detail.usage).reserved_tokens) || "未知"} · 金额 未计价
              </div>
              <div style={muted} data-testid="mission-budget">
                {`预算：Token 上限 ${text(record(mission.budget).max_tokens) || "—"} · 尝试次数上限 ${text(record(mission.budget).max_attempts) || "—"}`}
              </div>
              <MissionSearch value={detail.search} />
              {waiting.length ? (
                <div aria-label="等待原因" style={{ marginTop: tokens.space.sm }}>
                  {waiting.map((item, index) => (
                    <div key={`${index}-${text(record(item).request_id)}`}>{waitLine(item)}</div>
                  ))}
                </div>
              ) : null}
              <button
                type="button"
                style={{ ...button, marginTop: tokens.space.sm }}
                disabled={TERMINAL.has(text(mission.status))}
                onClick={() => send("mission_cancel", { mission_id: selectedId })}
              >
                取消 Mission
              </button>
            </div>

            {list(detail.blocked).map((item) => {
              const taskId = text(item.task_id);
              const basis = bases[taskId] ?? "";
              return (
                <div key={taskId} role="alert" style={{ ...box, borderColor: tokens.color.warning.bg }}>
                  回合结果未知（进程在模型调用中途退出）：{taskId}
                  <textarea aria-label="接管依据" style={field} value={basis} onChange={(e) => setBases({ ...bases, [taskId]: e.target.value })} />
                  <div style={{ display: "flex", gap: tokens.space.sm }}>
                    <button type="button" style={button} disabled={!basis.trim()} onClick={() => send("mission_takeover", { task_id: taskId, action: "retry_with_note", basis })}>接管：重试</button>
                    <button type="button" style={button} disabled={!basis.trim()} onClick={() => send("mission_takeover", { task_id: taskId, action: "stop", basis })}>接管：停止</button>
                  </div>
                </div>
              );
            })}

            {pendingApprovals.map((approval) => {
              const requestId = text(approval.request_id);
              const reason = reasons[requestId] ?? "";
              const kind = text(approval.kind);
              const action = record(approval.action);
              return (
                <div key={requestId} style={{ ...box, borderColor: tokens.color.accent.border }} data-testid={`approval-${requestId}`}>
                  <div style={heading}>
                    {WAIT_LABEL[kind] ?? `未知审批类型（${kind || "未提供"}）`}：<ModelText value={approval.summary} />
                  </div>
                  {action.reason ? <div>理由：<ModelText value={action.reason} /></div> : null}
                  {kind === "action" ? <ActionOutcome action={action} /> : null}
                  {kind === "source_change" ? <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(record(approval.source_change), null, 2)}</pre> : null}
                  {kind === "arbitration" && approval.arbitration != null ? <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(approval.arbitration, null, 2)}</pre> : null}
                  {kind === "action" || kind === "source_change" ? (
                    <>
                      <textarea aria-label="拒绝理由" style={field} value={reason} onChange={(e) => setReasons({ ...reasons, [requestId]: e.target.value })} />
                      <div style={{ display: "flex", gap: tokens.space.sm }}>
                        <button type="button" style={button} onClick={() => send("mission_approval_decide", { approval_id: requestId, decision: "approve" })}>批准</button>
                        <button type="button" style={button} disabled={!reason.trim()} onClick={() => send("mission_approval_decide", { approval_id: requestId, decision: "reject", reason })}>拒绝</button>
                      </div>
                    </>
                  ) : kind === "review" ? (
                    <div style={{ display: "flex", gap: tokens.space.sm }}>
                      <button type="button" style={button} onClick={() => send("mission_approval_decide", { approval_id: requestId, decision: "review_pass" })}>复核通过</button>
                      <button type="button" style={button} onClick={() => send("mission_approval_decide", { approval_id: requestId, decision: "review_fail" })}>复核不通过</button>
                    </div>
                  ) : kind === "arbitration" ? (
                    <>
                      <textarea aria-label="仲裁依据" style={field} value={reason} onChange={(e) => setReasons({ ...reasons, [requestId]: e.target.value })} />
                      <div style={{ display: "flex", gap: tokens.space.sm, flexWrap: "wrap" }}>
                        {(Array.isArray(approval.options) ? approval.options : []).filter((option) => typeof option === "string" &&
                          (approval.topic === "judgment" ? option === "met" || option === "unmet" :
                            /^keep:.+/.test(option) || option === "contextual" || option === "unresolved")).map((option) => (
                          <button key={text(option)} type="button" style={button} disabled={!reason.trim()} onClick={() => send("mission_approval_decide", { approval_id: requestId, decision: "arbitrate", ruling: text(option), basis: reason })}>
                            裁决：{text(option)}
                          </button>
                        ))}
                      </div>
                    </>
                  ) : null}
                  {list(approval.comments).map((item, index) => (
                    <div key={index} style={{ ...muted, marginTop: tokens.space.xs }}>
                      {`评论（${text(item.principal_id) || "未知"}）：${text(item.text)}`}
                    </div>
                  ))}
                </div>
              );
            })}

            {detail.document != null && <MissionDocument key={selectedId} missionId={selectedId} document={record(detail.document)} channel={channel} onChanged={() => refreshSelected(selectedId)} />}

            <div style={box}>
              <div style={heading}>Task 与验证</div>
              {list(detail.tasks).map((task) => {
                const taskId = text(task.id);
                const blockedIds = new Set(list(detail.blocked).map((item) => text(item.task_id)));
                const canTakeOver = !TERMINAL.has(text(mission.status)) && !TASK_ENDED.has(text(task.status)) && !blockedIds.has(taskId);
                return (
                  <div key={taskId} data-testid={`task-${taskId}`} style={{ marginTop: tokens.space.sm }}>
                    <ModelText value={task.goal} /> · {text(task.status)}
                    {canTakeOver ? <TakeoverBox taskId={taskId} send={send} /> : null}
                  </div>
                );
              })}
              {list(detail.results).map((result) => {
                const attemptId = text(result.attempt_id);
                const layers = list(result.verification_layers);
                const verdict = text(result.verdict);
                const verificationState = text(result.verification_state);
                const failed = verificationState === "DONE" && verdict === "FAIL";
                const rejection = failed ? record(result.final_rejection) : {};
                const reason = text(rejection.reason);
                return (
                  <div key={text(result.result_id) || attemptId} style={{ marginTop: tokens.space.sm }}>
                    {(verdict || verificationState) && <div
                      data-testid={`result-final-${text(result.result_id) || attemptId}`}
                      data-verdict={verdict}
                      data-verification-state={verificationState}
                      style={{ color: failed ? tokens.color.danger.fg : dark.textMuted }}
                    >
                      <strong>{failed ? "结果最终未接受" : "结果判定"}（{verdict || "尚未判定"} / {verificationState || "未知"}）</strong>
                      {reason && <div>拒绝原因：{REJECTION_LABEL[reason] ?? reason}（{reason}）</div>}
                      {list(rejection.source_issues).map((issue, index) => <div key={index} style={{ overflowWrap: "anywhere" }}>
                        {text(issue.path) || "来源"} · {REJECTION_LABEL[text(issue.reason)] ?? text(issue.reason)}（{text(issue.reason) || text(issue.code)}）
                        {issue.version ? ` · 版本：${text(issue.version)}` : ""}
                      </div>)}
                    </div>}
                    <div>{detail.document != null ? "分析 / 非结论（正文非结论陈述不做覆盖核对）" : "结果摘要"}：<ModelText value={result.summary} /></div>
                    <div style={{ display: "flex", gap: tokens.space.xs, flexWrap: "wrap" }}>
                      {layers.map((layer) => {
                        const layerStatus = text(layer.status);
                        const color = layerStatus === "PASS" ? tokens.color.success.fg : layerStatus === "FAIL" || layerStatus === "ERROR" ? tokens.color.danger.fg : dark.textMuted;
                        return (
                          <span
                            key={text(layer.layer)}
                            data-testid={`layer-${attemptId}-${text(layer.layer)}`}
                            data-status={layerStatus}
                            style={{ ...box, padding: `2px ${tokens.space.sm}px`, color }}
                          >
                            {text(layer.layer)}：{LAYER_LABEL[layerStatus] ?? layerStatus}
                          </span>
                        );
                      })}
                    </div>
                    {layers
                      .filter((layer) => plain(layer.summary).length > 0)
                      .map((layer) => (
                        <div
                          key={text(layer.layer)}
                          data-testid={`layer-summary-${attemptId}-${text(layer.layer)}`}
                          style={{ ...muted, marginTop: tokens.space.xs }}
                        >
                          {text(layer.layer)}：<LayerSummary value={layer.summary} />
                        </div>
                      ))}
                  </div>
                );
              })}
            </div>

            {artifacts.length ? (
              <div style={box}>
                <div style={heading}>产物</div>
                {artifacts.map((item) => {
                  const artifactId = text(item.id);
                  const verification = text(item.verification_status);
                  return (
                    <div
                      key={artifactId}
                      data-testid={`artifact-${artifactId}`}
                      style={{ display: "flex", alignItems: "center", gap: tokens.space.sm, marginTop: tokens.space.sm }}
                    >
                      <span style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere" }}>
                        {`${text(item.path)} · ${formatBytes(item.size_bytes)} · 验证：${LAYER_LABEL[verification] ?? (verification || "—")} · ${shortHash(item.content_hash)}`}
                      </span>
                      <button type="button" style={button} onClick={() => {
                        setArtifact(null);
                        const requestId = newKey();
                        artifactRequest.current = { requestId, missionId: selectedId, artifactId };
                        channel?.send({ type: "mission_artifact_read", request_id: requestId, payload: { artifact_id: artifactId } });
                      }}>
                        查看产物
                      </button>
                    </div>
                  );
                })}
              </div>
            ) : null}

            {artifact ? <div>{detail.document != null && <div>分析 / 非结论（正文非结论陈述不做覆盖核对）</div>}<ArtifactPanel artifact={artifact} /></div> : null}

            <div style={box}>
              <div style={heading}>评论</div>
              <textarea aria-label="评论" style={{ ...field, marginTop: tokens.space.sm }} value={comment} onChange={(e) => setComment(e.target.value)} />
              <button
                type="button"
                style={{ ...button, marginTop: tokens.space.sm }}
                disabled={!comment.trim()}
                onClick={() => send("mission_comment", { target_id: selectedId, text: comment.trim() })}
              >
                发表评论
              </button>
            </div>

            <div style={box}>
              <div style={heading}>事件</div>
              {events.length > TIMELINE_SIZE ? (
                <div style={muted}>{`共 ${events.length} 条，显示最近 ${TIMELINE_SIZE} 条`}</div>
              ) : null}
              {recentEvents.map((event) => (
                <div key={event.seq} data-testid={`event-${event.seq}`} style={muted}>
                  {eventLine(event)}
                </div>
              ))}
              {showMore ? (
                <button
                  type="button"
                  style={{ ...button, marginTop: tokens.space.sm }}
                  onClick={() => fetchEvents(selectedId, true)}
                >
                  加载更多事件
                </button>
              ) : null}
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
};

export default MissionsView;
