// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { Message } from "./sessionTypes";
import { stable_json } from "./stableJson";

export function dedupe_ppt_outline_messages(messages: Message[]): Message[] {
  const lastByOutlineId = new Map<string, number>();
  messages.forEach((m, idx) => {
    if (m.role === "ppt_outline" && m.outline_id) {
      lastByOutlineId.set(m.outline_id, idx);
    }
  });
  if (lastByOutlineId.size === 0) return messages;
  return messages.filter((m, idx) => {
    if (m.role !== "ppt_outline" || !m.outline_id) return true;
    return lastByOutlineId.get(m.outline_id) === idx;
  });
}

export function normalized_history_value(value: unknown): string {
  if (typeof value !== "string") return stable_json(value);
  try {
    return stable_json(JSON.parse(value));
  } catch {
    return value;
  }
}

/**
 * Live websocket frames are rendered before SessionDB assigns its durable row
 * id.  A later history hydration therefore cannot use `id` alone to replace
 * the temporary copy.  This key is deliberately limited to ordinary durable
 * chat/tool rows; cards and partial streams keep their explicit identities.
 * Matching is consumed one-for-one, so two genuinely repeated messages remain
 * two messages instead of being collapsed by a global text set.
 */
export function history_reconciliation_key(message: Message): string | null {
  switch (message.role) {
    case "user":
    case "assistant":
    case "error":
      return `${message.role}\u001f${message.text ?? ""}`;
    case "reasoning_summary":
      return `${message.role}\u001f${message.reasoning_summary_id ?? ""}\u001f${message.text ?? ""}`;
    case "tool_call":
      return `${message.role}\u001f${message.tool_name ?? ""}\u001f${stable_json(message.tool_args ?? {})}`;
    case "tool_result":
      return `${message.role}\u001f${message.tool_name ?? ""}\u001f${normalized_history_value(message.tool_result ?? "")}`;
    default:
      return null;
  }
}

export function compatible_history_scope(left: Message, right: Message): boolean {
  // 两边都知道自己属于哪个 Run 时，Run 就是权威判据 —— 不要再拿
  // task_scope_id 做相等比较：它们分属不同命名空间。后端把 user 行持久化成
  // **回合**作用域（turn-*），而本地乐观副本带的是 Run 投影的任务作用域
  // （task-*），两者永远不等 → 同一条消息被判成"不同作用域"而拒绝对账 →
  // 切走再切回后用户气泡渲染两次（r5 S05 真机实测，DB 只有一行）。
  if (left.run_id && right.run_id) return left.run_id === right.run_id;
  if (left.task_scope_id && right.task_scope_id && left.task_scope_id !== right.task_scope_id) {
    return false;
  }
  return true;
}

export function reconcile_live_messages_with_history(
  liveMessages: Message[],
  historyMessages: Message[],
): Message[] {
  const remainingLive = [...liveMessages];
  for (const historyMessage of historyMessages) {
    let liveIndex = remainingLive.findIndex((message) => message.id === historyMessage.id);
    if (liveIndex < 0) {
      const historyKey = history_reconciliation_key(historyMessage);
      if (historyKey) {
        liveIndex = remainingLive.findIndex(
          (message) =>
            history_reconciliation_key(message) === historyKey &&
            compatible_history_scope(message, historyMessage),
        );
      }
    }
    if (liveIndex >= 0) remainingLive.splice(liveIndex, 1);
  }
  return [...remainingLive, ...historyMessages];
}
