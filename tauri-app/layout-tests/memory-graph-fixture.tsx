// Layout-only protocol values, never runtime memory data.
import { StrictMode, useState } from "react";
import "../src/index.css";
import { createRoot } from "react-dom/client";
import { PrimaryMemoryPanel } from "../src/components/PrimaryMemoryPanel";
import { graph } from "../src/primary/testing/graphFixture";
import type { PrimaryPort } from "../src/primary/controller";

const current = {
  ...graph,
  nodes: Array.from({ length: 7 }, (_, i) => ({
    ...graph.nodes[0], node_id: `layout-node-${i}`, memory_id: `layout-memory-${i}`,
    label: i === 0 ? "user:self · favorite_season · 秋天" : `Layout fixture memory ${i}`,
  })),
  edges: [],
};
const listeners = new Set<(event: unknown) => void>();
const emit = (event: unknown) => [...listeners].forEach((fn) => fn(event));
const port: PrimaryPort = {
  state: () => "connected", on_state_change: () => () => {},
  on_message: (fn) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
  send_command: (r) => {
    queueMicrotask(() => emit({ type: "human_memory_response", request_id: r.request_id,
      payload: { ok: true, operation: r.operation, result: r.operation === "primary.memory.graph"
        ? current : { primary_ref: graph.primary_ref, items: [], next_cursor: null } },
    }));
    return true;
  },
};
export function Fixture() {
  const [owner, setOwner] = useState("fixture:1");
  return <div style={{ height: "100vh", display: "flex", background: "#10141d", color: "#eee", overflow: "hidden" }}>
    <aside style={{ width: 240, flexShrink: 0 }}>Layout fixture, not production facts
      <button onClick={() => emit({ type: "human_memory_changed", payload: {} })}>Fixture producer</button>
      <button onClick={() => setOwner((old) => old === "fixture:1" ? "fixture:2" : "fixture:1")}>Fixture rebind</button>
    </aside>
    <section style={{ display: "flex", flexDirection: "column", minHeight: 0, flex: 1 }}>
      <header style={{ height: 96, flexShrink: 0 }}>Primary nested scrolling layout</header>
      <div id="memory-scroll" style={{ maxHeight: "50%", overflowY: "auto", flexShrink: 1 }}>
        <PrimaryMemoryPanel port={port} primaryRef={graph.primary_ref} verifiedOwnerKey={owner} ready />
      </div>
      <div style={{ overflowY: "auto", flex: 1, minHeight: 0 }}>History</div>
      <footer style={{ height: 80, flexShrink: 0 }}>Input</footer>
    </section>
  </div>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><Fixture /></StrictMode>);
