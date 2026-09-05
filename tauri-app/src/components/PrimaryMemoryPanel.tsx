import { useEffect, useState, useSyncExternalStore } from "react";
import { PrimaryMemoryGraph } from "./PrimaryMemoryGraph";
import { CognitiveRequests } from "../primary/cognitiveRequests";
import type { PrimaryPort } from "../primary/controller";
import { buttonStyle, dark } from "../theme/components";

export interface PrimaryMemoryPanelProps {
  port: PrimaryPort;
  primaryRef: string;
  /** Derived from the actual bound owner, not global identity_status. */
  verifiedOwnerKey: string | null;
  ready: boolean;
  /** Main may retain this instance across panel close/reopen for unresolved action IDs. */
  requests?: CognitiveRequests;
  /** Clear old history/detail synchronously, then start a fresh authenticated read. */
  onForgotten?: () => void;
  onClose?: () => void;
}

export function PrimaryMemoryPanel({ port, primaryRef, verifiedOwnerKey, ready, requests, onForgotten, onClose }: PrimaryMemoryPanelProps) {
  const [tab, setTab] = useState<"list" | "graph">("list");
  const [local] = useState(() => new CognitiveRequests());
  const client = requests ?? local;
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  useEffect(() => onForgotten ? client.onForgotten(onForgotten) : undefined, [client, onForgotten]);
  useEffect(() => client.connect(port, primaryRef, verifiedOwnerKey, ready), [client, port, primaryRef, verifiedOwnerKey, ready]);
  return <section aria-label="认知记忆" style={{ padding: 16, color: dark.text, overflowY: "auto" }}>
    <header style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <h2 style={{ flex: 1 }}>认知记忆</h2>
      <button style={buttonStyle("secondary", "sm")} disabled={!state.ready || state.loading} onClick={() => void client.refresh()}>刷新</button>
      {onClose && <button style={buttonStyle("secondary", "sm")} onClick={onClose}>关闭</button>}
    </header>
    <nav aria-label="记忆视图" style={{ display: "flex", gap: 8 }}>
      <button aria-pressed={tab === "list"} onClick={() => setTab("list")}>记忆列表</button>
      <button aria-pressed={tab === "graph"} onClick={() => setTab("graph")}>关系图</button>
    </nav>
    {tab === "graph" && <PrimaryMemoryGraph key={`${primaryRef}:${verifiedOwnerKey ?? "unbound"}`} port={port} primaryRef={primaryRef} verifiedOwnerKey={verifiedOwnerKey} ready={ready} cognitive={client} />}
    {tab === "list" && <>
    <p>这里展示当前认知记忆。忘记会禁止该记忆继续使用，原始历史档案保留；不会自动撤销。</p>
    {!state.ready && <p role="status">等待当前连接身份确认</p>}
    {state.notice && <p role="status">{state.notice}</p>}
    {state.error && <p role="alert">{state.error}</p>}
    {state.ready && <>
      {state.loading && <p role="status">正在读取…</p>}
      {!state.loading && !state.error && state.items.length === 0 && <p>当前页没有可展示的认知记忆。</p>}
      <ul style={{ listStyle: "none", padding: 0 }}>{state.items.map((item) => <li key={item.memory_id} style={{ padding: 12, marginBottom: 8, background: dark.card, borderRadius: 8, overflowWrap: "anywhere" }}>
        <p>{item.label}</p>
        <div><button style={buttonStyle("secondary", "sm")} disabled={!item.can_forget || state.writing}
          onClick={() => void client.forget(item)}>忘记这条记忆</button></div>
      </li>)}</ul>
      {state.nextCursor && <button disabled={state.loading} onClick={() => void client.refresh(state.nextCursor)}>下一页</button>}
      {state.pending.map((action) => <div key={action.action_id}>
        <p>有一项忘记操作尚未确认。</p>
        <button disabled={state.writing} onClick={() => void client.retry(action.action_id)}>重试同一忘记操作</button>
      </div>)}
    </>}
    </>}
  </section>;
}
