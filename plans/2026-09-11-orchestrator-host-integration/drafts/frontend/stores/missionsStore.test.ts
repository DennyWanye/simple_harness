// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * missionsStore 测试（计划 2026-09-11 H4）。草稿：实现之前写的 oracle。
 */
import { afterEach, describe, expect, it } from "vitest";

import { useMissionsStore } from "./missionsStore";

afterEach(() => useMissionsStore.getState().reset());

describe("missionsStore", () => {
  it("事件按 seq 合并，不重复、保持递增", () => {
    const store = useMissionsStore.getState();
    store.appendEvents("m1", [
      { seq: 1, type: "MissionCreated", created_at: 1, summary: "" },
      { seq: 2, type: "MissionPlanning", created_at: 2, summary: "" },
    ]);
    store.appendEvents("m1", [
      { seq: 2, type: "MissionPlanning", created_at: 2, summary: "" },
      { seq: 3, type: "TaskCommitted", created_at: 3, summary: "" },
    ]);
    expect(useMissionsStore.getState().events.m1.map((e) => e.seq)).toEqual([1, 2, 3]);
  });

  it("mission_changed 只前进 last_seq，不回退", () => {
    const store = useMissionsStore.getState();
    store.setMissions([{ id: "m1", goal: "g", status: "RUNNING", stop_reason: null, created_at: 1, pending_approvals: 0 }]);
    store.applyChange({ mission_id: "m1", status: "COMPLETED", last_seq: 9 });
    store.applyChange({ mission_id: "m1", status: "RUNNING", last_seq: 5 }); // 迟到的旧推送
    const row = useMissionsStore.getState().missions.find((m) => m.id === "m1");
    expect(row?.status).toBe("COMPLETED");
    expect(useMissionsStore.getState().lastSeq.m1).toBe(9);
  });

  it("未知 Mission 的推送触发重新拉列表的标记", () => {
    const store = useMissionsStore.getState();
    store.setMissions([]);
    store.applyChange({ mission_id: "m-new", status: "PLANNING", last_seq: 1 });
    expect(useMissionsStore.getState().listStale).toBe(true);
  });

  it("待审批总数是各 Mission 之和（侧栏角标）", () => {
    const store = useMissionsStore.getState();
    store.setMissions([
      { id: "a", goal: "g", status: "RUNNING", stop_reason: null, created_at: 1, pending_approvals: 1 },
      { id: "b", goal: "g", status: "RUNNING", stop_reason: null, created_at: 2, pending_approvals: 2 },
    ]);
    expect(useMissionsStore.getState().pendingApprovalTotal()).toBe(3);
  });
});
