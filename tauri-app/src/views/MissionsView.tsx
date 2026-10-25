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
 * 不画成通过）。可访问名称是原生 AX 验收的定位点，改名要同步
 * 改验收脚本。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useDepsChanged } from "../hooks/useDepsChanged";
import { useShallow } from "zustand/react/shallow";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import { type SourceDraft } from "./MissionSources";
import { useConfirm } from "../components/useConfirm";
import {
  asList as list,
  asRecord as record,
  asText as text,
  newRequestKey as newKey,
  useMissionsStore,
  type MissionEvent,
  type MissionsChannel,
} from "../stores/missionsStore";
import { PROTOCOL_ERROR, STALE_NOTE } from "../ws/orchestrationContracts";
import {
  TERMINAL,
  ERROR_TEXT,
  NOISE_EVENTS,
  OWN_RESPONSES,
  EVENT_PAGE_LIMIT,
  STRICT_QUOTES_CRITERION,
  MAX_AUTO_PAGES,
  TIMELINE_SIZE,
} from "./missionsViewShared";
import type { Json, Flight } from "./missionsViewShared";
import { MissionListAside } from "./MissionListAside";
import { MissionCreateForm } from "./MissionCreateForm";
import { MissionDetailPanel } from "./MissionDetailPanel";
import "./MissionsView.styles.css";

export interface MissionsViewProps {
  channel: MissionsChannel | null;
}
/** 结构图（elkjs + React Flow）按需加载：默认看「任务过程」，不点结构图就不下载这两个库。 */
export const MissionsView: React.FC<MissionsViewProps> = ({ channel }) => {
  // 2026-09-27 性能：按字段订阅。以前订阅整个 store，任何任务的每秒推送都让整页重画。
  const store = useMissionsStore(
    useShallow((s) => ({
      selectedId: s.selectedId,
      detail: s.detail,
      status: s.status,
      policy: s.policy,
      missions: s.missions,
      error: s.error,
      listError: s.listError,
      selectedEvents: s.selectedId ? s.events[s.selectedId] : undefined,
      selectedHasMore: s.selectedId ? s.eventsHasMore[s.selectedId] === true : false,
      selectedLoading: s.selectedId ? s.eventsLoading[s.selectedId] === true : false,
      select: s.select,
      setError: s.setError,
    })),
  );
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
  // 渲染只需要知道"有没有一次结果未明的创建"；幂等键本身留在 ref 里供提交时同步读取。
  const [hasCreateRetry, setHasCreateRetry] = useState(false);
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
  const artifactRequest = useRef<{
    requestId: string;
    missionId: string | null;
    artifactId: string;
  } | null>(null);
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
        if (channel?.send({ type: "mission_approval_decide", request_id: requestId, payload }))
          return;
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
        send("mission_events", {
          mission_id: missionId,
          after_seq: after,
          limit: EVENT_PAGE_LIMIT,
        }),
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

  // 订阅重建（比对项与下面订阅 effect 的依赖一致）：旧订阅上的请求不会再有回执，渲染期清掉对应画面状态。
  const subscriptionChanged = useDepsChanged([
    channel,
    send,
    flightOf,
    fetchDetail,
    fetchEvents,
    refreshSelected,
  ]);
  if (subscriptionChanged && channel) {
    setReviewPending({});
    setArtifact(null);
    setCreatePending(false);
  }
  useEffect(() => {
    if (!channel) return undefined;
    // a new subscription: whatever was in flight on the old one will never answer
    flights.current = {};
    requests.current.clear();
    reviewDecisionRequests.current.clear();
    artifactRequest.current = null;
    if (createRequest.current)
      useMissionsStore.getState().setError("连接已变化，创建结果未知；可以使用原请求键重试");
    createRequest.current = null;
    useMissionsStore.getState().clearEventsLoading();

    const off = channel.onMessage((incoming: IncomingMessage) => {
      const message = incoming as unknown as { type?: unknown; payload?: unknown };
      const type = text(message.type);
      const payload = record(message.payload);
      const state = useMissionsStore.getState();

      if (type === "session_event") {
        const missionId = text(payload.mission_id);
        if (!missionId || missionId !== selectedRef.current) return;
        const progress = record(payload.payload);
        const taskEvent = payload.kind === "task_progress";
        if (!taskEvent || !NOISE_EVENTS.has(text(progress.type))) fetchDetail(missionId);
        const seq = taskEvent ? Number(progress.seq) : Number.POSITIVE_INFINITY;
        if (seq > (state.eventCursor[missionId] ?? 0)) fetchEvents(missionId, true);
        return;
      }
      if (!OWN_RESPONSES.has(type)) return;

      const ok = payload.ok === true;
      const data = record(payload.data);
      const requestId = text(payload.request_id);
      const reviewApprovalId =
        type === "mission_approval_decide_response"
          ? reviewDecisionRequests.current.get(requestId)
          : undefined;
      if (reviewApprovalId !== undefined) {
        reviewDecisionRequests.current.delete(requestId);
        setReviewPending((pending) => ({ ...pending, [reviewApprovalId]: false }));
        if (ok) setReasons((current) => ({ ...current, [reviewApprovalId]: "" }));
      }
      if (
        (type === "mission_create_response" || type === "mission_create_with_sources_response") &&
        requestId !== createRequest.current
      )
        return;
      const tracked = requests.current.get(requestId);
      if (
        (type === "mission_get_response" || type === "mission_events_response") &&
        tracked === undefined
      )
        return;
      if (type === "mission_artifact_read_response") {
        const pending = artifactRequest.current;
        if (
          !pending ||
          pending.requestId !== requestId ||
          pending.missionId !== selectedRef.current
        )
          return;
        if (ok && data.artifact_id != null && data.artifact_id !== pending.artifactId) return;
        artifactRequest.current = null;
      }
      if (tracked !== undefined) requests.current.delete(requestId);
      if (payload.ok === false && (tracked === undefined || tracked === selectedRef.current)) {
        const code = text(payload.error_code);
        const message = ERROR_TEXT[code] ?? (text(payload.error) || code || "请求失败");
        // 详情、事件读到坏消息时保留上次的内容，并标明下面是旧的
        const stale =
          code === PROTOCOL_ERROR &&
          ((type === "mission_get_response" && state.detail !== null) ||
            (type === "mission_events_response" && (state.events[tracked ?? ""]?.length ?? 0) > 0));
        state.setError(stale ? message + STALE_NOTE : message);
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
            if (ok && selected && typeof through === "number" && through < cursor)
              fetchDetail(missionId);
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
            setHasCreateRetry(false);
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
      flights.current = {};
      requests.current.clear();
      reviewDecisionRequests.current.clear();
      setReviewPending({});
      artifactRequest.current = null;
      setArtifact(null);
      useMissionsStore.getState().clearEventsLoading();
      if (createRequest.current)
        useMissionsStore.getState().setError("连接已变化，创建结果未知；可以使用原请求键重试");
      createRequest.current = null;
      setCreatePending(false);
      if (createTimer.current) clearTimeout(createTimer.current);
      if (connection === "connected") {
        send("orchestration_policy_status");
        if (selectedRef.current) refreshSelected(selectedRef.current);
      }
    });
    return () => {
      off();
      offState?.();
      if (createTimer.current) clearTimeout(createTimer.current);
    };
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
  const selectedContextId =
    hasCreateRetry || contextOffered.some((p) => p.profile_id === proposedContextId)
      ? proposedContextId
      : (status?.default_context_profile_id ?? "");
  const selectedContext = status?.context_profiles?.find((p) => p.profile_id === selectedContextId);
  const defaultTokenCap =
    selectedContext?.mission_max_tokens ?? status?.mission_budget_defaults?.max_tokens;
  // 2026-09-25 UI 全量点击：Token 上限填 -5 时以前被悄悄忽略、按默认值创建。
  const positiveIntOrBlank = (value: string) =>
    !value.trim() || (Number.isSafeInteger(Number(value)) && Number(value) > 0);
  const validCaps = positiveIntOrBlank(maxTokens) && positiveIntOrBlank(maxAttempts);
  // 2026-09-25 UI 全量点击："pytest: 至少 6 个测试…" 被当成 pytest 命令参数运行，
  // 结果 "no tests ran"、任务失败。以 pytest: 开头的行后面只能是测试路径/参数。
  const badPytestLines = criteria
    .split("\n")
    .map((line) => line.trim())
    .filter(
      (line) =>
        line.startsWith("pytest:") && !/^pytest:\s*[\w./\-:=[\]]+(\s+[\w./\-:=[\]]+)*$/.test(line),
    );
  // 附资料建任务需要后台支持原子批次（与严格引用模式同一接口）
  const canAttachSources = domains.atomic_source_create === true;
  const sourcesComplete = sources.every(
    (source) =>
      source.path.trim().length > 0 && source.path !== "sources/" && source.content.length > 0,
  );
  const submittable =
    validCaps &&
    badPytestLines.length === 0 &&
    !createPending &&
    !sourceImporting &&
    !!channel &&
    goal.trim().length > 0 &&
    criteria.split("\n").some((line) => line.trim().length > 0) &&
    (!selectedContextId || !!selectedContext) &&
    // 2026-09-26：任务可以附带参考资料（可选）；严格引用必须至少一份。
    (sources.length === 0 ? !strictQuotes : canAttachSources && sourcesComplete);
  // 2026-09-26 真机测试：目标写了"根据参考资料《新品需求说明》…"却没附资料，执行者找不到只好编数字。
  const mentionsMaterial = /参考资料|附件|资料里|资料中|根据资料|《[^》]+》/.test(goal);
  const missingMaterialHint =
    mentionsMaterial && sources.length === 0
      ? "目标里提到了参考资料，但还没有附上任何资料；需要的话请在上面「参考资料」里添加"
      : "";
  // 2026-09-26 真机点击：按钮变灰却不说原因，用户以为"点了没反应"。
  const submitBlocker =
    createPending || submittable
      ? ""
      : !channel
        ? "还没连上后台，请稍候"
        : !goal.trim()
          ? "请填写任务目标"
          : !criteria.split("\n").some((line) => line.trim())
            ? "请至少写一条成功条件"
            : sourceImporting
              ? "正在导入来源资料…"
              : strictQuotes && sources.length === 0
                ? "严格引用需要至少一份资料：在「参考资料」里点「添加来源」粘贴正文，或「导入来源文件」；不需要就在高级设置里取消勾选"
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
      success_criteria: [
        ...criteria
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
        ...(strictQuotes ? [STRICT_QUOTES_CRITERION] : []),
      ],
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
    const retry =
      createRetry.current?.fingerprint === fingerprint
        ? createRetry.current
        : { fingerprint, key: newKey() };
    createRetry.current = retry;
    setHasCreateRetry(true);
    const missionSpec = { ...spec, idempotency_key: retry.key };
    const requestId = newKey();
    createRequest.current = requestId;
    setCreatePending(true);
    store.setError(null);
    const accepted = channel?.send({
      type: withSources ? "mission_create_with_sources" : "mission_create",
      request_id: requestId,
      payload: !withSources ? missionSpec : { mission: missionSpec, sources: sourcesOut },
    });
    if (!accepted) {
      // No new request left the client. Preserve an earlier uncertain request, if any.
      createRetry.current = previousRetry;
      setHasCreateRetry(previousRetry !== null);
      setContextProfile(previousContext);
      createRequest.current = null;
      setCreatePending(false);
      store.setError("连接不可用，创建请求未发送");
      return;
    }
    createTimer.current = setTimeout(() => {
      if (createRequest.current === requestId) {
        createRequest.current = null;
        setCreatePending(false);
        useMissionsStore.getState().setError("创建超时，结果未知；可以使用原请求键重试");
      }
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
    if (id)
      setStalled((current) => (current[id] === count ? current : { ...current, [id]: count }));
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
  const waiting: unknown[] = Array.isArray(detail?.waiting_on)
    ? (detail.waiting_on as unknown[])
    : [];
  const artifacts = list(detail?.artifacts);
  // 用户要的交付物在前；审阅/检查留下的内部记录（.assurance/…）默认收起（2026-09-25 真机点击）。
  const internals = artifacts.filter((item) => text(item.path).startsWith(".assurance/"));
  // 2026-09-25 UI 全量点击：每次尝试都登记一份产物，同一文件在列表里出现多次。
  // 每个路径只列一份：优先最后一份已验证的，否则最后一份。
  const deliverables = Array.from(
    artifacts
      .filter((item) => !text(item.path).startsWith(".assurance/"))
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
      <section data-testid="view-missions" aria-label="任务编排" className="sh-MissionsView-1">
        <div role="alert" className="sh-MissionsView-2">
          编排服务不可用：{status.reason ?? status.state}
        </div>
      </section>
    );
  }

  return (
    <section data-testid="view-missions" aria-label="任务编排" className="sh-MissionsView-3">
      {confirmDialog}
      <MissionListAside
        status={status}
        setCreating={setCreating}
        store={store}
        selectedRef={selectedRef}
        setArtifact={setArtifact}
        selectedId={selectedId}
        open={open}
        stalled={stalled}
        drift={drift}
      />

      <div className="sh-MissionsView-4">
        {store.error ? (
          <div role="alert" className="sh-MissionsView-5">
            {store.error}
          </div>
        ) : null}

        {creating ? (
          <MissionCreateForm
            createPending={createPending}
            goal={goal}
            setGoal={setGoal}
            criteria={criteria}
            setCriteria={setCriteria}
            store={store}
            badPytestLines={badPytestLines}
            canAttachSources={canAttachSources}
            sources={sources}
            setSources={setSources}
            setSourceImporting={setSourceImporting}
            strictQuotes={strictQuotes}
            setStrictQuotes={setStrictQuotes}
            status={status}
            selectedContextId={selectedContextId}
            setContextProfile={setContextProfile}
            selectedContext={selectedContext}
            defaultTokenCap={defaultTokenCap}
            maxTokens={maxTokens}
            setMaxTokens={setMaxTokens}
            maxAttempts={maxAttempts}
            setMaxAttempts={setMaxAttempts}
            validCaps={validCaps}
            submittable={submittable}
            submit={submit}
            submitBlocker={submitBlocker}
            missingMaterialHint={missingMaterialHint}
          />
        ) : null}

        {!creating && selectedId && detail ? (
          <MissionDetailPanel
            mission={mission}
            detail={detail}
            waiting={waiting}
            send={send}
            selectedId={selectedId}
            channel={channel}
            ask={ask}
            pendingApprovals={pendingApprovals}
            refreshPlanningQuestions={refreshPlanningQuestions}
            bases={bases}
            setBases={setBases}
            reasons={reasons}
            setReasons={setReasons}
            reviewPending={reviewPending}
            submitReviewDecision={submitReviewDecision}
            graphTab={graphTab}
            setGraphTab={setGraphTab}
            onStalled={onStalled}
            fetchEvents={fetchEvents}
            refreshSelected={refreshSelected}
            status={status}
            artifacts={artifacts}
            internals={internals}
            setShowInternal={setShowInternal}
            showInternal={showInternal}
            deliverables={deliverables}
            setArtifact={setArtifact}
            artifactRequest={artifactRequest}
            artifact={artifact}
            comment={comment}
            setComment={setComment}
            events={events}
            allEventsShown={allEventsShown}
            setAllEventsShown={setAllEventsShown}
            recentEvents={recentEvents}
            showMore={showMore}
          />
        ) : null}
      </div>
    </section>
  );
};
export default MissionsView;
