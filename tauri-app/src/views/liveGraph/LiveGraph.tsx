// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 执行图（NEXT-TG-1.0 §8.6）：计划结构 + 实际执行过程画在一张图上。
 *
 * - 数据：SDK 正式只读接口 `taskgraph.execution_snapshot`（严格执行图 + 同一次读取的执行过程），
 *   点节点再读 `taskgraph.execution_detail`（这一回合模型实际说了什么、调了哪些工具、交了什么、
 *   审阅理由）。Host 不直读 SDK 表。
 * - 每个步骤框里是它的"执行 → 审阅 → 修补请求 → 规划 → 再执行"链，失败回路画出来；旧的失败不被新成功覆盖。
 * - 实时：本任务的 `mission_changed` 到了就 800ms 防抖重读（分页读完再换画面）；读取在途再来推送只合并
 *   成一次后续读取。执行过程与步骤状态都没变就不换画面；节点集合不变不重新排版。
 * - 第二个标签「时间线」：同一份执行过程按时间排开，点开看同样的回合详情。
 * - 读不到时保留旧画面并标明"可能已过期"；未启用/启用中的任务说清楚原因，不伪造图。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { asList, asRecord, asText, newRequestKey, useMissionsStore, type MissionsChannel } from "../../stores/missionsStore";
import {
  DECISION_STATUS, DECISION_TYPE, STALL_SECONDS, TONE_COLOR, cleanText, execDisplay, mergePages, parseExecutionPage,
  phaseDisplay, readinessLabel, stepProgress, stepTitle,
  type ExecNode, type ExecutionPage, type ExecutionView, type StructureNode,
} from "./model";
import { AUTO_COLLAPSE_OVER, buildElkGraph, flatten, homesOf, structureKey, type DrawEdge, type Placed } from "./layoutModel";
import { layoutGraph } from "./layout";
import "./LiveGraph.css";

type Props = {
  missionId: string;
  channel: MissionsChannel | null;
  /** mission_get 的投影（tasks/mission），用来给没有方法步骤名的节点起标题。 */
  detail: Record<string, unknown> | null;
  onLoadMoreEvents?: () => void;
  /** "可能卡住"的执行数变化时告诉外面（任务列表行显示提示）。 */
  onStalled?: (count: number) => void;
};

const DEBOUNCE_MS = 800;
const TIMEOUT_MS = 30000;
const MAX_PAGES = 20;
const TITLE_LIMIT = 40;
const SNAPSHOT_TYPE = "taskgraph.execution_snapshot";
const DETAIL_TYPE = "taskgraph.execution_detail";
const ICON: Record<ExecNode["kind"], string> = {
  attempt: "▶", check: "✔", review: "⚖", planning: "🧭", repair_request: "🔧", plan_revision: "📋", operation: "📤",
};

function short(value: string, limit = TITLE_LIMIT): string {
  return value.length > limit ? value.slice(0, limit - 1) + "…" : value;
}
function clock(ms: number | null): string {
  if (!ms) return "";
  const d = new Date(ms);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((v) => String(v).padStart(2, "0")).join(":");
}

type StepData = {
  step: StructureNode; title: string; progress: string; attempts: number; collapsed: boolean; childCount: number;
  group: boolean; foldable: boolean; onToggle: (id: string) => void; onPick: (id: string) => void;
};
type ExecData = { node: ExecNode; onPick: (id: string) => void; picked: boolean };

function StepCard({ data }: NodeProps<Node<StepData>>) {
  const { step, title, progress, attempts, collapsed, childCount, group, foldable } = data;
  const display = phaseDisplay(step.phase);
  const color = TONE_COLOR[display.tone];
  const reason = readinessLabel(step.readiness);
  const waiting = display.tone === "idle" || display.tone === "ready" || display.tone === "person";
  return (
    <div className={"lg-node" + (group ? " lg-group" : "") + (display.tone === "running" ? " lg-running" : "")
      + (display.tone === "cancelled" ? " lg-cancelled" : "")} style={{ borderColor: color }}
      data-testid={"lg-node-" + step.occurrence_id}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <div className="lg-head">
        {foldable && <button type="button" className="lg-fold" aria-expanded={!collapsed}
          aria-label={(collapsed ? "展开" : "折叠") + "：" + title}
          onClick={(event) => { event.stopPropagation(); data.onToggle(step.occurrence_id); }}>{collapsed ? "▸" : "▾"}</button>}
        <button type="button" className="lg-title" title={title} onClick={() => data.onPick(step.occurrence_id)}>{short(title)}</button>
      </div>
      <div className="lg-meta">
        <span className="lg-dot" style={{ background: color }} />
        <span>{display.label}</span>
        {waiting && reason && step.readiness !== "READY_CANDIDATE" && <span className="lg-muted">· {reason}</span>}
        {attempts > 1 && <span>· 执行 {attempts} 次</span>}
        {collapsed && childCount > 0 && <span>· 已折叠 {childCount} 项</span>}
      </div>
      {progress && !group && <div className="lg-line" title={progress}>{progress}</div>}
      {progress && group && <div className="lg-line lg-muted" title={progress}>{short(progress, 60)}</div>}
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

function ExecCard({ data }: NodeProps<Node<ExecData>>) {
  const { node, picked } = data;
  const { title, status } = execDisplay(node);
  const color = TONE_COLOR[status.tone];
  const line = node.summary ? cleanText(node.summary.text) : "";
  return (
    <div className={"lg-exec" + (status.tone === "running" ? " lg-running" : "") + (picked ? " lg-picked" : "")}
      style={{ borderColor: color }} data-testid={"lg-exec-" + node.node_id}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <button type="button" className="lg-title" title={title + (line ? "：" + line : "")} onClick={() => data.onPick(node.node_id)}>
        <span aria-hidden="true">{ICON[node.kind]} </span>{title}
        <span className="lg-exec-status" style={{ color }}>{" · " + status.label}</span>
      </button>
      {line && <div className="lg-line" title={line}>{line}</div>}
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

const nodeTypes = { step: StepCard, exec: ExecCard };
const EDGE_COLOR: Record<DrawEdge["kind"], string> = { order: "#8b5cf6", data: "#0284c7", process: "#64748b", rework: "#ef4444" };

type Pending = { id: string; pages: ExecutionPage[]; restarted: boolean };

function errorText(code: string, detail: string): string {
  if (code === "NOT_ENABLED") return "这个任务创建于执行图启用之前，没有执行图。";
  if (code === "ACTIVATION_PENDING") return "执行图正在启用：等规划授权发出后由系统启用，稍后自动出现。";
  return detail || "执行图读取失败，请稍后重试";
}

export function LiveGraph({ missionId, channel, detail, onLoadMoreEvents, onStalled }: Props) {
  const [view, setView] = useState<ExecutionView | null>(null);
  const [error, setError] = useState<{ code: string; text: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<"graph" | "timeline">("graph");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [showData, setShowData] = useState(false);
  const [showProcess, setShowProcess] = useState(true);
  const [placed, setPlaced] = useState<{ key: string; nodes: Placed[]; edges: DrawEdge[] } | null>(null);
  const [slow, setSlow] = useState(false);
  const [slowRead, setSlowRead] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const request = useRef<Pending | null>(null);
  const again = useRef(false);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shown = useRef<ExecutionView | null>(null);
  const signature = useRef<string | null>(null);
  const autoCollapsed = useRef<number | null>(null);
  const [collapseReady, setCollapseReady] = useState<number | null>(null);
  const placedKey = useRef<string | null>(null);
  useEffect(() => { shown.current = view; }, [view]);

  const events = useMissionsStore((state) => state.events[missionId]);

  const sendPage = useCallback((cursor: string | null, pending: Pending | null) => {
    if (!channel) return;
    const id = newRequestKey();
    request.current = { id, pages: pending?.pages ?? [], restarted: pending?.restarted ?? false };
    setLoading(true);
    const payload: Record<string, unknown> = { mission_id: missionId };
    if (cursor) payload.cursor = cursor;
    if (!channel.send({ type: SNAPSHOT_TYPE, request_id: id, payload })) {
      request.current = null; setLoading(false); setError({ code: "offline", text: "连接不可用，请求未发送" }); return;
    }
    if (timeout.current) clearTimeout(timeout.current);
    timeout.current = setTimeout(() => {
      if (request.current?.id !== id) return;
      request.current = null; setLoading(false); setError({ code: "timeout", text: "读取超时，已保留上次画面" });
    }, TIMEOUT_MS);
  }, [channel, missionId]);

  const refresh = useCallback(() => {
    if (request.current) { again.current = true; return; }
    sendPage(null, null);
  }, [sendPage]);

  useEffect(() => {
    refresh();
    const tick = setInterval(() => setNow(Date.now() / 1000), 30000);
    const reset = () => {
      request.current = null; again.current = false; placedKey.current = null; autoCollapsed.current = null;
      signature.current = null;
      setView(null); setPlaced(null); setCollapseReady(null); setPicked(null); setLoading(false);
    };
    const off = channel?.onMessage((raw) => {
      const message = raw as unknown as { type?: string; payload?: unknown };
      const body = asRecord(message.payload);
      const type = message.type ?? "";
      if (["companion_identity_unready", "companion_control_rechallenge", "companion_profile_unbound"].includes(type)
        || (type === "companion_identity_status" && body.ready === false)) { reset(); return; }
      if (type === "mission_changed") {
        if (body.mission_id !== missionId) return;
        if (debounce.current) clearTimeout(debounce.current);
        debounce.current = setTimeout(() => { debounce.current = null; refresh(); }, DEBOUNCE_MS);
        return;
      }
      if (type !== SNAPSHOT_TYPE + "_response") return;
      const pending = request.current;
      if (!pending || body.request_id !== pending.id) return;
      request.current = null;
      if (timeout.current) clearTimeout(timeout.current);
      if (body.ok !== true) {
        const code = asText(body.error_code);
        if (code === "SNAPSHOT_CHANGED" && !pending.restarted) { sendPage(null, { id: "", pages: [], restarted: true }); return; }
        setLoading(false);
        setError({ code, text: errorText(code, asText(body.error)) });
      } else {
        try {
          const page = parseExecutionPage(body.data, missionId);
          const pages = [...pending.pages, page];
          if (!page.complete && page.next_cursor && pages.length < MAX_PAGES) {
            sendPage(page.next_cursor, { ...pending, pages });
            return;
          }
          setLoading(false);
          const merged = mergePages(pages);
          const sig = merged.execution_hash + "|" + merged.structure.map((n) => n.occurrence_id + ":" + n.phase + ":" + n.readiness).join(",");
          if (sig !== signature.current) { signature.current = sig; setView(merged); }
          setError(pages.length >= MAX_PAGES && !page.complete ? { code: "partial", text: "执行过程太长，只显示了前 " + MAX_PAGES + " 页" } : null);
        } catch (caught) {
          setLoading(false);
          setError({ code: "invalid", text: caught instanceof Error ? caught.message : "执行图响应无效" });
        }
      }
      if (again.current) { again.current = false; refresh(); }
    });
    const offState = channel?.onStateChange?.((state) => { reset(); if (state === "connected") refresh(); });
    return () => {
      off?.(); offState?.(); clearInterval(tick);
      if (debounce.current) clearTimeout(debounce.current);
      if (timeout.current) clearTimeout(timeout.current);
      request.current = null;
    };
  }, [channel, missionId, refresh, sendPage]);

  // 节点多时默认折叠：不含执行中/验收中步骤的复合步骤（每个计划版本只自动做一次）
  useEffect(() => {
    if (!view || view.plan_revision === autoCollapsed.current) return;
    autoCollapsed.current = view.plan_revision;
    if (view.structure.length + view.nodes.length > AUTO_COLLAPSE_OVER) {
      const busy = new Set<string>();
      const parentOf = new Map(view.structure.map((n) => [n.occurrence_id, n.parent]));
      for (const node of view.structure) {
        const tone = phaseDisplay(node.phase).tone;
        if (tone !== "running" && tone !== "verifying") continue;
        for (let at = node.parent; at; at = parentOf.get(at) ?? null) busy.add(at);
      }
      // eslint-disable-next-line react-hooks/set-state-in-effect -- once per plan revision, not per render
      setCollapsed(new Set(view.structure.filter((n) => n.form === "compound" && n.parent && !busy.has(n.occurrence_id)).map((n) => n.occurrence_id)));
    }
    setCollapseReady(view.plan_revision);
  }, [view]);

  const layoutKey = view && collapseReady === view.plan_revision ? structureKey(view, collapsed, showData, showProcess) : null;
  useEffect(() => {
    const current = shown.current;
    if (tab !== "graph" || layoutKey === null || !current || placedKey.current === layoutKey) return;
    let cancelled = false;
    const plan = buildElkGraph(current, collapsed, showData, showProcess);
    const slowTimer = setTimeout(() => { if (!cancelled) setSlow(true); }, 1000);
    layoutGraph(plan.elk).then((output) => {
      if (cancelled) return;
      placedKey.current = layoutKey;
      setPlaced({ key: layoutKey, nodes: flatten(output, plan.groups), edges: plan.edges });
    }).catch((caught) => {
      if (!cancelled) setError({ code: "layout", text: "排版失败：" + (caught instanceof Error ? caught.message : String(caught)) });
    }).finally(() => { clearTimeout(slowTimer); if (!cancelled) setSlow(false); });
    return () => { cancelled = true; clearTimeout(slowTimer); };
    // collapsed/showData/showProcess are part of layoutKey
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutKey, tab]);

  const tasks = useMemo(() => new Map(asList(detail?.tasks).map((t) => [asText(t.id), t])), [detail]);
  const mission = asRecord(detail?.mission);
  const structureById = useMemo(() => new Map((view?.structure ?? []).map((n) => [n.occurrence_id, n])), [view]);
  const execById = useMemo(() => new Map((view?.nodes ?? []).map((n) => [n.node_id, n])), [view]);
  const homes = useMemo(() => (view ? homesOf(view) : new Map<string, string>()), [view]);
  const attemptsOf = useMemo(() => {
    const out = new Map<string, ExecNode[]>();
    for (const node of view?.nodes ?? []) {
      if (node.kind !== "attempt") continue;
      const home = String(node.raw.occurrence_id ?? "");
      out.set(home, [...(out.get(home) ?? []), node]);
    }
    return out;
  }, [view]);
  const childCount = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of view?.structure ?? []) if (node.parent) counts.set(node.parent, (counts.get(node.parent) ?? 0) + 1);
    if (showProcess) for (const home of homes.values()) counts.set(home, (counts.get(home) ?? 0) + 1);
    return counts;
  }, [view, homes, showProcess]);
  const titleOf = useCallback((node: StructureNode): string => {
    const goal = asRecord(tasks.get(node.task_id)?.goal);
    const text = asText(goal.text ?? tasks.get(node.task_id)?.goal);
    if (node.step) return stepTitle(node.step, text);
    if (!node.parent) {
      const missionGoal = asText(asRecord(mission.goal).text ?? mission.goal);
      if (missionGoal) return missionGoal;
    }
    return text ? stepTitle(null, text) : "步骤";
  }, [tasks, mission.goal]);

  const toggle = useCallback((id: string) => setCollapsed((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  }), []);
  const pick = useCallback((id: string) => setPicked((current) => current === id ? null : id), []);

  const flowNodes: Node[] = useMemo(() => (placed?.nodes ?? []).flatMap((p): Node[] => {
    const base = { id: p.id, position: { x: p.x, y: p.y }, ...(p.parent ? { parentId: p.parent } : {}),
      draggable: false, connectable: false, selectable: false, style: { width: p.width, height: p.height },
      width: p.width, height: p.height };
    const step = structureById.get(p.id);
    if (step) {
      return [{ ...base, type: "step", data: { step, title: titleOf(step), progress: stepProgress(step, attemptsOf.get(step.occurrence_id) ?? []),
        attempts: (attemptsOf.get(step.occurrence_id) ?? []).length, collapsed: collapsed.has(p.id),
        childCount: childCount.get(p.id) ?? 0, group: p.group,
        foldable: step.form === "compound" && (childCount.get(p.id) ?? 0) > 0, onToggle: toggle, onPick: pick } satisfies StepData }];
    }
    const exec = execById.get(p.id);
    return exec ? [{ ...base, type: "exec", data: { node: exec, onPick: pick, picked: picked === exec.node_id } satisfies ExecData }] : [];
  }), [placed, structureById, execById, titleOf, attemptsOf, collapsed, childCount, toggle, pick, picked]);

  const flowEdges: Edge[] = useMemo(() => (placed?.edges ?? []).map((e) => ({
    id: e.kind + ":" + e.source + ">" + e.target, source: e.source, target: e.target, type: "smoothstep",
    label: e.kind === "rework" ? "再执行" : undefined,
    style: { stroke: EDGE_COLOR[e.kind], strokeDasharray: e.kind === "data" || e.kind === "process" ? "5 4" : undefined,
      strokeWidth: e.kind === "rework" ? 2 : 1 },
    markerEnd: { type: MarkerType.ArrowClosed, color: EDGE_COLOR[e.kind] },
  })), [placed]);

  useEffect(() => {
    if (!loading) return undefined;
    const timer = setTimeout(() => setSlowRead(true), 8000);
    return () => { clearTimeout(timer); setSlowRead(false); };
  }, [loading]);
  const retry = useCallback(() => { request.current = null; again.current = false; setLoading(false); refresh(); }, [refresh]);

  // 执行中、且这一步最后一条事件超过 10 分钟：只是提示，不改状态
  const stalledCount = useMemo(() => {
    const lastAt = new Map<string, number>();
    for (const event of events ?? []) if (event.task_id) lastAt.set(event.task_id, Math.max(lastAt.get(event.task_id) ?? 0, Number(event.created_at) || 0));
    return (view?.nodes ?? []).filter((n) => n.kind === "attempt" && ["RUNNING", "CLAIMED", "SUBMITTED"].includes(String(n.raw.status))
      && now - (lastAt.get(String(n.raw.task_id)) ?? now) > STALL_SECONDS).length;
  }, [view, events, now]);
  useEffect(() => { onStalled?.(stalledCount); }, [onStalled, stalledCount]);

  const pickedStep = picked ? structureById.get(picked) ?? null : null;
  const pickedExec = picked ? execById.get(picked) ?? null : null;
  const stale = error !== null && view !== null;
  const timeline = useMemo(() => [...(view?.nodes ?? [])].sort((a, b) => (a.at_ms ?? 0) - (b.at_ms ?? 0) || a.node_id.localeCompare(b.node_id)), [view]);

  return (
    <section className="live-graph" aria-label="执行图" data-testid="live-graph">
      <div className="lg-bar">
        <div role="tablist" aria-label="执行图视图" className="lg-tabs">
          {([["graph", "执行图"], ["timeline", "时间线"]] as const).map(([key, label]) => (
            <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{label}</button>
          ))}
        </div>
        {tab === "graph" && <>
          <label><input type="checkbox" checked={showProcess} onChange={(e) => setShowProcess(e.target.checked)} /> 显示执行过程</label>
          <label><input type="checkbox" checked={showData} onChange={(e) => setShowData(e.target.checked)} /> 显示数据依赖</label>
        </>}
        {loading && <span className="lg-muted">更新中…</span>}
        {view?.coverage === "PENDING_IMPORT" && <span className="lg-muted">有模型回合正在进行</span>}
        {stalledCount > 0 && <span className="lg-stall">{stalledCount} 次执行可能卡住</span>}
      </div>
      {error && <p role="alert" className={error.code === "ACTIVATION_PENDING" || error.code === "NOT_ENABLED" ? "lg-muted" : "lg-error"}>
        {error.text}{stale ? "（下面是上次读到的画面，可能已过期）" : ""}</p>}
      {slowRead && <p className="lg-muted lg-slow" role="status">读取较慢，可能后台正忙。<button type="button" onClick={retry}>重试</button></p>}
      {!view && !error && <p className="lg-muted" role="status">正在读取执行图…</p>}
      {slow && tab === "graph" && <p className="lg-muted" role="status">正在排版…</p>}
      {view && tab === "graph" && (
        <div className={"lg-canvas" + (stale ? " lg-historical" : "")}>
          <ReactFlow nodes={flowNodes} edges={flowEdges} nodeTypes={nodeTypes} fitView minZoom={0.2} maxZoom={1.5}
            nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} proOptions={{ hideAttribution: true }}>
            <Background gap={24} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      )}
      {view && tab === "graph" && (
        <details className="lg-list">
          <summary>{"全部步骤（" + view.structure.length + "）"}</summary>
          <ul>{view.structure.map((node) => (
            <li key={node.occurrence_id}>
              <button type="button" onClick={() => pick(node.occurrence_id)}>{short(titleOf(node))}</button>
              {" · " + phaseDisplay(node.phase).label}
            </li>
          ))}</ul>
        </details>
      )}
      {view && tab === "timeline" && (
        <ol className="lg-timeline" aria-label="执行过程时间线">
          {timeline.length === 0 && <li className="lg-muted">还没有执行过程。</li>}
          {timeline.map((node) => {
            const { title, status } = execDisplay(node);
            const home = structureById.get(homes.get(node.node_id) ?? "");
            return (
              <li key={node.node_id}>
                <button type="button" className="lg-tl-row" aria-expanded={picked === node.node_id} onClick={() => pick(node.node_id)}>
                  <span className="lg-muted">{clock(node.at_ms)}</span>{" "}
                  <span className="lg-dot" style={{ background: TONE_COLOR[status.tone] }} /> {title + " · " + status.label}
                  {home && home.parent && <span className="lg-muted">{" · " + short(titleOf(home), 30)}</span>}
                  {node.summary && <span className="lg-line">{cleanText(node.summary.text)}</span>}
                </button>
                {picked === node.node_id && <ExecDetail missionId={missionId} channel={channel} node={node} />}
              </li>
            );
          })}
        </ol>
      )}
      {tab === "graph" && pickedStep && (
        <StepPanel step={pickedStep} title={titleOf(pickedStep)} execs={(view?.nodes ?? []).filter((n) => homes.get(n.node_id) === pickedStep.occurrence_id)}
          onPick={pick} onClose={() => setPicked(null)} onLoadMoreEvents={onLoadMoreEvents} />
      )}
      {tab === "graph" && pickedExec && (
        <aside className="lg-panel" aria-label="执行详情" data-testid="lg-panel">
          <div className="lg-bar">
            <strong>{ICON[pickedExec.kind] + " " + execDisplay(pickedExec).title}</strong>
            <button type="button" onClick={() => setPicked(null)} aria-label="关闭详情">关闭</button>
          </div>
          <ExecDetail missionId={missionId} channel={channel} node={pickedExec} />
        </aside>
      )}
    </section>
  );
}

function StepPanel({ step, title, execs, onPick, onClose, onLoadMoreEvents }: {
  step: StructureNode; title: string; execs: ExecNode[]; onPick: (id: string) => void; onClose: () => void;
  onLoadMoreEvents?: () => void;
}) {
  const display = phaseDisplay(step.phase);
  const reason = readinessLabel(step.readiness);
  return (
    <aside className="lg-panel" aria-label="步骤详情" data-testid="lg-panel">
      <div className="lg-bar">
        <strong>{title}</strong>
        <button type="button" onClick={onClose} aria-label="关闭详情">关闭</button>
      </div>
      <p>
        <span className="lg-dot" style={{ background: TONE_COLOR[display.tone] }} /> {display.label}
        {reason && <span className="lg-muted">{" · " + reason}</span>}
      </p>
      {step.step && step.step.evidence.length > 0 && <>
        <h4>这一步要交付</h4>
        <ul>{step.step.evidence.map((text, index) => <li key={index}>{text}</li>)}</ul>
      </>}
      <h4>{"执行过程（" + execs.length + "）"}</h4>
      {execs.length === 0 ? <p className="lg-muted">还没有执行。</p> : (
        <ul>{execs.map((node) => {
          const { title: name, status } = execDisplay(node);
          return <li key={node.node_id}>
            <button type="button" className="lg-link" onClick={() => onPick(node.node_id)}>{clock(node.at_ms) + " " + name}</button>
            {" · " + status.label}
            {node.summary && <span className="lg-muted">{" · " + short(cleanText(node.summary.text), 80)}</span>}
          </li>;
        })}</ul>
      )}
      {onLoadMoreEvents && <p className="lg-muted">原始事件在页面下方「原始事件记录」。</p>}
    </aside>
  );
}

type DetailItem = Record<string, unknown> & { t: string };

/** 一个执行节点的回合详情：SDK 白名单过的模型原话、工具调用、提交与审阅理由。 */
function ExecDetail({ missionId, channel, node }: { missionId: string; channel: MissionsChannel | null; node: ExecNode }) {
  const [body, setBody] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<string | null>(null);
  const turnState = node.turn?.state ?? "";
  useEffect(() => {
    if (!channel) return undefined;
    const id = newRequestKey();
    pending.current = id;
    const off = channel.onMessage((raw) => {
      const message = raw as unknown as { type?: string; payload?: unknown };
      const payload = asRecord(message.payload);
      if (message.type !== DETAIL_TYPE + "_response" || payload.request_id !== pending.current) return;
      pending.current = null;
      if (payload.ok === true) { setBody(asRecord(payload.data)); setError(null); }
      else setError(asText(payload.error) || "读取失败");
    });
    if (!channel.send({ type: DETAIL_TYPE, request_id: id, payload: { mission_id: missionId, node_id: node.node_id } })) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- a send failure is reported once
      setError("连接不可用，请求未发送");
    }
    return () => { off?.(); pending.current = null; };
  }, [channel, missionId, node.node_id, turnState]);

  const raw = node.raw;
  const facts: string[] = [];
  if (node.kind === "check") {
    for (const layer of asList(raw.layers)) facts.push(asText(layer.layer) + "：" + asText(layer.status) + (layer.summary ? " · " + asText(layer.summary) : ""));
  }
  if (node.kind === "planning") {
    for (const d of asList(raw.decisions)) {
      const codes = Array.isArray(d.rejection_codes) ? d.rejection_codes.map(String) : [];
      facts.push((DECISION_TYPE[asText(d.decision_type)] ?? (asText(d.decision_type) || "未解析")) + " · "
        + (DECISION_STATUS[asText(d.status)] ?? asText(d.status)) + (codes.length ? " · 原因：" + codes.join("、") : ""));
    }
  }
  if (node.kind === "repair_request" && Array.isArray(raw.trigger_refs)) facts.push("由 " + (raw.source_event_type ? asText(raw.source_event_type) : "失败") + " 触发");
  const turn = asRecord(body?.turn);
  const items = asList(body?.items) as DetailItem[];
  return (
    <div className="lg-detail" data-testid={"lg-detail-" + node.node_id}>
      {node.summary && <p>{cleanText(node.summary.text)}</p>}
      {facts.length > 0 && <ul>{facts.map((fact, index) => <li key={index}>{fact}</li>)}</ul>}
      {node.turn && <p className="lg-muted">{"模型回合" + (node.turn.model ? "（" + node.turn.model + "）" : "")
        + (turn.coverage === "PENDING_IMPORT" ? " · 进行中，内容会继续增加" : turn.coverage === "SOURCE_UNAVAILABLE" ? " · 回合记录暂不可读" : "")}</p>}
      {error && <p role="alert" className="lg-error">{error}</p>}
      {node.turn && !body && !error && <p className="lg-muted">正在读取这一回合…</p>}
      {items.length > 0 && <ol className="lg-items">{items.map((item, index) => <li key={index}><TurnItem item={item} /></li>)}</ol>}
      {Number(body?.hidden_items) > 0 && <p className="lg-muted">{"更早的 " + Number(body?.hidden_items) + " 条已省略"}</p>}
    </div>
  );
}

function TurnItem({ item }: { item: DetailItem }) {
  switch (item.t) {
    case "say": return <span>💬 {cleanText(asText(item.text))}</span>;
    case "feedback": return <span>↩ 系统反馈：{cleanText(asText(item.text))}</span>;
    case "submit": return <span>📨 提交{item.outcome ? "（" + asText(item.outcome) + "）" : ""}：{cleanText(asText(item.text))}</span>;
    case "decision": return <span>🧭 决定「{DECISION_TYPE[asText(item.decision_type)] ?? asText(item.decision_type)}」：{cleanText(asText(item.text))}</span>;
    case "method": return <span>🧩 设计步骤 {(Array.isArray(item.steps) ? item.steps.map(String) : []).join(" → ")}：{cleanText(asText(item.text))}</span>;
    case "verdict": return <span>⚖ 结论 {asText(item.verdict)}
      {asList(item.reasons).length > 0 && <ul>{asList(item.reasons).map((r, i) => <li key={i}>{(asText(r.verdict) ? "[" + asText(r.verdict) + "] " : "") + cleanText(asText(r.text))}</li>)}</ul>}
    </span>;
    case "tool": {
      const parts = [asText(item.path), item.bytes != null ? item.bytes + " 字节" : "", item.chars != null ? item.chars + " 字" : "",
        item.file_count != null ? item.file_count + " 个文件" : "", item.found != null ? "找到 " + item.found : "",
        item.passed != null ? (item.passed ? "测试通过" : "测试未通过") : "", asText(item.tail)].filter(Boolean);
      return <span>{item.ok ? "🔧" : "⚠"} {asText(item.tool)}{parts.length ? "：" + parts.join(" · ") : ""}
        {!item.ok && item.error ? <span className="lg-error">{" · " + asText(item.error)}</span> : null}
        {Number(item.count) > 1 ? <span className="lg-muted">{" ×" + Number(item.count)}</span> : null}</span>;
    }
    default: return null;
  }
}
