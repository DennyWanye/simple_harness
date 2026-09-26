// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 执行图（plans/2026-09-25-orchestration-live-view 第 4～6 步）：一张图看拆分与执行。
 *
 * - 数据：`mission_live_graph`（直读分层计划表），不再用对真实任务恒为 NOT_ENABLED 的严格读取。
 * - 实时：本任务的 `mission_changed` 到了就 800ms 防抖重读；读取在途再来推送只合并成一次后续读取；
 *   `through_seq` 倒退的结果丢弃。版本不变只换颜色文字，版本变了才重新排版并高亮新节点 3 秒。
 * - 复合任务是可折叠的框，框里是它拆出的子任务；箭头是先后顺序，数据依赖默认隐藏。
 * - 点节点看详情：任务内容、状态与原因、尝试、验证结果、这个节点的事件；复合任务另列规划决定。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { asList, asRecord, asText, newRequestKey, useMissionsStore, type MissionEvent, type MissionsChannel } from "../../stores/missionsStore";
import { DECISION_STATUS, DECISION_TYPE, TONE_COLOR, displayOf, stepTitle, isStalled, parseLiveGraph, readinessLabel,
  type LiveGraph as Graph, type LiveNode } from "./model";
import { AUTO_COLLAPSE_OVER, buildElkGraph, flatten, structureKey, type Placed } from "./layoutModel";
import { layoutGraph } from "./layout";
import "./LiveGraph.css";

type Props = {
  missionId: string;
  channel: MissionsChannel | null;
  /** mission_get 的投影（tasks/attempts/results/mission），用来给节点起标题和填详情栏。 */
  detail: Record<string, unknown> | null;
  onLoadMoreEvents?: () => void;
  /** "可能卡住"的步骤数变化时告诉外面（任务列表行显示提示）。 */
  onStalled?: (count: number) => void;
};

const DEBOUNCE_MS = 800;
const TIMEOUT_MS = 30000;
const HIGHLIGHT_MS = 3000;
const TERMINAL_MISSION = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
const TITLE_LIMIT = 40;


type NodeData = {
  node: LiveNode; title: string; fullTitle: string; modelWritten: boolean; stalled: boolean; fresh: boolean;
  collapsed: boolean; childCount: number; group: boolean; onToggle: (id: string) => void; onPick: (id: string) => void;
};

function short(value: string): string {
  return value.length > TITLE_LIMIT ? value.slice(0, TITLE_LIMIT - 1) + "…" : value;
}

function TaskCard({ data }: NodeProps<Node<NodeData>>) {
  const { node, title, fullTitle, modelWritten, stalled, fresh, collapsed, childCount, group } = data;
  const display = displayOf(node);
  const color = TONE_COLOR[display.tone];
  const compound = node.form === "compound";
  return (
    <div className={"lg-node" + (group ? " lg-group" : "") + (fresh ? " lg-fresh" : "") + (display.tone === "running" ? " lg-running" : "")
      + (display.tone === "cancelled" ? " lg-cancelled" : "")}
      style={{ borderColor: color }} data-testid={"lg-node-" + node.occurrence_id}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <div className="lg-head">
        {compound && childCount > 0 && <button type="button" className="lg-fold" aria-expanded={!collapsed}
          aria-label={(collapsed ? "展开" : "折叠") + "：" + fullTitle}
          onClick={(event) => { event.stopPropagation(); data.onToggle(node.occurrence_id); }}>{collapsed ? "▸" : "▾"}</button>}
        <button type="button" className="lg-title" title={fullTitle + (modelWritten ? "（模型生成）" : "")}
          onClick={() => data.onPick(node.occurrence_id)}>{title}</button>
      </div>
      <div className="lg-meta">
        <span className="lg-dot" style={{ background: color }} />
        <span>{display.label}</span>
        {node.attempt_count > 0 && <span>· 尝试 {node.attempt_count}</span>}
        {compound && collapsed && childCount > 0 && <span>· 已折叠 {childCount} 个子任务</span>}
        {stalled && <span className="lg-stall" title="超过 10 分钟没有新动静">可能卡住</span>}
      </div>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

const nodeTypes = { task: TaskCard };

function clock(seconds: unknown): string {
  const n = Number(seconds);
  if (!Number.isFinite(n) || n <= 0) return "";
  const d = new Date(n < 1e12 ? n * 1000 : n);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((v) => String(v).padStart(2, "0")).join(":");
}

type Pending = { id: string; kind: "graph" | "decisions"; revision: number | null };

export function LiveGraph({ missionId, channel, detail, onLoadMoreEvents, onStalled }: Props) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState<number | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [showData, setShowData] = useState(false);
  const [placed, setPlaced] = useState<{ key: string; nodes: Placed[]; edges: ReturnType<typeof buildElkGraph>["edges"] } | null>(null);
  const [slow, setSlow] = useState(false);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const [picked, setPicked] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Record<string, unknown>[] | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [slowRead, setSlowRead] = useState(false);
  const graphRequest = useRef<Pending | null>(null);
  const decisionRequest = useRef<string | null>(null);
  const again = useRef(false);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shown = useRef<Graph | null>(null);
  const currentSeq = useRef(0);
  const autoCollapsed = useRef<number | null>(null);
  const [collapseReady, setCollapseReady] = useState<number | null>(null);
  const placedKey = useRef<string | null>(null);
  const revisionRef = useRef<number | null>(null);
  useEffect(() => { revisionRef.current = revision; shown.current = graph; }, [revision, graph]);

  const events = useMissionsStore((state) => state.events[missionId]);
  const hasMoreEvents = useMissionsStore((state) => state.eventsHasMore[missionId] === true);

  const sendGraph = useCallback((wanted: number | null) => {
    if (!channel) return;
    if (graphRequest.current) { again.current = true; return; }
    const id = newRequestKey();
    graphRequest.current = { id, kind: "graph", revision: wanted };
    setLoading(true);
    const payload: Record<string, unknown> = { mission_id: missionId };
    if (wanted !== null) payload.revision = wanted;
    if (!channel.send({ type: "mission_live_graph", request_id: id, payload })) {
      graphRequest.current = null; setLoading(false); setError("连接不可用，请求未发送"); return;
    }
    if (timeout.current) clearTimeout(timeout.current);
    timeout.current = setTimeout(() => {
      if (graphRequest.current?.id !== id) return;
      graphRequest.current = null; setLoading(false); setError("读取超时，已保留上次画面");
    }, TIMEOUT_MS);
  }, [channel, missionId]);

  const sendDecisions = useCallback(() => {
    if (!channel || decisionRequest.current) return;
    const id = newRequestKey();
    decisionRequest.current = id;
    if (!channel.send({ type: "mission_planning_decisions", request_id: id, payload: { mission_id: missionId } })) decisionRequest.current = null;
  }, [channel, missionId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the first read starts on mount (sets "更新中…")
    sendGraph(null);
    const tick = setInterval(() => setNow(Date.now() / 1000), 30000);
    const reset = () => {
      graphRequest.current = null; decisionRequest.current = null; again.current = false; currentSeq.current = 0;
      placedKey.current = null; autoCollapsed.current = null;
      setGraph(null); setPlaced(null); setCollapseReady(null); setDecisions(null); setPicked(null); setLoading(false);
    };
    const off = channel?.onMessage((raw) => {
      const message = raw as unknown as { type?: string; payload?: unknown };
      const body = asRecord(message.payload);
      const type = message.type ?? "";
      if (["companion_identity_unready", "companion_control_rechallenge", "companion_profile_unbound"].includes(type)
        || (type === "companion_identity_status" && body.ready === false)) { reset(); return; }
      if (type === "mission_changed") {
        if (body.mission_id !== missionId || revisionRef.current !== null) return;
        if (debounce.current) clearTimeout(debounce.current);
        debounce.current = setTimeout(() => { debounce.current = null; sendGraph(null); }, DEBOUNCE_MS);
        return;
      }
      if (type === "mission_planning_decisions_response") {
        if (body.request_id !== decisionRequest.current) return;
        decisionRequest.current = null;
        if (body.ok === true) setDecisions(asList(asRecord(body.data).decisions));
        return;
      }
      if (type !== "mission_live_graph_response") return;
      const request = graphRequest.current;
      if (!request || body.request_id !== request.id) return;
      graphRequest.current = null;
      if (timeout.current) clearTimeout(timeout.current);
      setLoading(false);
      if (body.ok !== true) {
        setError(typeof body.error === "string" && body.error ? body.error : "执行图读取失败，请稍后重试");
      } else {
        try {
          const value = parseLiveGraph(body.data, missionId);
          const previous = shown.current;
          if (request.revision === null && value.through_seq < currentSeq.current) {
            // a late, older read never replaces a newer screen
          } else {
            if (request.revision === null) {
              currentSeq.current = value.through_seq;
              if (previous && previous.plan_revision !== null && value.plan_revision !== null
                && value.plan_revision > previous.plan_revision) {
                const old = new Set(previous.nodes.map((n) => n.occurrence_id));
                setFresh(new Set(value.nodes.map((n) => n.occurrence_id).filter((id) => !old.has(id))));
                setTimeout(() => setFresh(new Set()), HIGHLIGHT_MS);
              }
            }
            setGraph(value);
            setError(null);
          }
        } catch (caught) {
          setError(caught instanceof Error ? caught.message : "执行图响应无效");
        }
      }
      if (again.current) { again.current = false; sendGraph(revisionRef.current); }
    });
    const offState = channel?.onStateChange?.((state) => { reset(); if (state === "connected") sendGraph(revisionRef.current); });
    return () => {
      off?.(); offState?.(); clearInterval(tick);
      if (debounce.current) clearTimeout(debounce.current);
      if (timeout.current) clearTimeout(timeout.current);
      graphRequest.current = null; decisionRequest.current = null;
    };
  }, [channel, missionId, sendGraph]);

  // 节点多时默认折叠：不含运行中/验证中节点的复合任务（每个计划版本只自动做一次）。
  // 折叠定下来之前不排版（否则会先把整张大图排一遍再作废）。
  useEffect(() => {
    if (!graph || graph.plan_revision === autoCollapsed.current) return;
    autoCollapsed.current = graph.plan_revision;
    if (graph.nodes.length > AUTO_COLLAPSE_OVER) {
      const busy = new Set<string>();
      const parentOf = new Map(graph.nodes.map((n) => [n.occurrence_id, n.parent]));
      for (const node of graph.nodes) {
        const tone = displayOf(node).tone;
        if (tone !== "running" && tone !== "verifying") continue;
        for (let at = node.parent; at; at = parentOf.get(at) ?? null) busy.add(at);
      }
      // eslint-disable-next-line react-hooks/set-state-in-effect -- once per plan revision, not per render
      setCollapsed(new Set(graph.nodes.filter((n) => n.form === "compound" && n.parent && !busy.has(n.occurrence_id)).map((n) => n.occurrence_id)));
    }
    setCollapseReady(graph.plan_revision);
  }, [graph]);

  // 排版只跟结构走：状态更新（每秒可能一次）换的是新对象，但签名不变，不打断正在进行的排版
  const layoutKey = graph?.source === "htn" && collapseReady === graph.plan_revision
    ? structureKey(graph, collapsed, showData) : null;
  useEffect(() => {
    const current = shown.current;
    if (layoutKey === null || !current || placedKey.current === layoutKey) return;
    let cancelled = false;
    const plan = buildElkGraph(current, collapsed, showData);
    const slowTimer = setTimeout(() => { if (!cancelled) setSlow(true); }, 1000);
    layoutGraph(plan.elk).then((output) => {
      if (cancelled) return;
      placedKey.current = layoutKey;
      setPlaced({ key: layoutKey, nodes: flatten(output, plan.groups), edges: plan.edges });
    }).catch((caught) => {
      if (!cancelled) setError("排版失败：" + (caught instanceof Error ? caught.message : String(caught)));
    }).finally(() => { clearTimeout(slowTimer); if (!cancelled) setSlow(false); });
    return () => { cancelled = true; clearTimeout(slowTimer); };
    // collapsed/showData are part of layoutKey
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutKey]);

  const tasks = useMemo(() => new Map(asList(detail?.tasks).map((t) => [asText(t.id), t])), [detail]);
  const mission = asRecord(detail?.mission);
  const missionTerminal = TERMINAL_MISSION.has(asText(mission.status));
  const byId = useMemo(() => new Map((graph?.nodes ?? []).map((n) => [n.occurrence_id, n])), [graph]);
  const childCount = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of graph?.nodes ?? []) if (node.parent) counts.set(node.parent, (counts.get(node.parent) ?? 0) + 1);
    return counts;
  }, [graph]);
  const titleOf = useCallback((node: LiveNode, index: number): { full: string; model: boolean } => {
    const goal = asRecord(tasks.get(node.task_id)?.goal);
    const text = asText(goal.text ?? tasks.get(node.task_id)?.goal);
    // 2026-09-26 真机：每个子步骤的任务目标都是整个任务目标，看不出这一步做什么——优先用方法步骤的职责
    if (node.step) return { full: stepTitle(node.step, text), model: true };
    if (text) return { full: stepTitle(null, text), model: goal.source === "model" };
    if (!node.parent) {
      const missionGoal = asText(asRecord(mission.goal).text ?? mission.goal);
      if (missionGoal) return { full: missionGoal, model: false };
    }
    return { full: "子任务 " + (index + 1), model: false };
  }, [tasks, mission.goal]);
  const indexOf = useMemo(() => new Map((graph?.nodes ?? []).map((n, i) => [n.occurrence_id, i])), [graph]);

  const toggle = useCallback((id: string) => setCollapsed((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  }), []);
  const pick = useCallback((id: string) => {
    setPicked(id);
    if (byId.get(id)?.form === "compound") sendDecisions();
  }, [byId, sendDecisions]);

  const flowNodes: Node<NodeData>[] = useMemo(() => (placed?.nodes ?? []).filter((p) => byId.has(p.id)).map((p) => {
    const node = byId.get(p.id)!;
    const title = titleOf(node, indexOf.get(p.id) ?? 0);
    return {
      id: p.id, type: "task", position: { x: p.x, y: p.y }, ...(p.parent ? { parentId: p.parent } : {}),
      draggable: false, connectable: false, selectable: false,
      style: { width: p.width, height: p.height }, width: p.width, height: p.height,
      data: { node, title: short(title.full), fullTitle: title.full, modelWritten: title.model,
        stalled: isStalled(node, now, missionTerminal), fresh: fresh.has(p.id), collapsed: collapsed.has(p.id),
        childCount: childCount.get(p.id) ?? 0, group: p.group, onToggle: toggle, onPick: pick },
    };
  }), [placed, byId, titleOf, indexOf, now, missionTerminal, fresh, collapsed, childCount, toggle, pick]);

  const flowEdges: Edge[] = useMemo(() => (placed?.edges ?? []).map((e) => ({
    id: e.kind + ":" + e.source + ">" + e.target, source: e.source, target: e.target, type: "smoothstep",
    style: { stroke: e.kind === "order" ? "#8b5cf6" : "#0284c7", strokeDasharray: e.kind === "data" ? "5 4" : undefined },
    markerEnd: { type: MarkerType.ArrowClosed, color: e.kind === "order" ? "#8b5cf6" : "#0284c7" },
  })), [placed]);

  // 读取超过 8 秒没回：提示并给「重试」，不让人对着"更新中…"干等
  useEffect(() => {
    if (!loading) return undefined;
    const timer = setTimeout(() => setSlowRead(true), 8000);
    return () => { clearTimeout(timer); setSlowRead(false); };
  }, [loading]);
  const retry = useCallback(() => {
    graphRequest.current = null; again.current = false; setLoading(false);
    sendGraph(revisionRef.current);
  }, [sendGraph]);

  const historical = revision !== null;
  const pickedNode = picked ? byId.get(picked) ?? null : null;
  const stalledCount = (graph?.nodes ?? []).filter((n) => isStalled(n, now, missionTerminal)).length;
  useEffect(() => { onStalled?.(stalledCount); }, [onStalled, stalledCount]);

  return (
    <section className="live-graph" aria-label="执行图" data-testid="live-graph">
      <div className="lg-bar">
        <h3>执行图</h3>
        {graph && graph.revisions.length > 1 && (
          <label>计划版本{" "}
            <select aria-label="计划版本" value={revision === null ? "" : String(revision)}
              onChange={(event) => {
                const value = event.target.value === "" ? null : Number(event.target.value);
                setRevision(value); setPicked(null); sendGraph(value);
              }}>
              <option value="">当前</option>
              {graph.revisions.map((r) => <option key={r} value={String(r)}>{"版本 " + r}</option>)}
            </select>
          </label>
        )}
        <label><input type="checkbox" checked={showData} onChange={(e) => setShowData(e.target.checked)} /> 显示数据依赖</label>
        {loading && <span className="lg-muted">更新中…</span>}
        {stalledCount > 0 && <span className="lg-stall">{stalledCount} 个步骤可能卡住</span>}
      </div>
      {historical && <p className="lg-muted" role="status">历史版本，仅看结构；状态以当前为准。</p>}
      {error && <p role="alert" className="lg-error">{error}</p>}
      {slowRead && <p className="lg-muted lg-slow" role="status">读取较慢，可能后台正忙。<button type="button" onClick={retry}>重试</button></p>}
      {(!graph || graph.source === "planning") && !error && <p className="lg-muted" role="status">{notPlannedText(asText(mission.status), graph !== null)}</p>}
      {slow && <p className="lg-muted" role="status">正在排版…</p>}
      {graph?.source === "htn" && (
        <div className={"lg-canvas" + (historical ? " lg-historical" : "")}>
          <ReactFlow nodes={flowNodes} edges={flowEdges} nodeTypes={nodeTypes} fitView minZoom={0.2} maxZoom={1.5}
            nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} proOptions={{ hideAttribution: true }}>
            <Background gap={24} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      )}
      {graph?.source === "htn" && (
        <details className="lg-list">
          <summary>{"全部步骤（" + graph.nodes.length + "）"}</summary>
          <ul>{graph.nodes.map((node, index) => {
            const display = displayOf(node);
            return <li key={node.occurrence_id}>
              <button type="button" onClick={() => pick(node.occurrence_id)}>{short(titleOf(node, index).full)}</button>
              {" · " + display.label}
            </li>;
          })}</ul>
        </details>
      )}
      {pickedNode && (
        <NodePanel node={pickedNode} title={titleOf(pickedNode, indexOf.get(pickedNode.occurrence_id) ?? 0)}
          detail={detail} events={events ?? []} hasMoreEvents={hasMoreEvents} onLoadMoreEvents={onLoadMoreEvents}
          decisions={pickedNode.form === "compound" ? decisions : null} historical={historical}
          onClose={() => setPicked(null)} />
      )}
    </section>
  );
}

/** 还没有执行图时说清楚为什么：按任务状态直接给出，不等后台回复。 */
function notPlannedText(status: string, answered: boolean): string {
  if (status === "CREATED") return "还没开始规划：请先按上面「下一步」的提示确认完成要求，确认后系统开始拆分任务，执行图会自动出现在这里。";
  if (status === "PLANNING" || status === "ACTIVE") return "正在规划，计划生成后这里会自动出现执行图。";
  if (status === "COMPLETED" || status === "FAILED" || status === "CANCELLED") return answered ? "这个任务没有生成拆分计划，所以没有执行图。" : "正在读取执行图…";
  return "正在读取执行图…";
}

function NodePanel({ node, title, detail, events, hasMoreEvents, onLoadMoreEvents, decisions, historical, onClose }: {
  node: LiveNode; title: { full: string; model: boolean }; detail: Record<string, unknown> | null; events: MissionEvent[];
  hasMoreEvents: boolean; onLoadMoreEvents?: () => void; decisions: Record<string, unknown>[] | null; historical: boolean;
  onClose: () => void;
}) {
  const display = displayOf(node);
  const reason = readinessLabel(node.readiness_reason);
  const attempts = asList(detail?.attempts).filter((a) => a.task_id === node.task_id);
  const layers = asList(detail?.results).filter((r) => r.task_id === node.task_id)
    .flatMap((r) => asList(r.verification_layers));
  const mine = events.filter((e) => e.task_id === node.task_id).slice(-30).reverse();
  return (
    <aside className="lg-panel" aria-label="步骤详情" data-testid="lg-panel">
      <div className="lg-bar">
        <strong>{title.full}</strong>
        {title.model && <span className="lg-muted">（模型生成）</span>}
        <button type="button" onClick={onClose} aria-label="关闭详情">关闭</button>
      </div>
      <p>
        <span className="lg-dot" style={{ background: TONE_COLOR[display.tone] }} /> {historical ? "历史版本（状态以当前为准）" : display.label}
        {reason && <span className="lg-muted">{" · " + reason}</span>}
        {node.method && <span className="lg-muted">{" · 拆分方法 " + node.method}</span>}
      </p>
      {node.step && node.step.evidence.length > 0 && <>
        <h4>这一步要交付</h4>
        <ul>{node.step.evidence.map((text, index) => <li key={index}>{text}</li>)}</ul>
      </>}
      <h4>{"尝试（" + attempts.length + "）"}</h4>
      {attempts.length === 0 ? <p className="lg-muted">还没有尝试。</p> : (
        <ul>{attempts.map((a) => <li key={asText(a.id)}>
          {"第 " + (Number(a.ordinal) + 1 || 1) + " 次 · " + asText(a.status) + (a.model ? " · " + asText(a.model) : "")}
          {a.failure ? <span className="lg-muted">{" · " + asText(a.failure)}</span> : null}
        </li>)}</ul>
      )}
      {layers.length > 0 && <>
        <h4>验证</h4>
        <ul>{layers.map((layer, index) => <li key={index}>
          {asText(layer.layer) + "：" + asText(layer.status)}
          {asText(asRecord(layer.summary).text ?? layer.summary) && <span className="lg-muted">{" · " + asText(asRecord(layer.summary).text ?? layer.summary)}</span>}
        </li>)}</ul>
      </>}
      {decisions && <>
        <h4>{"规划决定（" + decisions.length + "）"}</h4>
        {decisions.length === 0 ? <p className="lg-muted">没有记录。</p> : (
          <ul>{decisions.map((d) => {
            const codes = Array.isArray(d.rejection_codes) ? d.rejection_codes.map(asText) : [];
            return <li key={asText(d.decision_id)}>
              {clock(d.created_at) + " " + (DECISION_TYPE[asText(d.decision_type)] ?? (asText(d.decision_type) || "未解析"))
                + " · " + (DECISION_STATUS[asText(d.status)] ?? asText(d.status))}
              {codes.length > 0 && <span className="lg-muted">{" · 原因：" + codes.join("、")}</span>}
            </li>;
          })}</ul>
        )}
      </>}
      <h4>事件</h4>
      {mine.length === 0 ? <p className="lg-muted">已加载的事件里没有这个步骤的记录。</p> : (
        <ul>{mine.map((e) => <li key={e.seq}>{clock(e.created_at) + " " + e.type}</li>)}</ul>
      )}
      {hasMoreEvents && onLoadMoreEvents && <button type="button" onClick={onLoadMoreEvents}>加载更多事件</button>}
    </aside>
  );
}
