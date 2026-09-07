import { afterEach, expect, it, vi } from "vitest";
import { GraphRequests, parseMemoryGraph } from "./graphRequests";
import { CognitiveRequests } from "./cognitiveRequests";
import { graph, wire, flush } from "./testing/graphFixture";

afterEach(() => vi.useRealTimers());
it("accepts closed real DTO shapes and rejects borrowed, dangling or duplicate records", () => {
  expect(parseMemoryGraph(graph, "p")).toEqual(graph);
  expect(() => parseMemoryGraph(graph, "other")).toThrow();
  expect(() => parseMemoryGraph({ ...graph, nodes: [...graph.nodes, ...graph.nodes] }, "p")).toThrow();
  expect(() => parseMemoryGraph({ ...graph, edges: [{ edge_id: "e", source_node_id: "n", target_node_id: "missing" }] }, "p")).toThrow();
});
it("actual change wire drops earlier replies and unbound broadcast never grants access", async () => {
  const w = wire(), c = new GraphRequests(); let stop = c.connect(w.port, "p", null, false);
  w.emit({ type: "companion_identity_status", payload: { ready: true } }); expect(w.sent).toHaveLength(0);
  stop(); stop = c.connect(w.port, "p", "owner:1", true);
  w.reply(0, graph); await flush(); expect(c.getSnapshot().graph).toEqual(graph);
  void c.refresh(); w.emit({ type: "human_memory_changed", payload: {} });
  expect(c.getSnapshot().graph).toBeNull();
  w.reply(1, graph); await flush(); expect(c.getSnapshot().graph).toBeNull();
  w.reply(2, { ...graph, nodes: [] }); await flush(); expect(c.getSnapshot().graph?.nodes).toEqual([]);
  w.emit({ type: "companion_profile_bound", payload: { profile_id: "other", profile_generation: 2 } });
  expect(c.getSnapshot().graph).toBeNull(); expect(c.getSnapshot().ready).toBe(false); stop();
});
it.each([false, true])("real CognitiveRequests forget pending before graph mount=%s invalidates without producer mock", async (mountAfter) => {
  const w = wire(), cognitive = new CognitiveRequests(), graphClient = new GraphRequests();
  const closeC = cognitive.connect(w.port, "p", "owner:1", true);
  const item = { ...graph.nodes[0], status: "active" };
  w.reply(0, { primary_ref: "p", items: [item], next_cursor: null }); await flush();
  let closeG = () => {};
  if (!mountAfter) {
    closeG = graphClient.connect(w.port, "p", "owner:1", true, cognitive);
    w.reply(1, graph); await flush(); expect(graphClient.getSnapshot().graph).toBeTruthy();
    void graphClient.refresh(); // late graph is now in flight
  }
  const pending = cognitive.forget(item);
  const writeIndex = w.sent.findIndex((r) => r.operation === "primary.memory.forget");
  if (mountAfter) closeG = graphClient.connect(w.port, "p", "owner:1", true, cognitive);
  expect(graphClient.getSnapshot().graph).toBeNull();
  if (!mountAfter) { w.reply(2, graph); await flush(); expect(graphClient.getSnapshot().graph).toBeNull(); }
  const before = w.sent.filter((r) => r.operation === "primary.memory.graph").length;
  void graphClient.refresh(); expect(w.sent.filter((r) => r.operation === "primary.memory.graph")).toHaveLength(before);
  w.reply(writeIndex, { ...w.sent[writeIndex].request, status: "applied", directive_ref: "directive", evidence_ref: "action", decision_hash: "d".repeat(64) });
  await flush(); expect(graphClient.getSnapshot().graph).toBeNull();
  for (let i = writeIndex + 1; i < w.sent.length; i++) {
    if (w.sent[i].operation === "primary.memory.list") w.reply(i, { primary_ref: "p", items: [], next_cursor: null });
    else w.reply(i, { ...graph, nodes: [] });
  }
  await pending; await flush(); expect(graphClient.getSnapshot().graph?.nodes).toEqual([]);
  closeG(); closeC();
});
it("unknown forget survives tab reopen and same-owner rebind but cannot replay to another owner", async () => {
  vi.useFakeTimers(); const w = wire(), cognitive = new CognitiveRequests(), c = new GraphRequests();
  let closeC = cognitive.connect(w.port, "p", "owner:1", true);
  w.reply(0, { primary_ref: "p", items: graph.nodes, next_cursor: null }); await flush();
  const pending = cognitive.forget(graph.nodes[0]); await vi.advanceTimersByTimeAsync(15000); await pending;
  let closeG = c.connect(w.port, "p", "owner:1", true, cognitive);
  expect(w.sent.filter((r) => r.operation === "primary.memory.graph")).toHaveLength(0);
  w.emit({ type: "companion_control_rechallenge" }); closeG(); closeC();
  closeC = cognitive.connect(w.port, "p", "owner:1", true); closeG = c.connect(w.port, "p", "owner:1", true, cognitive);
  expect(cognitive.getSnapshot().pending).toHaveLength(1); expect(c.getSnapshot().graph).toBeNull();
  expect(w.sent.filter((r) => r.operation === "primary.memory.forget")).toHaveLength(1);
  closeG(); closeC(); closeC = cognitive.connect(w.port, "p", "new:2", true); closeG = c.connect(w.port, "p", "new:2", true, cognitive);
  expect(cognitive.getSnapshot().pending).toEqual([]); expect(c.getSnapshot().graph).toBeNull();
  closeG(); closeC();
});
