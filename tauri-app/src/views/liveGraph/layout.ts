// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 在 Worker 里排版；没有 Worker 的环境（测试）退回主线程。
 *
 * 2026-09-26 真机：曾把 `elk.bundled.js` 放进自写的 module Worker，它在 Worker 环境里会把自己
 * 当成 elk 的 worker 端接管消息，排版请求永远等不到回复（界面一直"正在排版…"）。现在按 elkjs
 * 官方用法：主线程用 `elk-api`，Worker 直接跑 `elk-worker.min.js`。另加超时，超时就报错不空等。
 */
import type { ElkInput, ElkOutput } from "./layoutModel";

export const LAYOUT_TIMEOUT_MS = 20000;

type Layouter = { layout: (graph: unknown) => Promise<unknown> };
let engine: Promise<Layouter> | null = null;

async function createEngine(): Promise<Layouter> {
  if (typeof Worker === "undefined") {
    const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
    return new ELK() as unknown as Layouter;
  }
  const [{ default: ELK }, { default: workerUrl }] = await Promise.all([
    import("elkjs/lib/elk-api.js"),
    import("elkjs/lib/elk-worker.min.js?url"),
  ]);
  return new ELK({ workerFactory: () => new Worker(workerUrl) }) as unknown as Layouter;
}

export async function layoutGraph(graph: ElkInput): Promise<ElkOutput> {
  engine ??= createEngine();
  const layouter = await engine;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error("排版超时")), LAYOUT_TIMEOUT_MS);
  });
  try {
    return (await Promise.race([layouter.layout(graph), timeout])) as ElkOutput;
  } catch (error) {
    engine = null; // a stuck or broken worker is not reused
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
