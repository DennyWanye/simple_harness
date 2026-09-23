// SPDX-License-Identifier: BUSL-1.1
import { useEffect, useId, useMemo, useReducer, useRef, useState } from "react";
import { asRecord, newRequestKey, type MissionsChannel } from "../stores/missionsStore";
import { emptyGraphState, graphReducer, graphErrorMessage, parseSnapshot, parseExplanation, parseDiff, parseConvergence,
  type GraphSnapshot, type GraphNode } from "../stores/taskgraphStore";
import "./MissionTaskGraph.css";

type Props = { missionId: string; channel: MissionsChannel | null };
type Pending = { id: string; kind: string; revision: number | null; occurrence?: string;
  from?: number; to?: number; changed: boolean };
const channelIds = new WeakMap<object, number>();
let lastChannelId = 0;
const labels: Record<string, string> = {
  NOT_SELECTED: "未选入执行", NEEDS_REFINEMENT: "等待细化", WAITING_ORDER: "等待前序完成",
  WAITING_DATA: "等待输入", WAITING_EVIDENCE: "等待证据", WAITING_APPROVAL: "等待授权",
  STALE_BINDING: "绑定已变化", READY_CANDIDATE: "可供调度", WAITING_OPERATION_UNKNOWN: "等待效果核对",
  OBSERVER_UNAVAILABLE: "来源不可用", GRAPH_INTEGRITY: "结构待修复", VALIDITY_RECHECK_PENDING: "等待有效性复核",
};
const completionLabels: Record<string, string> = {
  COMPLETED: "已完成正式验收", AWAITING_INITIAL_PLAN: "等待初始计划",
  AWAITING_COMPLETION_MAPPING: "等待确认完成要求", WAITING_EFFECT: "等待实际效果",
  AWAITING_OUTCOME_REVIEW: "已执行，等待效果验收", RECONCILIATION_REQUIRED: "效果未知，等待核对",
  WAITING_FORMAL_CONTENT_REVIEW: "准备完成，等待正式内容验收",
};
const nodeLabel = (node: GraphNode) => completionLabels[node.phase] || labels[node.readiness] || node.readiness;
const colors = { refinement: "#64748b", order: "#8b5cf6", data: "#0284c7" };

export function MissionTaskGraph(props: Props) {
  if (props.channel && !channelIds.has(props.channel)) channelIds.set(props.channel, ++lastChannelId);
  return <GraphSession key={props.missionId + ":" + (props.channel ? channelIds.get(props.channel) : "none")} {...props} />;
}

function Network({ snapshot, mode, inspect, disabled }: { snapshot: GraphSnapshot; mode: "hierarchy" | "execution";
  inspect: (node: GraphNode) => void; disabled: boolean }) {
  const marker = useId().replace(/:/g, "");
  const points = useMemo(() => new Map(snapshot.nodes.map((node, index) =>
    [node.occurrence_id, { x: 30 + Math.floor(index / 12) * 270, y: 35 + (index % 12) * 105 }])),
  [snapshot]);
  const edges = snapshot.edges.filter(edge => mode === "hierarchy" ? edge.kind === "refinement" : edge.kind !== "refinement");
  const width = Math.max(550, Math.ceil(snapshot.nodes.length / 12) * 270 + 50);
  const height = Math.max(140, Math.min(12, snapshot.nodes.length) * 105 + 45);
  return <div className="tg-canvas" role="region" aria-label={mode === "hierarchy" ? "任务层级关系" : "任务执行依赖"} tabIndex={0}>
    <svg width={width} height={height} role="img" aria-label={"完整执行图，共 " + snapshot.nodes.length + " 个节点"}>
      <defs>{Object.entries(colors).map(([kind, color]) =>
        <marker key={kind} id={marker + kind} markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
          <path d="M0,0 L0,6 L7,3 z" fill={color} />
        </marker>)}</defs>
      {edges.map(edge => {
        const from = points.get(edge.source)!, to = points.get(edge.target)!;
        return <path key={edge.kind + ":" + edge.identity}
          d={"M " + (from.x + 210) + " " + (from.y + 30) + " C " + (from.x + 250) + " " + (from.y + 30) +
            ", " + (to.x - 25) + " " + (to.y + 30) + ", " + to.x + " " + (to.y + 30)}
          fill="none" stroke={colors[edge.kind]} strokeWidth="1.5" markerEnd={"url(#" + marker + edge.kind + ")"}>
          <title>{edge.kind + ": " + edge.source + " → " + edge.target}</title>
        </path>;
      })}
      {snapshot.nodes.map(node => {
        const point = points.get(node.occurrence_id)!;
        return <g key={node.occurrence_id} transform={"translate(" + point.x + "," + point.y + ")"}
          role="button" aria-disabled={disabled} tabIndex={disabled ? -1 : 0}
          aria-label={node.task_id + "：" + nodeLabel(node)}
          onClick={() => { if (!disabled) inspect(node); }}
          onKeyDown={event => { if (!disabled && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); inspect(node); } }}>
          <title>{node.task_id + "\n" + node.occurrence_id + "\n" + node.phase}</title>
          <rect width="210" height="68" rx={node.form === "compound" ? 4 : 12} fill="var(--tg-node)"
            stroke={snapshot.execution_frontier.includes(node.occurrence_id) ? "#0d9488" : "#94a3b8"} strokeWidth="1.5" />
          <text x="10" y="23" fill="currentColor" fontSize="12">{node.task_id.length > 25 ? node.task_id.slice(0, 24) + "…" : node.task_id}</text>
          <text x="10" y="47" fill="currentColor" fontSize="11">{snapshot.view_mode === "HISTORICAL_STRUCTURE" ? "历史记录" : nodeLabel(node)}</text>
        </g>;
      })}
    </svg>
  </div>;
}

function GraphSession({ missionId, channel }: Props) {
  const [state, dispatch] = useReducer(graphReducer, emptyGraphState);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"hierarchy" | "execution">("hierarchy");
  const [revisionText, setRevisionText] = useState("");
  const [fromText, setFromText] = useState("");
  const [toText, setToText] = useState("");
  const pending = useRef<Pending | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shown = useRef(state.snapshot);
  shown.current = state.snapshot;
  const clear = () => { pending.current = null; setBusy(false); if (timer.current) clearTimeout(timer.current); timer.current = null; };

  useEffect(() => {
    let identity: string | null = null;
    const off = channel?.onMessage(raw => {
      const message = raw as unknown as { type?: string; payload?: unknown };
      const body = asRecord(message.payload);
      if (["companion_identity_unready", "companion_control_rechallenge", "companion_profile_unbound"].includes(message.type || "")
          || (message.type === "companion_identity_status" && body.ready === false)) {
        clear(); identity = null; dispatch({ type: "reset" }); return;
      }
      if (message.type === "companion_identity_status" || message.type === "companion_profile_bound") {
        if (typeof body.profile_id === "string") {
          const next = JSON.stringify([body.profile_id, body.profile_generation]);
          if (identity !== null && identity !== next) { clear(); dispatch({ type: "reset" }); }
          identity = next;
        }
        return;
      }
      if (message.type === "mission_changed" && body.mission_id === missionId) {
        if (pending.current) pending.current.changed = true;
        dispatch({ type: "stale" });
        return;
      }
      const request = pending.current;
      if (!request || body.request_id !== request.id || message.type !== request.kind + "_response") return;
      clear();
      if (body.ok !== true) {
        let error = typeof body.error === "string" ? body.error : "读取失败，请重试";
        if (body.taskgraph_error !== undefined) {
          try { error = graphErrorMessage(body.taskgraph_error); }
          catch { error = "执行图错误响应格式无效，请重新读取"; }
        }
        dispatch({ type: "error", error }); return;
      }
      try {
        if (request.kind === "taskgraph.snapshot") {
          const value = parseSnapshot(body.data, missionId, request.revision);
          if (value.view_mode === "CURRENT" && shown.current?.view_mode === "CURRENT"
            && value.read_token.through_seq < shown.current.read_token.through_seq) throw new Error("收到较旧的执行图，请重新读取");
          dispatch({ type: "snapshot", value });
          if (request.changed && value.view_mode === "CURRENT") dispatch({ type: "stale" });
        } else if (request.kind === "taskgraph.why_not_ready") {
          const value = parseExplanation(body.data, missionId);
          if (value.occurrence_id !== request.occurrence) throw new Error("返回了其他任务节点的原因");
          dispatch({ type: "explanation", value });
          if (request.changed) dispatch({ type: "stale" });
        } else if (request.kind === "taskgraph.diff") {
          const value = parseDiff(body.data, missionId);
          if (value.from_revision !== request.from || value.to_revision !== request.to) throw new Error("返回了其他版本的差异");
          dispatch({ type: "diff", value });
        } else {
          dispatch({ type: "convergence", value: parseConvergence(body.data, missionId) });
          if (request.changed) dispatch({ type: "stale" });
        }
      } catch (error) {
        dispatch({ type: "error", error: error instanceof Error ? error.message : "执行图响应无效" });
      }
    });
    const offState = channel?.onStateChange?.(() => {
      clear(); dispatch({ type: "reset" }); // Authentication/tenant boundary: retain no previous screen.
    });
    return () => { off?.(); offState?.(); pending.current = null; if (timer.current) clearTimeout(timer.current); };
  }, [channel, missionId]);

  function request(kind: string, payload: Record<string, unknown>, detail: Partial<Pending> = {}) {
    if (!channel || pending.current) return;
    const id = newRequestKey();
    pending.current = { id, kind, revision: null, changed: false, ...detail }; setBusy(true);
    if (!channel.send({ type: kind, request_id: id, payload: { mission_id: missionId, ...payload } })) {
      clear(); dispatch({ type: "error", error: "连接不可用，请求未发送" }); return;
    }
    timer.current = setTimeout(() => {
      if (pending.current?.id === id) { clear(); dispatch({ type: "error", error: "读取超时，已保留上次画面" }); }
    }, 30000);
  }
  function revision(value: string): number | null {
    if (!/^\d+$/.test(value.trim())) return null;
    const result = Number(value); return Number.isSafeInteger(result) && result >= 0 ? result : null;
  }
  function refresh() {
    const selected = revisionText.trim() ? revision(revisionText) : null;
    if (revisionText.trim() && selected === null) { dispatch({ type: "error", error: "请输入有效的历史版本号" }); return; }
    request("taskgraph.snapshot", selected === null ? {} : { revision: selected }, { revision: selected });
  }
  const snapshot = state.snapshot;
  const inspect = (node: GraphNode) => request("taskgraph.why_not_ready", { occurrence_id: node.occurrence_id }, { occurrence: node.occurrence_id });
  return <section className="taskgraph" aria-label="TaskGraph 执行图">
    <div className="tg-heading"><h3>执行图</h3><span>只读</span></div>
    <div className="tg-toolbar">
      <label>历史版本 <input aria-label="执行图历史版本" value={revisionText} onChange={e => setRevisionText(e.target.value)} placeholder="留空查看当前" inputMode="numeric" /></label>
      <button disabled={busy || !channel} onClick={refresh}>{busy ? "读取中…" : "读取执行图"}</button>
      <button disabled={busy || !channel} onClick={() => request("taskgraph.convergence", {})}>收敛状态</button>
    </div>
    {state.error && <p role="alert">{state.error}</p>}
    {state.stale && <p role="status">画面尚未更新。刷新后再判断当前就绪状态。</p>}
    {snapshot && <>
      <p>{snapshot.view_mode === "HISTORICAL_STRUCTURE" ? "历史结构 · 不提供执行许可" : "当前执行图"}
        {" · 版本 " + snapshot.read_token.plan_revision + " · 事件 " + snapshot.read_token.through_seq}</p>
      <div className="tg-toolbar" aria-label="执行图视图">
        <button aria-pressed={mode === "hierarchy"} onClick={() => setMode("hierarchy")}>层级关系</button>
        <button aria-pressed={mode === "execution"} onClick={() => setMode("execution")}>执行依赖</button>
        <span>灰：细化　紫：顺序　蓝：数据</span>
      </div>
      <Network snapshot={snapshot} mode={mode} inspect={inspect}
        disabled={busy || state.stale || snapshot.view_mode !== "CURRENT"} />
      <details><summary>{"全部节点（" + snapshot.nodes.length + "）"}</summary>
        <table><thead><tr><th>任务 / 节点</th><th>阶段</th><th>就绪原因</th><th>合同 / 派发代次</th></tr></thead>
          <tbody>{snapshot.nodes.map(node => <tr key={node.occurrence_id}><td>{node.task_id}<br /><small>{node.occurrence_id}</small></td>
            <td>{node.phase}</td><td><button disabled={busy || state.stale || snapshot.view_mode !== "CURRENT"}
              onClick={() => inspect(node)}>{nodeLabel(node)}</button></td>
            <td>{node.contract_revision + " / " + node.dispatch_generation}</td></tr>)}</tbody></table>
      </details>
      <details><summary>{"根目标结果记录（" + snapshot.root_resolution_refs.length + "）"}</summary>
        <p>来自该版本覆盖期间的原始提交记录；当前是否有效以最新就绪状态为准。</p>
        {!snapshot.root_resolution_refs.length && <p>本次读取中没有匹配的根目标结果记录。</p>}
        <ul>{snapshot.root_resolution_refs.map(ref => <li key={ref.id}>
          {ref.id + " · 合同 " + ref.revision}<br /><small>{"内容 SHA-256：" + ref.content_hash}</small>
        </li>)}</ul>
      </details>
    </>}
    {state.explanation && <div className="tg-explanation"><strong>{labels[state.explanation.readiness] || state.explanation.readiness}</strong>
      <p>{state.explanation.reason_codes.join(" · ")}</p>
      {state.explanation.details.map((detail, index) => <p key={index}>{detail}</p>)}
      <ul>{state.explanation.source_refs.map(ref => <li key={ref.kind + ":" + ref.id}>{ref.kind + " · " + ref.id + " @ " + ref.revision}</li>)}</ul>
    </div>}
    <details><summary>比较历史结构</summary><div className="tg-toolbar">
      <input aria-label="比较起始版本" value={fromText} onChange={e => setFromText(e.target.value)} placeholder="起始版本" inputMode="numeric" />
      <input aria-label="比较目标版本" value={toText} onChange={e => setToText(e.target.value)} placeholder="目标版本" inputMode="numeric" />
      <button disabled={busy || !channel} onClick={() => {
        const from = revision(fromText), to = revision(toText);
        if (from === null || to === null) { dispatch({ type: "error", error: "请输入两个有效版本号" }); return; }
        request("taskgraph.diff", { from_revision: from, to_revision: to }, { from, to });
      }}>比较</button>
    </div>{state.diff && <><p>{state.diff.from_revision + " → " + state.diff.to_revision + "，" + state.diff.changes.length + " 项变化"}</p>
      <ul>{state.diff.changes.map(c => <li key={c.kind + ":" + c.identity}>{c.change + " · " + c.kind + " · " + c.identity}</li>)}</ul></>}</details>
    {state.convergence && <details open><summary>{"收敛状态 · 事件 " + state.convergence.through_seq}</summary>
      {state.convergenceStale && <p role="status">收敛状态已变化或读取失败，请重新读取；此处保留的是上次结果。</p>}
      {!state.convergence.jobs.length && <p>本次完整读取中没有收敛任务。</p>}
      {state.convergence.jobs.map(job => <div key={job.job_id}><strong>{job.state + " · " + job.job_id}</strong>
        <ul>{job.targets.map(t => <li key={t.occurrence_id}>{t.task_id + " · " + t.target_kind}</li>)}</ul>
        {job.diagnostic_refs.map(ref => <p key={ref.kind + ":" + ref.id}>{ref.kind + " · " + ref.id + " @ " + ref.revision}</p>)}
      </div>)}</details>}
  </section>;
}
