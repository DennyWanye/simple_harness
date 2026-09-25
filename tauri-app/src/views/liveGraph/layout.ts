// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/** 在 Worker 里排版；没有 Worker 的环境（测试）退回主线程。 */
import type { ElkInput, ElkOutput } from "./layoutModel";

let worker: Worker | null = null;
let nextId = 0;
const waiting = new Map<number, { resolve: (value: ElkOutput) => void; reject: (error: Error) => void }>();

function ensureWorker(): Worker | null {
  if (worker || typeof Worker === "undefined") return worker;
  worker = new Worker(new URL("./layout.worker.ts", import.meta.url), { type: "module" });
  worker.onmessage = (event: MessageEvent<{ id: number; result?: ElkOutput; error?: string }>) => {
    const entry = waiting.get(event.data.id);
    if (!entry) return;
    waiting.delete(event.data.id);
    if (event.data.error) entry.reject(new Error(event.data.error));
    else entry.resolve(event.data.result as ElkOutput);
  };
  return worker;
}

export async function layoutGraph(graph: ElkInput): Promise<ElkOutput> {
  const target = ensureWorker();
  if (!target) {
    const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return (await new ELK().layout(graph as any)) as ElkOutput;
  }
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    waiting.set(id, { resolve, reject });
    target.postMessage({ id, graph });
  });
}
