// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { beforeEach, describe, expect, it } from "vitest";

import { useSubagentStore } from "./subagentStore";

describe("subagentStore (WI-3.4)", () => {
  beforeEach(() => useSubagentStore.getState().clear());

  it("upserts a run and merges partial updates", () => {
    const s = useSubagentStore.getState();
    s.upsert({ run_id: "r1", kind: "research", task_id: "t1", status: "queued", ts: 1 });
    s.upsert({ run_id: "r1", status: "running" });
    const run = useSubagentStore.getState().runs["r1"];
    expect(run.status).toBe("running");
    expect(run.kind).toBe("research"); // merged, not lost
    expect(run.task_id).toBe("t1");
  });

  it("tracks multiple concurrent runs", () => {
    const s = useSubagentStore.getState();
    s.upsert({ run_id: "a", kind: "doc", status: "running", ts: 1 });
    s.upsert({ run_id: "b", kind: "web", status: "queued", ts: 2 });
    expect(Object.keys(useSubagentStore.getState().runs)).toHaveLength(2);
  });

  it("clearTerminal keeps active runs, drops completed/failed", () => {
    const s = useSubagentStore.getState();
    s.upsert({ run_id: "a", status: "running", ts: 1 });
    s.upsert({ run_id: "b", status: "completed", ts: 2 });
    s.upsert({ run_id: "c", status: "failed", ts: 3 });
    useSubagentStore.getState().clearTerminal();
    const runs = useSubagentStore.getState().runs;
    expect(runs["a"]).toBeTruthy();
    expect(runs["b"]).toBeUndefined();
    expect(runs["c"]).toBeUndefined();
  });

  it("clear removes everything", () => {
    useSubagentStore.getState().upsert({ run_id: "a", status: "running", ts: 1 });
    useSubagentStore.getState().clear();
    expect(Object.keys(useSubagentStore.getState().runs)).toHaveLength(0);
  });
});
