// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * subagent-concurrency-driver WI-3.4 — 子代理并发进度 store。
 *
 * 后端 SubagentScheduler 经 control WS 广播 `subagent_progress`
 * （queued → running → completed/failed，含 run_id/kind/task_id）。
 * ws.ts 的 dispatch 把它喂给本 store；SubagentProgressPanel 订阅渲染。
 * 独立 slice，不碰 sessionsStore 的消息派生（计划 R7：避免与 ChatRow 冲突）。
 */
import { create } from "zustand";

export interface SubagentRunView {
  run_id: string;
  task_id: string;
  kind: string;
  status: string; // queued | running | completed | failed
  summary?: string;
  ts: number;
}

interface SubagentState {
  runs: Record<string, SubagentRunView>;
  upsert: (p: Partial<SubagentRunView> & { run_id: string }) => void;
  clear: () => void;
  /** 清掉已终止（completed/failed）的，保留还在跑的。 */
  clearTerminal: () => void;
}

export const useSubagentStore = create<SubagentState>((set) => ({
  runs: {},
  upsert: (p) =>
    set((s) => {
      const prev =
        s.runs[p.run_id] ||
        ({
          run_id: p.run_id,
          task_id: "",
          kind: "",
          status: "queued",
          ts: 0,
        } as SubagentRunView);
      return { runs: { ...s.runs, [p.run_id]: { ...prev, ...p } } };
    }),
  clear: () => set({ runs: {} }),
  clearTerminal: () =>
    set((s) => {
      const next: Record<string, SubagentRunView> = {};
      for (const [k, v] of Object.entries(s.runs)) {
        if (v.status === "queued" || v.status === "running") next[k] = v;
      }
      return { runs: next };
    }),
}));
