import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { CognitiveRequests } from "../primary/cognitiveRequests";
import type { PrimaryPort } from "../primary/controller";
import { GraphRequests, type MemoryGraphView } from "../primary/graphRequests";
import { MemoryGraphCanvas, type GraphSelection } from "./MemoryGraphCanvas";
import { memoryTypeLabels } from "../primary/graphElements";
import { dark } from "../theme/components";

const labels: Record<string, string> = {
  active: "有效", draft: "草稿", candidate: "候选", pending: "待触发", completed: "已完成",
  contested: "有争议", none: "无", resolved: "已解决", uncontested: "无争议",
  explicit_user: "用户明确提供", observed_behavior: "行为观察", llm_inference: "模型推断",
  source_bound: "来源绑定", source_verified: "来源已核验", repeated_observation: "多次观察",
  unverified: "未核验", verified: "已核验", verified_external: "外部核验", unknown: "未知",
  expired: "已过期", superseded: "已替代", user_confirmed: "用户确认",
};
const label = (value: string) => labels[value] ?? value;
export function PrimaryMemoryGraph({ port, primaryRef, verifiedOwnerKey, ready, cognitive, claimInitialReveal }: {
  port: PrimaryPort; primaryRef: string; verifiedOwnerKey: string | null; ready: boolean; cognitive: CognitiveRequests;
  claimInitialReveal: () => boolean;
}) {
  const [client] = useState(() => new GraphRequests());
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  const [query, setQuery] = useState(""), [type, setType] = useState("");
  const [selection, setSelection] = useState<{ view: MemoryGraphView; value: GraphSelection } | null>(null);
  const selected = selection?.view === state.graph ? selection.value : null;
  const setSelected = (value: GraphSelection) => setSelection(state.graph ? { view: state.graph, value } : null);
  useEffect(() => client.connect(port, primaryRef, verifiedOwnerKey, ready, cognitive), [client, port, primaryRef, verifiedOwnerKey, ready, cognitive]);
  useEffect(() => client.subscribe(() => setSelection(null)), [client]);
  const nodes = useMemo(() => state.graph?.nodes.filter((node) =>
    (!type || node.memory_type === type) && node.label.toLocaleLowerCase().includes(query.toLocaleLowerCase())) ?? [], [state.graph, type, query]);
  const edges = useMemo(() => {
    const ids = new Set(nodes.map((node) => node.node_id));
    return state.graph?.edges.filter((edge) => ids.has(edge.source_node_id) && ids.has(edge.target_node_id)) ?? [];
  }, [nodes, state.graph]);
  const node = selected?.kind === "node" ? nodes.find((n) => n.node_id === selected.id) : undefined;
  const edge = selected?.kind === "edge" ? edges.find((e) => e.edge_id === selected.id) : undefined;
  return <section aria-label="记忆关系" style={{ minWidth: 0 }}>
    <p>查看已保存记忆之间的关系。此图只供你查看；排列位置不表示重要程度或推理结果。</p>
    <button disabled={!state.ready || state.loading} onClick={() => void client.refresh()}>刷新关系</button>
    {!state.ready && <p role="status">等待当前连接身份确认</p>}
    {state.loading && <p role="status">正在读取记忆关系…</p>}
    {state.error && <p role="alert">{state.error}</p>}
    {state.ready && !state.loading && !state.graph && !state.error && <p role="status">有一项忘记操作尚未确认，请返回记忆列表处理。</p>}
    {ready && verifiedOwnerKey && state.ready && state.graph && <>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, margin: "12px 0" }}>
        <label>筛选当前视图 <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按记忆内容筛选" /></label>
        <label>记忆类型 <select value={type} onChange={(event) => setType(event.target.value)}>
          <option value="">全部类型</option>{Object.entries(memoryTypeLabels).map(([key, value]) => <option key={key} value={key}>{value}</option>)}
        </select></label>
      </div>
      {(state.graph.truncated.nodes || state.graph.truncated.edges) && <p role="status">当前仅展示部分记忆与关系；筛选只作用于当前视图。</p>}
      <p>{nodes.length} 条记忆 · {edges.length} 条关系。虚线表示候选或推断，橙色边框表示争议。</p>
      {nodes.length ? <MemoryGraphCanvas nodes={nodes} edges={edges} selected={selected} onSelect={setSelected} claimInitialReveal={claimInitialReveal} /> : <p>当前没有符合条件的记忆关系可展示。</p>}
      <details open><summary>文字视图与键盘选择</summary>
        <ul aria-label="图中记忆">{nodes.map((n) => <li key={n.node_id}>
          <button aria-pressed={node?.node_id === n.node_id} onClick={() => setSelected({ kind: "node", id: n.node_id })}>
            {n.label} · {memoryTypeLabels[n.memory_type]} · {label(n.status)}
          </button>
        </li>)}</ul>
        <ul aria-label="图中关系">{edges.map((e) => <li key={e.edge_id}>
          <button aria-pressed={edge?.edge_id === e.edge_id} onClick={() => setSelected({ kind: "edge", id: e.edge_id })}>
            {nodes.find((n) => n.node_id === e.source_node_id)?.label} → {e.label === "applies_to" ? "适用于" : e.label} → {nodes.find((n) => n.node_id === e.target_node_id)?.label}
          </button>
        </li>)}</ul>
      </details>
      {(node || edge) && <aside aria-label="选中记忆详情" style={{ padding: 12, background: dark.card, borderRadius: 8, overflowWrap: "anywhere" }}>
        {node && <>
          <h3>{node.label}</h3>
          <dl><dt>类型与状态</dt><dd>{memoryTypeLabels[node.memory_type]} · {label(node.lifecycle_state)}</dd>
            <dt>证据与核验</dt><dd>{label(node.epistemic_status)} · {label(node.verification_state)} · {label(node.conflict_status)}</dd>
            <dt>置信度</dt><dd>{Math.round(node.confidence * 100)}%</dd></dl>
          <p>需要更正或忘记时，请返回记忆列表或主对话操作。</p>
          <details><summary>来源记录标识</summary>
            <p>这里提供来源的校验标识，不包含原文审计内容。</p>
            <ul>{node.source_refs.map((source, i) => <li key={i}>{source.source_kind}<br />{source.evidence_ref_hash}<br />{source.span_ref_hash}</li>)}</ul>
            {node.source_refs_truncated && <p>只展示部分来源标识。</p>}
          </details>
        </>}
        {edge && <><h3>关系：{edge.label === "applies_to" ? "适用于" : edge.label}</h3>
          <p>{nodes.find((n) => n.node_id === edge.source_node_id)?.label} → {nodes.find((n) => n.node_id === edge.target_node_id)?.label}</p>
          <p>关系来自已保存记录。此处暂不支持直接修改关系。</p>
          <details><summary>关系校验标识</summary><p>{edge.relation_hash}</p></details></>}
      </aside>}
    </>}
  </section>;
}
