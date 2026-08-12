// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S20 Wave 1c — usePermissionRequests hook
 *
 * Subscribes to the ControlChannel for `permission_request` messages,
 * exposes a single "currently-shown" request and a resolver callback.
 *
 * Multiple concurrent requests are queued FIFO so the user only sees
 * one popup at a time (the agent loop dispatches tools concurrently
 * via asyncio.gather, but rendering them serially makes the UI
 * predictable).
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { useSessionsStore } from "../stores/sessionsStore";
import type {
  PermissionRequest,
  PermissionResponse,
} from "../types/skillPlatform";

type Decision = "allow" | "allow_session" | "deny";

type PermissionChannel = {
  send(message: { type: string; payload?: Record<string, unknown> }): boolean;
  onMessage?: (listener: (message: unknown) => void) => () => void;
  on_message?: (listener: (message: unknown) => void) => () => void;
};

function permissionIdentity(
  payload: Pick<PermissionRequest["payload"], "decision_id" | "request_id">,
): string {
  return String(payload.decision_id || payload.request_id || "").trim();
}

export function usePermissionRequests(channel: PermissionChannel | null) {
  const [current, setCurrent] = useState<
    PermissionRequest["payload"] | null
  >(null);
  const currentRef = useRef<PermissionRequest["payload"] | null>(null);
  const queueRef = useRef<PermissionRequest["payload"][]>([]);
  const resolvedRef = useRef<Set<string>>(new Set());

  const showNext = useCallback(() => {
    const next = queueRef.current.shift();
    currentRef.current = next ?? null;
    setCurrent(next ?? null);
  }, []);

  const enqueue = useCallback((payload: PermissionRequest["payload"]) => {
    const identity = permissionIdentity(payload);
    if (!identity || resolvedRef.current.has(identity)) return;
    if (
      (currentRef.current && permissionIdentity(currentRef.current) === identity) ||
      queueRef.current.some((item) => permissionIdentity(item) === identity)
    ) {
      return;
    }
    if (currentRef.current === null) {
      currentRef.current = payload;
      setCurrent(payload);
    } else {
      queueRef.current.push(payload);
    }
  }, []);

  useEffect(() => {
    if (!channel) return undefined;
    const subscribe = channel.onMessage ?? channel.on_message;
    if (!subscribe) return undefined;
    channel.send({ type: "permissions_pending_list", payload: {} });
    const off = subscribe.call(channel, (raw) => {
      const msg = raw as { type?: string };
      if (msg.type === "permission_request") {
        const payload = (msg as PermissionRequest).payload;
        if (payload) enqueue(payload);
        return;
      }
      if (msg.type === "permissions_pending_list_response") {
        const pending = (
          msg as unknown as {
            payload?: { pending?: Array<Partial<PermissionRequest["payload"]>> };
          }
        ).payload?.pending ?? [];
        for (const item of pending) {
          if (!item.request_id) continue;
          enqueue({
            request_id: item.request_id,
            category: item.category ?? "read_file",
            summary: item.summary ?? "待批准的工具操作",
            params: item.params ?? {},
            default_action: item.default_action ?? "prompt",
            dangerous: item.dangerous ?? false,
            session_id: item.session_id ?? "",
            ...(item.run_id ? { run_id: item.run_id } : {}),
            ...(item.decision_id ? { decision_id: item.decision_id } : {}),
            ...(item.nonce ? { nonce: item.nonce } : {}),
            ...(item.version !== undefined ? { version: item.version } : {}),
          });
        }
      }
    });
    return () => {
      off();
    };
  }, [channel, enqueue]);

  const resolve = useCallback(
    (decision: Decision) => {
      if (!current || !channel) return;
      const reply: PermissionResponse = {
        type: "permission_response",
        payload: {
          request_id: current.request_id,
          decision,
          session_id: current.session_id,
          ...(current.run_id ? { run_id: current.run_id } : {}),
          ...(current.decision_id
            ? { decision_id: current.decision_id }
            : {}),
          ...(current.nonce ? { nonce: current.nonce } : {}),
          ...(current.version !== undefined
            ? { version: current.version }
            : {}),
        },
      };
      const sent = channel.send(
        reply as unknown as { type: string; payload?: Record<string, unknown> },
      );
      if (!sent) return;
      const sessionId = String(current.session_id || "").trim();
      const runId = String(current.run_id || "").trim();
      if (sessionId && runId) {
        const store = useSessionsStore.getState();
        store.upsert_run_projection(sessionId, runId, {
          status: "running",
          inflight: true,
        });
        // upsert_run_projection preserves terminal authority.  Only clear the
        // permission pill when this decision actually resumed a live Run;
        // a late response must not reopen an already terminal session.
        if (
          useSessionsStore.getState().sessions[sessionId]?.run_projections?.[
            runId
          ]?.status === "running"
        ) {
          useSessionsStore.getState().upsert(sessionId, {
            status: "running",
            inflight: true,
          });
        }
      }
      const identity = permissionIdentity(current);
      if (identity) {
        resolvedRef.current.add(identity);
        // A session can run for days; keep this replay fence bounded.
        if (resolvedRef.current.size > 512) {
          const oldest = resolvedRef.current.values().next().value;
          if (oldest) resolvedRef.current.delete(oldest);
        }
      }
      showNext();
    },
    [current, channel, showNext]
  );

  return { current, resolve } as const;
}
