// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { PendingPermissionItem } from "../types/messages";

export type ApprovalDecision = "allow" | "allow_session" | "deny";

// ----------------------------------------------------------------------
// Pure helpers — wire format + list reducers（测试直接 import）
// ----------------------------------------------------------------------

/** 构造「列出当前 session pending 权限请求」的只读查询消息。 */
export function buildPendingListMessage(sessionId?: string): {
  type: "permissions_pending_list";
  payload: { session_id?: string };
} {
  const payload: { session_id?: string } = {};
  if (sessionId) payload.session_id = sessionId;
  return { type: "permissions_pending_list", payload };
}

/**
 * 构造一条对单个 request_id 的 `permission_response`（与既有协议一致）。
 * 批量决策 = 对每个 pending item 各调一次本函数发送。
 */
export function buildDecisionMessage(
  requestId: string,
  decision: ApprovalDecision,
): {
  type: "permission_response";
  payload: { request_id: string; decision: ApprovalDecision };
} {
  return {
    type: "permission_response",
    payload: { request_id: requestId, decision },
  };
}

/**
 * 把后端快照 + 实时推送 merge 进当前列表（按 request_id 去重，新值覆盖）。
 * 顺序：保留既有顺序，新 request_id 追加到尾部 —— 用户视觉稳定。
 */
export function mergePending(
  current: PendingPermissionItem[],
  incoming: PendingPermissionItem[],
): PendingPermissionItem[] {
  const byId = new Map<string, PendingPermissionItem>();
  const order: string[] = [];
  for (const item of current) {
    if (!byId.has(item.request_id)) order.push(item.request_id);
    byId.set(item.request_id, item);
  }
  for (const item of incoming) {
    if (!byId.has(item.request_id)) order.push(item.request_id);
    byId.set(item.request_id, item);
  }
  return order.map((id) => byId.get(id)!).filter(Boolean);
}

/** 从列表移除一批已决议的 request_id（批量批准/拒绝后用）。 */
export function removeResolved(
  current: PendingPermissionItem[],
  resolvedIds: Iterable<string>,
): PendingPermissionItem[] {
  const drop = new Set(resolvedIds);
  return current.filter((item) => !drop.has(item.request_id));
}

/** 把单条 permission_request 推送规整成 PendingPermissionItem。 */
export function pendingFromRequest(
  payload: Partial<PendingPermissionItem> & { request_id: string },
): PendingPermissionItem {
  return {
    request_id: payload.request_id,
    category: payload.category ?? "",
    summary: payload.summary ?? "",
    params: payload.params ?? {},
    default_action: payload.default_action ?? "prompt",
    dangerous: payload.dangerous ?? false,
    session_id: payload.session_id ?? "",
  };
}
