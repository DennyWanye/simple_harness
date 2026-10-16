// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/** 工具正文首行是 Host 拼上的 `{"call_id":…,"name":…}`；拆出来做折叠标题。 */
export function toolHeadline(text: string): { name: string; body: string } {
  const cut = text.indexOf("\n");
  const head = cut === -1 ? text : text.slice(0, cut);
  try {
    const meta = JSON.parse(head) as Record<string, unknown>;
    if (meta && typeof meta === "object" && typeof meta.name === "string" && meta.name) {
      return { name: meta.name, body: cut === -1 ? "" : text.slice(cut + 1) };
    }
  } catch {
    /* 不是元数据首行就整体当正文 */
  }
  return { name: "", body: text };
}
