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
  state?:
    | "disconnected"
    | "connecting"
    | "connected"
    | (() => "disconnected" | "connecting" | "connected");
  onStateChange?: (
    listener: (state: "disconnected" | "connecting" | "connected") => void,
  ) => () => void;
  on_state_change?: (
    listener: (state: "disconnected" | "connecting" | "connected") => void,
  ) => () => void;
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
  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState<string | null>(null);

  const showNext = useCallback(() => {
    const next = queueRef.current.shift();
    currentRef.current = next ?? null;
    setCurrent(next ?? null);
    setResolving(false);
    setResolveError(null);
  }, []);

  const rememberResolved = useCallback((identity: string) => {
    if (!identity) return;
    resolvedRef.current.add(identity);
    if (resolvedRef.current.size > 512) {
      const oldest = resolvedRef.current.values().next().value;
      if (oldest) resolvedRef.current.delete(oldest);
    }
  }, []);

  const completeCurrent = useCallback(
    (identity: string) => {
      const completed = currentRef.current;
      if (!completed || permissionIdentity(completed) !== identity) return;
      const sessionId = String(completed.session_id || "").trim();
      const runId = String(completed.run_id || "").trim();
      if (sessionId && runId) {
        const store = useSessionsStore.getState();
        store.upsert_run_projection(sessionId, runId, {
          status: "running",
          inflight: true,
        });
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
      rememberResolved(identity);
      showNext();
    },
    [rememberResolved, showNext],
  );

  const dismissRun = useCallback(
    (runId: string) => {
      const normalized = runId.trim();
      if (!normalized) return;
      const retained = [] as PermissionRequest["payload"][];
      for (const item of queueRef.current) {
        if (String(item.run_id || "").trim() === normalized) {
          rememberResolved(permissionIdentity(item));
        } else {
          retained.push(item);
        }
      }
      queueRef.current = retained;
      const active = currentRef.current;
      if (active && String(active.run_id || "").trim() === normalized) {
        rememberResolved(permissionIdentity(active));
        showNext();
      }
    },
    [rememberResolved, showNext],
  );

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
    const requestPending = () => {
      channel.send({ type: "permissions_pending_list", payload: {} });
    };
    // ChatView commonly mounts while ControlChannel is still connecting. Its
    // generic outbox queues chat turns only, so an eager pending-list request
    // can otherwise be dropped forever. Re-query on every successful
    // connection; decision identity de-duplication safely merges live replay.
    const subscribeState = channel.onStateChange ?? channel.on_state_change;
    const channelState =
      typeof channel.state === "function" ? channel.state() : channel.state;
    if (!subscribeState || channelState === "connected") {
      requestPending();
    }
    const offState = subscribeState?.call(channel, (state) => {
      if (state === "connected") requestPending();
    });
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
        return;
      }
      if (msg.type === "permission_response_applied") {
        const payload = (
          msg as {
            payload?: {
              ok?: boolean;
              request_id?: string;
              decision_id?: string;
              error?: { message?: string };
            };
          }
        ).payload;
        if (!payload) return;
        const identity = String(
          payload.decision_id || payload.request_id || "",
        ).trim();
        if (
          !identity ||
          !currentRef.current ||
          identity !== permissionIdentity(currentRef.current)
        ) {
          return;
        }
        if (payload.ok === false) {
          setResolving(false);
          setResolveError(payload.error?.message ?? "授权提交失败，请重试");
          return;
        }
        completeCurrent(identity);
        return;
      }
      if (msg.type === "chat_v2_interrupted") {
        const payload = (
          msg as {
            payload?: {
              run_id?: string;
              cancelled?: boolean;
            };
          }
        ).payload;
        const runId = String(payload?.run_id || "").trim();
        if (!runId) return;
        if (payload?.cancelled === false) {
          if (
            currentRef.current &&
            String(currentRef.current.run_id || "").trim() === runId
          ) {
            setResolving(false);
            setResolveError("停止任务失败，请重试");
          }
          return;
        }
        dismissRun(runId);
      }
    });
    return () => {
      off();
      offState?.();
    };
  }, [channel, completeCurrent, dismissRun, enqueue]);

  const resolve = useCallback(
    (decision: Decision) => {
      if (!current || !channel || resolving) return;
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
      setResolving(true);
      setResolveError(null);
    },
    [current, channel, resolving]
  );

  const stopCurrentRun = useCallback(() => {
    if (!current || !channel || resolving) return;
    const runId = String(current.run_id || "").trim();
    if (!runId) return;
    const sent = channel.send({
      type: "chat_v2_interrupt",
      payload: { session_id: current.session_id, run_id: runId },
    });
    if (!sent) {
      setResolveError("停止任务请求发送失败，请重试");
      return;
    }
    setResolving(true);
    setResolveError(null);
  }, [channel, current, resolving]);

  return {
    current,
    resolve,
    stopCurrentRun,
    resolving,
    resolveError,
  } as const;
}
