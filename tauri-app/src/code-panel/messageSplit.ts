// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// P5-S2: split a content string on `<think>...</think>` boundaries.
// Handles both well-formed (closed) and unclosed (streaming or
// premature-end_turn) think tags. Returns a flat list of segments
// in original order; consumer renders normal segments through markdown
// and think segments as collapsed reasoning bubbles.
//
// Examples:
//   "hi <think>foo</think> bye"
//     → [{kind:"normal","hi "},{kind:"think","foo",closed:true},{kind:"normal"," bye"}]
//   "<think>still going..."
//     → [{kind:"think","still going...",closed:false}]
//   "plain text"
//     → [{kind:"normal","plain text"}]
export type ThinkSegment =
  | { kind: "normal"; text: string }
  | { kind: "think"; text: string; closed: boolean };

export function splitThinkBlocks(text: string): ThinkSegment[] {
  if (!text) return [];
  const out: ThinkSegment[] = [];
  let i = 0;
  while (i < text.length) {
    const open = text.indexOf("<think>", i);
    if (open === -1) {
      const tail = text.slice(i);
      if (tail) out.push({ kind: "normal", text: tail });
      break;
    }
    if (open > i) {
      out.push({ kind: "normal", text: text.slice(i, open) });
    }
    const close = text.indexOf("</think>", open + 7);
    if (close === -1) {
      // Unclosed — everything after <think> is a still-streaming reasoning block.
      out.push({ kind: "think", text: text.slice(open + 7), closed: false });
      break;
    }
    out.push({ kind: "think", text: text.slice(open + 7, close), closed: true });
    i = close + "</think>".length;
  }
  return out;
}

// P5-S2 Phase 5.1: 把后端 os_tools 错误返回值（统一 schema）拆成
//   { body, hint, examples }
// 让 ToolResultCard 可以对 hint 做高亮渲染。后端 schema:
//   { ok: false, error: "...", hint: "...", examples: [...] }
// 没有 hint 字段或不是 JSON 时退化为 { body: 原文, hint: null, examples: null }。
//
// 这里挂在 export 是为了 splitToolError.test.ts 能直接 import 测纯函数。
export interface SplitToolErrorResult {
  body: string;
  hint: string | null;
  examples: unknown[] | null;
}

export function splitToolError(raw: string): SplitToolErrorResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { body: raw, hint: null, examples: null };
  }
  if (!parsed || typeof parsed !== "object") {
    return { body: JSON.stringify(parsed, null, 2), hint: null, examples: null };
  }
  const obj = parsed as Record<string, unknown>;
  // hint 必须是非空 string；空白也算空
  let hint: string | null = null;
  if (typeof obj.hint === "string" && obj.hint.trim().length > 0) {
    hint = obj.hint;
  }
  let examples: unknown[] | null = null;
  if (Array.isArray(obj.examples)) {
    examples = obj.examples;
  }
  // last-mile envelope 包装后，工具自身的 {ok:false, hint, examples} 被套进
  // envelope.result（JSON 字符串）→ 顶层取不到 hint，金黄修复建议卡不触发。
  // 兜底：当顶层无 hint 时，解包 envelope.result 再取 hint/examples（与
  // ArtifactCard.extractArtifactsFromResult 的嵌套兜底同一模式）。
  if (hint === null && typeof obj.result === "string") {
    try {
      const inner = JSON.parse(obj.result) as Record<string, unknown>;
      if (inner && typeof inner === "object") {
        if (typeof inner.hint === "string" && inner.hint.trim().length > 0) {
          hint = inner.hint;
        }
        if (examples === null && Array.isArray(inner.examples)) {
          examples = inner.examples;
        }
      }
    } catch {
      /* envelope.result 不是 JSON → 忽略，保持 hint=null */
    }
  }
  return {
    body: JSON.stringify(parsed, null, 2),
    hint,
    examples,
  };
}
