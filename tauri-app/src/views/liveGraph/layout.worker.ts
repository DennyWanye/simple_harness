// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/** 运行视图排版 Worker：只接 elk 图、算坐标、回传，不碰界面。 */
import ELK from "elkjs/lib/elk.bundled.js";

const elk = new ELK();

self.onmessage = async (event: MessageEvent<{ id: number; graph: unknown }>) => {
  const { id, graph } = event.data;
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const result = await elk.layout(graph as any);
    (self as unknown as Worker).postMessage({ id, result });
  } catch (error) {
    (self as unknown as Worker).postMessage({ id, error: error instanceof Error ? error.message : String(error) });
  }
};
