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
import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";

import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import { MissionSources, SourceDrafts, type SourceDraft } from "./MissionSources";
import { PlanningQuestions } from "./PlanningQuestions";
import { PlanningAuthorization } from "./PlanningAuthorization";
import { OperationWorkspace } from "./OperationWorkspace";
import { MissionDiagnostics } from "./MissionDiagnostics";
import { MissionStory } from "./missionStory/MissionStory";
import { MissionProgress } from "./liveGraph/MissionProgress";
import { MissionAssurance } from "./MissionAssurance";
import { PublishCriterionHelper } from "./PublishCriterionHelper";
import { ActionApprovalSummary } from "./ActionApprovalSummary";
import { useConfirm } from "../components/useConfirm";
import {
  asList as list,
  asRecord as record,
  asText as text,
  backgroundTrouble,
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
  waiting_person: "等你处理",
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
  // 产物的验证状态（2026-09-26 真机：列表里直接显示英文 VERIFIED）
  VERIFIED: "已验证",
  UNVERIFIED: "未验证",
  REJECTED: "未通过",
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
  "mission_action_resolve_response",
  "mission_comment_response",
  "mission_artifact_read_response",
]);
const EVENT_PAGE_LIMIT = 200;
// 严格引用开关打开时追加的成功条件（2026-10-02 用户决定：交给审阅员按这条判断，程序不再逐条核对）。
export const STRICT_QUOTES_CRITERION =
  "每个结论都逐字引用所附资料里的原文，并注明出自哪份资料、哪一处；资料里找不到依据的内容不写成结论，而是明确说明资料未提及";
/** 结构图（elkjs + React Flow）按需加载：默认看「任务过程」，不点结构图就不下载这两个库。 */
const LiveGraph = lazy(() => import("./liveGraph/LiveGraph").then((m) => ({ default: m.LiveGraph })));
/** 只说明"后台还活着"、不改变详情内容的事件：推送只带这些时不重拉详情（2026-09-27 性能：
 *  运行中每几秒一次心跳，每次都重拉整份详情并重画整页）。等人操作的变化由 5 秒兜底刷新接住。 */
const NOISE_EVENTS = new Set(["HeartbeatReceived", "AssuranceEvidenceChanged", "AssuranceUseValidityChecked"]);
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

/** 推送里只带原始 status 时的中文（2026-09-25 真机点击：列表曾直接显示 PLANNING）。 */
const STATUS_LABEL: Record<string, string> = {
  CREATED: "请求已接收",
  PLANNING: "规划中",
  ACTIVE: "运行",
  VERIFYING: "待验证",
  COMPLETED: "正式交付",
  FAILED: "失败",
  CANCELLED: "已取消",
  STOPPED: "已停止",
};

/** 停止原因与 Task 状态的中文（2026-09-25 真机点击：界面直接显示 verification_passed / BLOCKED）。 */
const STOP_REASON_LABEL: Record<string, string> = {
  verification_passed: "验证通过",
  verification_failed: "验证未通过",
  budget_exhausted: "预算用完",
  cancelled: "已取消",
  user_cancelled: "已取消",
  planning_failed: "规划失败",
  max_attempts: "尝试次数用完",
  timeout: "超时",
  human_override: "人工接管后停止",
  no_dispatchable_work: "没有可继续执行的工作",
  store_fault: "任务数据读写反复出错，已停止",
};
const TASK_STATUS_LABEL: Record<string, string> = {
  PENDING: "等待", READY: "就绪", ACTIVE: "执行中", RUNNING: "执行中", BLOCKED: "等待前置步骤",
  VERIFYING: "检查中", DONE: "完成", COMPLETED: "完成", FAILED: "失败", CANCELLED: "已取消", SKIPPED: "跳过",
};

function stateLabel(uiState: unknown, status: unknown): string {
  const ui = text(uiState);
  const raw = text(status);
  return ui ? UI_STATE_LABEL[ui] ?? ui : STATUS_LABEL[raw] ?? raw;
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

/** 顶部"下一步"提示（2026-09-25 真机点击：要人操作的按钮埋在长页面中间，用户找不到）。
 *  只读投影：从详情推出当前最该做的一件事，按钮跳到对应区域；不代替那一步本身。 */
function nextStep(detail: Json, approvals: number): { text: string; target?: string; action?: string } {
  const mission = record(detail.mission);
  const status = text(mission.status);
  const ws = record(detail.operation_workspace);
  // 已结束的任务：留下的提问、授权请求都不再需要人处理（2026-09-29 取消后仍提示"去回答"）。
  const ended = ["COMPLETED", "FAILED", "CANCELLED"].includes(status);
  if (!ended && text(ws.mission_id) && text(ws.state) && text(ws.state) !== "APPROVED" && ws.editable === true)
    return { text: "下一步：在「完成要求」里勾选要交付的内容，再点「确认上述完成要求」。", target: "mission-step-requirements", action: "去确认" };
  if (!ended && list(detail.planning_authorization_requests).length)
    return { text: "下一步：授权本轮规划，任务才会开始执行。" };
  if (!ended && list(detail.planning_questions).some((q) => text(record(q).state) === "PENDING"))
    return { text: "下一步：回答任务提出的问题。", target: "mission-step-questions", action: "去回答" };
  if (approvals > 0)
    return { text: `下一步：有 ${approvals} 项等你审批或复核。`, target: "mission-step-approvals", action: "去处理" };
  if (status === "COMPLETED") return { text: "任务已完成，结果在下方「产物」里。", target: "mission-step-artifacts", action: "查看产物" };
  if (status === "FAILED" && text(mission.stop_reason).toLowerCase() === "human_override")
    return { text: "任务已按你的接管操作停止。" };
  if (status === "FAILED") return { text: "任务失败了，原因见上方状态和下方「Task 与验证」。" };
  if (status === "CANCELLED") return { text: "任务已取消。" };
  return { text: "任务正在自动进行，需要你操作时这里会提示。" };
}

/** 完成要求还没确认时，提示条先让人去确认；授权仍留在原位置，不会同时出现两份。 */
function nextStepBlocksAuthorization(detail: Json): boolean {
  const ws = record(detail.operation_workspace);
  return !!(text(ws.mission_id) && text(ws.state) && text(ws.state) !== "APPROVED" && ws.editable === true);
}

function jumpTo(id: string): void {
  const target = typeof document === "undefined" ? null : document.getElementById(id);
  target?.scrollIntoView?.({ behavior: "smooth", block: "start" });
}

const ArtifactPanel: React.FC<{ artifact: Json; verified?: boolean }> = ({ artifact, verified = false }) => {
  const encoding = text(artifact.encoding);
  const content = typeof artifact.content === "string" ? artifact.content : null;
  const readable = encoding !== "binary" && content !== null;
  return (
    <section aria-label="产物内容" style={{ ...box, minWidth: 0 }}>
      <div style={{ ...heading, minWidth: 0, overflowWrap: "anywhere", wordBreak: "break-word" }}>产物：{text(artifact.path)}</div>
      <div style={{ ...muted, minWidth: 0, overflowWrap: "anywhere", wordBreak: "break-word" }}>
        {`hash：${text(artifact.content_hash)} · 大小：${formatBytes(artifact.size_bytes)}`}
      </div>
      {readable ? (
        <>
          <div style={muted}>
            {/* 2026-09-26 真机：列表写"已验证"，预览却一律写"未核实"。 */}
            {verified ? "（已通过核验）" : "（模型生成，未核实）"}{artifact.truncated === true ? " · 内容过长，已截断" : ""}
          </div>
          <pre
            style={{
              margin: `${tokens.space.sm}px 0 0`,
              maxHeight: 360,
              overflow: "auto",
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
              wordBreak: "break-word",
              minWidth: 0,
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
    <div
      style={{ ...muted, minWidth: 0, overflowWrap: "anywhere", wordBreak: "break-word" }}
      data-testid="action-outcome"
      data-action-state={state}
    >
      {label}
      {path ? ` · ${path}` : ""}
      {hash ? ` · 内容 ${shortHash(hash)}` : ""}
    </div>
  );
}

/** Task 已经结束的状态：这些 Task 不再提供接管。 */
const TASK_ENDED = new Set(["DONE", "COMPLETED", "FAILED", "CANCELLED", "SKIPPED"]);

/**
 * 阶段 B 裁决第 3 类：一次对外动作结果不明（或失败了却证明不了没生效），系统判不了，由人裁定。
 * 依据必填；"没生效"之后系统会按原内容重新出一张审批卡。只能由人亲手点。
 */
const ResolveOutcomeBox: React.FC<{ actionKey: string; failed: boolean; send: (type: string, payload?: Json) => unknown }> = ({ actionKey, failed, send }) => {
  const [basis, setBasis] = useState("");
  return (
    <div style={{ ...box, marginTop: tokens.space.xs }} data-testid={`resolve-${actionKey}`}>
      <div style={{ color: dark.textMuted }}>系统判断不了这次发布有没有生效，请你查看后裁定：</div>
      <textarea aria-label="裁定依据" placeholder="写一句你依据什么判断（例如：查了发布目录）" style={field}
        value={basis} onChange={(e) => setBasis(e.target.value)} />
      <div style={{ display: "flex", gap: tokens.space.sm }}>
        {failed ? null : (
          <button type="button" style={button} disabled={!basis.trim()}
            onClick={() => send("mission_action_resolve", { action_key: actionKey, outcome: "succeeded", basis })}>已生效</button>
        )}
        <button type="button" style={button} disabled={!basis.trim()}
          onClick={() => send("mission_action_resolve", { action_key: actionKey, outcome: "failed", basis })}>没生效</button>
      </div>
    </div>
  );
};

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
  // 2026-09-27 性能：按字段订阅。以前订阅整个 store，任何任务的每秒推送都让整页重画。
  const store = useMissionsStore(useShallow((s) => ({
    selectedId: s.selectedId, detail: s.detail, status: s.status, policy: s.policy, missions: s.missions, error: s.error,
    selectedEvents: s.selectedId ? s.events[s.selectedId] : undefined,
    selectedHasMore: s.selectedId ? s.eventsHasMore[s.selectedId] === true : false,
    selectedLoading: s.selectedId ? s.eventsLoading[s.selectedId] === true : false,
    select: s.select, setError: s.setError,
  })));
  const [graphTab, setGraphTab] = useState<"story" | "graph">("graph");
  const [creating, setCreating] = useState(false);
  const [goal, setGoal] = useState("");
  const [criteria, setCriteria] = useState("");
  const [strictQuotes, setStrictQuotes] = useState(false);
  const [sources, setSources] = useState<SourceDraft[]>([]);
  const [sourceImporting, setSourceImporting] = useState(false);
  const [createPending, setCreatePending] = useState(false);
  const createRequest = useRef<string | null>(null);
  const createRetry = useRef<{ fingerprint: string; key: string } | null>(null);
  const createTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [maxTokens, setMaxTokens] = useState("");
  const [maxAttempts, setMaxAttempts] = useState("");
  const [confirmDialog, ask] = useConfirm();
  const [allEventsShown, setAllEventsShown] = useState(false);
  const [contextProfile, setContextProfile] = useState<string | null>(null);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [bases, setBases] = useState<Record<string, string>>({});
  const [reviewPending, setReviewPending] = useState<Record<string, boolean>>({});
  const [comment, setComment] = useState("");
  const [showInternal, setShowInternal] = useState(false);
  const [artifact, setArtifact] = useState<Json | null>(null);
  const artifactRequest = useRef<{ requestId: string; missionId: string | null; artifactId: string } | null>(null);
  const selectedRef = useRef<string | null>(null);
  /** 打开过的任务里"可能卡住"的步骤数（执行图算出，列表行显示提示）。 */
  const [stalled, setStalled] = useState<Record<string, number>>({});
  const flights = useRef<Record<string, Flight>>({});
  /** UI request_id → review approval_id, so only a successful atomic decision clears its input. */
  const reviewDecisionRequests = useRef(new Map<string, string>());
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

  /** Review decisions must either be in the response map before sending or remain retryable locally. */
  const submitReviewDecision = useCallback(
    (approvalId: string, decision: "review_pass" | "review_fail", note: string) => {
      const requestId = newKey();
      const payload: Json = {
        approval_id: approvalId,
        decision,
        ...(note ? { note } : {}),
      };
      // Register and mark pending first: a synchronous response may arrive from a test or transport adapter.
      reviewDecisionRequests.current.set(requestId, approvalId);
      setReviewPending((pending) => ({ ...pending, [approvalId]: true }));
      try {
        if (channel?.send({ type: "mission_approval_decide", request_id: requestId, payload })) return;
      } catch {
        // A throwing transport has the same local outcome as a rejected send.
      }
      reviewDecisionRequests.current.delete(requestId);
      setReviewPending((pending) => ({ ...pending, [approvalId]: false }));
      useMissionsStore.getState().setError("连接不可用，复核请求未发送");
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
    reviewDecisionRequests.current.clear();
    setReviewPending({});
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
        const pushed = Array.isArray(payload.events) ? (payload.events as MissionEvent[]) : [];
        const onlyNoise = payload.truncated !== true && pushed.length > 0 && pushed.every((e) => NOISE_EVENTS.has(e.type))
          && text(payload.status) === text(record(state.detail?.mission).status);
        if (!onlyNoise) fetchDetail(missionId);
        // 2026-09-26 推送带事件：接得上就直接追加；有缺口、没带全或分页在途时照旧分页补齐
        const lastSeq = Number(payload.last_seq) || 0;
        const events = Array.isArray(payload.events) ? (payload.events as MissionEvent[]) : [];
        const appended = payload.truncated !== true && !state.eventsLoading[missionId]
          && state.appendPushed(missionId, Number(payload.from_seq) || 0, events, lastSeq);
        if (!appended && lastSeq > (state.eventCursor[missionId] ?? 0)) fetchEvents(missionId, true);
        return;
      }
      if (!OWN_RESPONSES.has(type)) return;

      const ok = payload.ok === true;
      const data = record(payload.data);
      const requestId = text(payload.request_id);
      const reviewApprovalId = type === "mission_approval_decide_response"
        ? reviewDecisionRequests.current.get(requestId)
        : undefined;
      if (reviewApprovalId !== undefined) {
        reviewDecisionRequests.current.delete(requestId);
        setReviewPending((pending) => ({ ...pending, [reviewApprovalId]: false }));
        if (ok) setReasons((current) => ({ ...current, [reviewApprovalId]: "" }));
      }
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
            setSources([]);
            setStrictQuotes(false);
            createRetry.current = null;
            setContextProfile(null);
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
        case "mission_action_resolve_response":
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
      flights.current = {}; requests.current.clear(); reviewDecisionRequests.current.clear(); setReviewPending({}); artifactRequest.current = null; setArtifact(null);
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

  // 兜底刷新（2026-09-25 真机点击）：等人操作的状态（如"授权本轮规划"）不一定伴随新事件，
  // 推送可能察觉不到；打开的任务未结束时每 5 秒取一次详情，界面最多 5 秒跟上。
  const openStatus = text(record(store.detail?.mission).status);
  useEffect(() => {
    if (!channel || !store.selectedId || TERMINAL.has(openStatus)) return undefined;
    const timer = setInterval(() => {
      if (selectedRef.current) fetchDetail(selectedRef.current);
    }, 5000);
    return () => clearInterval(timer);
  }, [channel, store.selectedId, openStatus, fetchDetail]);

  const status = store.status;
  const domains = record(record(record(status?.deployment_manifest).features).domains);
  const proposedContextId = contextProfile ?? status?.default_context_profile_id ?? "";
  const contextOffered = status?.context_profiles ?? [];
  const selectedContextId = createRetry.current || contextOffered.some((p) => p.profile_id === proposedContextId)
    ? proposedContextId : status?.default_context_profile_id ?? "";
  const selectedContext = status?.context_profiles?.find((p) => p.profile_id === selectedContextId);
  const defaultTokenCap = selectedContext?.mission_max_tokens ?? status?.mission_budget_defaults?.max_tokens;
  // 2026-09-25 UI 全量点击：Token 上限填 -5 时以前被悄悄忽略、按默认值创建。
  const positiveIntOrBlank = (value: string) => !value.trim() || (Number.isSafeInteger(Number(value)) && Number(value) > 0);
  const validCaps = positiveIntOrBlank(maxTokens) && positiveIntOrBlank(maxAttempts);
  // 2026-09-25 UI 全量点击："pytest: 至少 6 个测试…" 被当成 pytest 命令参数运行，
  // 结果 "no tests ran"、任务失败。以 pytest: 开头的行后面只能是测试路径/参数。
  const badPytestLines = criteria.split("\n").map((line) => line.trim())
    .filter((line) => line.startsWith("pytest:") && !/^pytest:\s*[\w./\-:=\[\]]+(\s+[\w./\-:=\[\]]+)*$/.test(line));
  // 附资料建任务需要后台支持原子批次（与严格引用模式同一接口）
  const canAttachSources = domains.atomic_source_create === true;
  const sourcesComplete = sources.every((source) => source.path.trim().length > 0 && source.path !== "sources/" && source.content.length > 0);
  const submittable = validCaps && badPytestLines.length === 0 && !createPending && !sourceImporting && !!channel && goal.trim().length > 0 && criteria.split("\n").some((line) => line.trim().length > 0) &&
    (!selectedContextId || !!selectedContext) &&
    // 2026-09-26：任务可以附带参考资料（可选）；严格引用必须至少一份。
    (sources.length === 0 ? !strictQuotes : canAttachSources && sourcesComplete);
  // 2026-09-26 真机测试：目标写了"根据参考资料《新品需求说明》…"却没附资料，执行者找不到只好编数字。
  const mentionsMaterial = /参考资料|附件|资料里|资料中|根据资料|《[^》]+》/.test(goal);
  const missingMaterialHint = mentionsMaterial && sources.length === 0
    ? "目标里提到了参考资料，但还没有附上任何资料；需要的话请在上面「参考资料」里添加" : "";
  // 2026-09-26 真机点击：按钮变灰却不说原因，用户以为"点了没反应"。
  const submitBlocker = createPending || submittable ? ""
    : !channel ? "还没连上后台，请稍候"
    : !goal.trim() ? "请填写任务目标"
    : !criteria.split("\n").some((line) => line.trim()) ? "请至少写一条成功条件"
    : sourceImporting ? "正在导入来源资料…"
    : strictQuotes && sources.length === 0 ? "严格引用需要至少一份资料：在「参考资料」里点「添加来源」粘贴正文，或「导入来源文件」；不需要就在高级设置里取消勾选"
    : !sourcesComplete
      ? "每份来源资料都要填写路径（如 sources/笔记.md）和正文"
    : "请检查上面标红的设置";

  const submit = () => {
    if (!submittable) return;
    const previousRetry = createRetry.current;
    const previousContext = contextProfile;
    // Retrying an uncertain creation must not adopt a changed deployment default.
    setContextProfile(selectedContextId);
    const budget: Json = {};
    if (Number(maxTokens) > 0) budget.max_tokens = Math.floor(Number(maxTokens));
    if (Number(maxAttempts) > 0) budget.max_attempts = Math.floor(Number(maxAttempts));
    const spec = {
      goal: goal.trim(),
      // 2026-10-02 用户决定（严格引用选 A）：不再由程序逐条核对引用，改成一条交给审阅员判断的要求。
      success_criteria: [...criteria.split("\n").map((line) => line.trim()).filter(Boolean),
        ...(strictQuotes ? [STRICT_QUOTES_CRITERION] : [])],
      ...(selectedContext ? { runtime_profile_id: selectedContext.profile_id } : {}),
      ...(Object.keys(budget).length ? { budget } : {}),
    };
    // 2026-09-25 UI 全量点击：来源路径写成 notes.md（没有 sources/ 前缀）时后端报"找不到这个对象"。
    const sourcesOut = sources.map((source) => {
      const path = source.path.trim().replace(/^\/+/, "");
      return { ...source, path: path.startsWith("sources/") ? path : `sources/${path}` };
    });
    const withSources = sourcesOut.length > 0;
    const fingerprint = JSON.stringify([spec, withSources ? sourcesOut : []]);
    if (createRetry.current?.fingerprint !== fingerprint) createRetry.current = { fingerprint, key: newKey() };
    const missionSpec = { ...spec, idempotency_key: createRetry.current.key };
    const requestId = newKey();
    createRequest.current = requestId;
    setCreatePending(true);
    store.setError(null);
    const accepted = channel?.send({
      type: withSources ? "mission_create_with_sources" : "mission_create", request_id: requestId,
      payload: !withSources ? missionSpec
        : { mission: missionSpec, sources: sourcesOut },
    });
    if (!accepted) {
      // No new request left the client. Preserve an earlier uncertain request, if any.
      createRetry.current = previousRetry;
      setContextProfile(previousContext);
      createRequest.current = null;
      setCreatePending(false);
      store.setError("连接不可用，创建请求未发送");
      return;
    }
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

  const refreshPlanningQuestions = useCallback(() => {
    if (selectedRef.current) refreshSelected(selectedRef.current);
  }, [refreshSelected]);

  const onStalled = useCallback((count: number) => {
    const id = selectedRef.current;
    if (id) setStalled((current) => current[id] === count ? current : { ...current, [id]: count });
  }, []);
  const detail = store.detail;
  const mission = record(detail?.mission);
  const selectedId = store.selectedId;
  const events = store.selectedEvents ?? [];
  // 2026-09-25 UI 全量点击：以前只显示最近 50 条，更早的已加载也看不到。
  const recentEvents = allEventsShown ? events : events.slice(-TIMELINE_SIZE);
  const showMore = selectedId ? store.selectedHasMore && !store.selectedLoading : false;
  const pendingApprovals = useMemo(
    () => list(detail?.approvals).filter((approval) => text(approval.state) === "PENDING"),
    [detail],
  );
  const waiting: unknown[] = Array.isArray(detail?.waiting_on) ? (detail.waiting_on as unknown[]) : [];
  const artifacts = list(detail?.artifacts);
  // 用户要的交付物在前；审阅/检查留下的内部记录（.assurance/…）默认收起（2026-09-25 真机点击）。
  const internals = artifacts.filter((item) => text(item.path).startsWith(".assurance/"));
  // 2026-09-25 UI 全量点击：每次尝试都登记一份产物，同一文件在列表里出现多次。
  // 每个路径只列一份：优先最后一份已验证的，否则最后一份。
  const deliverables = Array.from(
    artifacts.filter((item) => !text(item.path).startsWith(".assurance/"))
      .reduce((byPath, item) => {
        const path = text(item.path);
        const kept = byPath.get(path);
        const verified = (row: Json) => text(row.verification_status).toUpperCase() === "VERIFIED";
        if (!kept || verified(item) || !verified(kept)) byPath.set(path, item);
        return byPath;
      }, new Map<string, Json>())
      .values(),
  );
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
      {confirmDialog}
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
        {backgroundTrouble(status) ? (
          <div role="alert" data-testid="background-trouble" style={{ ...box, borderColor: tokens.color.warning.bg, flexShrink: 0 }}>{backgroundTrouble(status)}</div>
        ) : null}
        {/* flexShrink 0: with many rows the column scrolls instead of squashing them (2026-09-25) */}
        <button type="button" style={{ ...button, flexShrink: 0 }} onClick={() => { setCreating(true); store.select(null); selectedRef.current = null; setArtifact(null); }}>
          新建任务
        </button>
        {store.missions.length === 0 ? (
          <div style={{ color: dark.textMuted }}>还没有任务。点「新建任务」开始。</div>
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
              style={{ ...button, height: "auto", flexShrink: 0, padding: tokens.space.sm, textAlign: "left", display: "flex", flexDirection: "column", gap: 2 }}
            >
              <span>{row.goal}</span>
              <span style={muted}>
                {stateLabel(row.ui_state, row.status)}
                {row.pending_approvals ? ` · 待审批：${row.pending_approvals}` : ""}
                {row.blocked && row.ui_state !== "unknown" ? " · 结果未知" : ""}
                {stalled[row.id] ? ` · ${stalled[row.id]} 个步骤可能卡住` : ""}
              </span>
              <MissionProgress status={row.status} uiState={row.ui_state} counts={row.task_counts} />
            </button>
          ))
        )}
        <div style={{ ...box, marginTop: "auto", flexShrink: 0 }} aria-label="策略状态">
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
            <label htmlFor="mission-goal">任务目标</label>
            <textarea id="mission-goal" aria-label="任务目标" style={field} disabled={createPending} value={goal} onChange={(e) => setGoal(e.target.value)} />
            <label htmlFor="mission-criteria">成功条件（每行一条）</label>
            <textarea id="mission-criteria" aria-label="成功条件" style={field} disabled={createPending} value={criteria} onChange={(e) => setCriteria(e.target.value)} />
            <PublishCriterionHelper criteria={criteria} onChange={setCriteria} disabled={createPending} publish={store.status?.publish} />
            <div style={muted}>普通文字写要求即可；以 pytest: 开头的行会当作测试命令运行，后面写测试路径，例如 pytest: tests/</div>
            {badPytestLines.length > 0 && <div role="alert" style={{ color: dark.danger }}>「{badPytestLines[0]}」会被当作测试命令运行，但 pytest: 后面不是测试路径。要写说明就去掉 pytest: 前缀。</div>}
            {canAttachSources && <div data-testid="create-sources" style={{ display: "flex", flexDirection: "column", gap: tokens.space.xs }}>
              <div style={{ fontWeight: tokens.weight.semibold }}>参考资料（可选）</div>
              <div style={muted}>给任务附上会议纪要、需求文档等，执行时可以读取。目标里提到的资料要在这里附上，系统不会自己去找。</div>
              <SourceDrafts sources={sources} onChange={setSources} disabled={createPending} onBusy={setSourceImporting} />
            </div>}
            <details data-testid="create-advanced" open={strictQuotes || undefined}>
            <summary style={{ cursor: "pointer", color: dark.textMuted }}>高级设置（可选，一般不用改）</summary>
            <div style={{ display: "flex", flexDirection: "column", gap: tokens.space.sm, marginTop: tokens.space.sm }}>
            {canAttachSources && <label style={{ display: "flex", alignItems: "flex-start", gap: tokens.space.xs }}>
              <input aria-label="严格引用模式" type="checkbox" checked={strictQuotes} disabled={createPending}
                onChange={(e) => setStrictQuotes(e.target.checked)} />
              <span>严格引用：只根据我提供的资料作答，每个结论都要逐字引用原文并注明出处；审阅员会按这条要求检查（需要至少一份资料）</span>
            </label>}
            {!!status?.context_profiles?.length && <label style={muted}>输入上下文容量
              <select aria-label="输入上下文容量" style={field} value={selectedContextId} disabled={createPending} onChange={(e) => setContextProfile(e.target.value)}>
                {status.context_profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.max_total_tokens ? `${profile.max_total_tokens / 1024}K 总窗口` : `${profile.max_input_tokens / 1024}K tokens`}</option>)}
              </select>
              <span>{selectedContext?.max_total_tokens
                ? `输入与输出共享 ${selectedContext.max_total_tokens / 1024}K 总窗口；输入最多 ${selectedContext.max_input_tokens / 1024}K，已预留输出与安全余量。总预算按实际调用消耗。`
                : "容量包含本轮材料与历史；单次输出另计，上限 32K。总预算按实际调用消耗。"}</span>
            </label>}
            <div style={{ display: "flex", gap: tokens.space.sm }}>
              <input aria-label="Token 上限" inputMode="numeric" placeholder={defaultTokenCap ? `Token 上限（留空=${defaultTokenCap}）` : "Token 上限（可选）"} style={{ ...field, minHeight: 0, height: tokens.controlHeight }} value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} />
              <input aria-label="尝试次数上限" inputMode="numeric" placeholder={status?.mission_budget_defaults ? `尝试次数上限（留空=${status.mission_budget_defaults.max_attempts}）` : "尝试次数上限（可选）"} style={{ ...field, minHeight: 0, height: tokens.controlHeight }} value={maxAttempts} onChange={(e) => setMaxAttempts(e.target.value)} />
            </div>
            {!validCaps && <div role="alert" style={{ color: dark.danger }}>Token 上限和尝试次数上限要填正整数，或者留空用默认值。</div>}
            </div>
            </details>
            <button type="button" style={button} disabled={!submittable} onClick={submit}>
              提交任务
            </button>
            {submitBlocker && <div role="status" data-testid="submit-blocker" style={muted}>还不能提交：{submitBlocker}</div>}
            {!submitBlocker && missingMaterialHint && <div role="status" data-testid="material-hint" style={{ color: dark.warning }}>{missingMaterialHint}</div>}
            {createPending && <div role="status">正在创建任务…</div>}
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
                {mission.stop_reason ? ` · 停止原因：${STOP_REASON_LABEL[text(mission.stop_reason).toLowerCase()] ?? text(mission.stop_reason)}` : ""}
                {` · 策略版本：${text(record(detail.mission_policy).version_id) || "—"}`}
              </div>
              <div style={muted}>
                Token 已结算 {text(record(detail.usage).settled_tokens) || "未知"} · 当前预留 {text(record(detail.usage).reserved_tokens) || "未知"} · 金额 未计价
              </div>
              <div style={muted} data-testid="mission-budget">
                {`预算：Token 上限 ${text(record(mission.budget).max_tokens) || "—"} · 尝试次数上限 ${text(record(mission.budget).max_attempts) || "—"}`}
              </div>
              {!!record(detail.runtime_context).max_input_tokens && <div style={muted} data-testid="mission-context">
                {record(detail.runtime_context).max_total_tokens
                  ? `本任务总上下文 ${Number(record(detail.runtime_context).max_total_tokens) / 1024}K，输入最多 ${Number(record(detail.runtime_context).max_input_tokens) / 1024}K tokens`
                  : `本任务输入上下文 ${Number(record(detail.runtime_context).max_input_tokens) / 1024}K tokens`}
              </div>}
              {waiting.length ? (
                <div aria-label="等待原因" style={{ marginTop: tokens.space.sm }}>
                  {waiting.map((item, index) => (
                    <div key={`${index}-${text(record(item).request_id)}`}>
                      {waitLine(item)}
                      {text(record(item).kind) === "reconciliation" && record(item).needs_human === true ? (
                        <ResolveOutcomeBox actionKey={text(record(item).action_key)}
                          failed={text(record(item).state) === "FAILED"} send={send} />
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}
              {!TERMINAL.has(text(mission.status)) && <button
                type="button"
                style={{ ...button, marginTop: tokens.space.sm }}
                onClick={async () => {
                  // 2026-09-25 UI 全量点击：取消不可撤销，以前一点就取消。
                  if (await ask({ title: "取消这个任务？", message: "取消后任务停止，不能恢复。已生成的产物会保留。", confirm_label: "取消任务" }))
                    send("mission_cancel", { mission_id: selectedId });
                }}
              >
                取消任务
              </button>}
            </div>

            {(() => {
              const step = nextStep(detail, pendingApprovals.length);
              return (
                <div data-testid="mission-next-step" role="status" style={{ ...box, borderColor: step.target ? tokens.color.accent.border : tokens.color.surface.hairline, display: "flex", flexWrap: "wrap", alignItems: "center", gap: tokens.space.sm }}>
                  <span style={{ flex: "1 1 240px", minWidth: 0 }}>{step.text}</span>
                  {step.target && <button type="button" style={button} onClick={() => jumpTo(step.target!)}>{step.action}</button>}
                  {/* 授权就在提示条里一键完成（2026-09-25 真机点击：跳转后还要再找按钮） */}
                  {list(detail.planning_authorization_requests).length > 0 && !nextStepBlocksAuthorization(detail) &&
                    <div style={{ flexBasis: "100%" }}>
                      <PlanningAuthorization requests={list(detail.planning_authorization_requests)} channel={channel}
                        onChanged={refreshPlanningQuestions} />
                    </div>}
                </div>
              );
            })()}

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

            <div id="mission-step-requirements"><OperationWorkspace value={detail.operation_workspace} channel={channel}
              onChanged={refreshPlanningQuestions} /></div>
            {nextStepBlocksAuthorization(detail) && <PlanningAuthorization requests={list(detail.planning_authorization_requests)} channel={channel}
              onChanged={refreshPlanningQuestions} />}
            <div id="mission-step-questions"><PlanningQuestions questions={list(detail.planning_questions)} channel={channel}
              onAnswered={refreshPlanningQuestions} /></div>
            <div id="mission-step-approvals" />
            {pendingApprovals.map((approval) => {
              const requestId = text(approval.request_id);
              const reason = reasons[requestId] ?? "";
              const kind = text(approval.kind);
              const action = record(approval.action);
              return (
                <div key={requestId} style={{ ...box, minWidth: 0, overflowWrap: "anywhere", borderColor: tokens.color.accent.border }} data-testid={`approval-${requestId}`}>
                  {kind === "action" ? <ActionApprovalSummary summary={approval.summary} action={action} /> : (
                    <div style={heading}>
                      {WAIT_LABEL[kind] ?? `未知审批类型（${kind || "未提供"}）`}：<ModelText value={approval.summary} />
                    </div>
                  )}
                  {kind === "action" ? <ActionOutcome action={action} /> : null}
                  {kind === "source_change" ? <pre style={{ minWidth: 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere", wordBreak: "break-word" }}>{JSON.stringify(record(approval.source_change), null, 2)}</pre> : null}
                  {kind === "action" || kind === "source_change" ? (
                    <>
                      <textarea aria-label="拒绝理由" style={field} value={reason} onChange={(e) => setReasons({ ...reasons, [requestId]: e.target.value })} />
                      <div style={{ display: "flex", gap: tokens.space.sm }}>
                        <button type="button" style={button} onClick={() => send("mission_approval_decide", { approval_id: requestId, decision: "approve" })}>批准</button>
                        <button type="button" style={button} disabled={!reason.trim()} onClick={() => send("mission_approval_decide", { approval_id: requestId, decision: "reject", reason })}>拒绝</button>
                      </div>
                    </>
                  ) : kind === "review" ? (
                    <>
                      <textarea
                        aria-label="复核理由"
                        placeholder="通过可选；未通过请说明原因"
                        style={field}
                        value={reason}
                        onChange={(e) => setReasons({ ...reasons, [requestId]: e.target.value })}
                      />
                      <div style={{ display: "flex", gap: tokens.space.sm }}>
                        <button
                          type="button"
                          style={button}
                          disabled={reviewPending[requestId] === true}
                          onClick={() => {
                            const note = reason.trim();
                            submitReviewDecision(requestId, "review_pass", note);
                          }}
                        >复核通过</button>
                        <button
                          type="button"
                          style={button}
                          disabled={!reason.trim() || reviewPending[requestId] === true}
                          onClick={() => {
                            submitReviewDecision(requestId, "review_fail", reason.trim());
                          }}
                        >复核不通过</button>
                      </div>
                    </>
                  ) : kind === "arbitration" ? (
                    <>
                      <textarea aria-label="仲裁依据" style={field} value={reason} onChange={(e) => setReasons({ ...reasons, [requestId]: e.target.value })} />
                      <div style={{ display: "flex", gap: tokens.space.sm, flexWrap: "wrap" }}>
                        {(Array.isArray(approval.options) ? approval.options : []).filter((option) =>
                          option === "met" || option === "unmet").map((option) => (
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

            {/* 2026-09-27：默认看「任务过程」（模型每一步做了什么）；结构图（步骤之间的先后关系）按需打开 */}
            <div role="tablist" aria-label="任务视图" style={{ display: "flex", gap: tokens.space.xs }}>
              {([["graph", "执行图"], ["story", "任务过程"]] as const).map(([key, label]) => (
                <button key={key} type="button" role="tab" aria-selected={graphTab === key} onClick={() => setGraphTab(key)}
                  style={{ ...button, borderColor: graphTab === key ? tokens.color.accent.border : tokens.color.surface.hairline,
                    fontWeight: graphTab === key ? tokens.weight.semibold : undefined }}>{label}</button>
              ))}
            </div>
            {graphTab === "story" ? (
              <MissionStory key={selectedId + ":story"} missionId={selectedId} channel={channel} missionStatus={text(mission.status)}
                onStalled={onStalled} />
            ) : (
              <Suspense fallback={<div style={muted}>正在加载执行图…</div>}>
                <LiveGraph key={selectedId + ":live-graph"} missionId={selectedId} channel={channel} detail={detail}
                  onLoadMoreEvents={() => fetchEvents(selectedId, true)} onStalled={onStalled} />
              </Suspense>
            )}

            {list(detail.sources).length > 0 && <MissionSources key={selectedId + ":sources"} missionId={selectedId} sources={list(detail.sources)} channel={channel} onChanged={() => refreshSelected(selectedId)} />}

            <details data-testid="mission-diagnostics-group">
            <summary style={{ cursor: "pointer", color: dark.textMuted }}>诊断信息（保证状态等，排查问题时用）</summary>

            {status?.assurance_available === true && <MissionAssurance key={selectedId + ":assurance"} missionId={selectedId} channel={channel} />}

            {status?.diagnostics_available === true && <MissionDiagnostics key={selectedId + ":diagnostics"} missionId={selectedId} channel={channel} />}
            </details>

            <div style={box}>
              <div style={heading}>Task 与验证</div>
              {list(detail.tasks).map((task) => {
                const taskId = text(task.id);
                const blockedIds = new Set(list(detail.blocked).map((item) => text(item.task_id)));
                const canTakeOver = !TERMINAL.has(text(mission.status)) && !TASK_ENDED.has(text(task.status)) && !blockedIds.has(taskId);
                return (
                  <div key={taskId} data-testid={`task-${taskId}`} style={{ marginTop: tokens.space.sm }}>
                    <ModelText value={task.goal} /> · {TASK_STATUS_LABEL[text(task.status)] ?? text(task.status)}
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
                    <div>结果摘要：<ModelText value={result.summary} /></div>
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
              <div id="mission-step-artifacts" style={box}>
                <div style={heading}>产物</div>
                {internals.length > 0 && <button type="button" style={{ ...button, height: "auto", padding: `2px ${tokens.space.sm}px`, marginTop: tokens.space.xs, color: dark.textMuted }}
                  onClick={() => setShowInternal((v) => !v)}>
                  {showInternal ? "收起系统检查记录" : `显示系统检查记录（${internals.length} 个，排查问题时看）`}
                </button>}
                {[...deliverables, ...internals].map((item, index) => {
                  const artifactId = text(item.id);
                  const folded = index >= deliverables.length;
                  const verification = text(item.verification_status);
                  return (
                    <div
                      key={artifactId}
                      data-testid={`artifact-${artifactId}`}
                      data-internal={folded ? "true" : undefined}
                      hidden={folded && !showInternal}
                      style={{ display: folded && !showInternal ? "none" : "flex", alignItems: "center", gap: tokens.space.sm, marginTop: tokens.space.sm, minWidth: 0 }}
                    >
                      <span style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere", wordBreak: "break-word" }}>
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

            {artifact ? <div style={{ minWidth: 0 }}><ArtifactPanel artifact={artifact}
              verified={[...deliverables, ...internals].some((item) => text(item.id) === text(artifact.artifact_id)
                && text(item.verification_status).toUpperCase() === "VERIFIED")} /></div> : null}

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
              <div style={heading}>原始事件记录</div>
              <div style={muted}>系统内部记录，排查问题时看；想了解模型做了什么，看上面的「任务过程」。</div>
              {events.length > TIMELINE_SIZE ? (
                <div style={muted}>
                  {allEventsShown ? `共 ${events.length} 条` : `共 ${events.length} 条，显示最近 ${TIMELINE_SIZE} 条`}
                  <button type="button" style={{ ...button, marginLeft: tokens.space.sm }} onClick={() => setAllEventsShown((v) => !v)}>
                    {allEventsShown ? "只看最近" : "显示全部"}
                  </button>
                </div>
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
