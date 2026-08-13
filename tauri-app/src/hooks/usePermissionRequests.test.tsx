// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { usePermissionRequests } from "./usePermissionRequests";
import { useSessionsStore } from "../stores/sessionsStore";

class FakePanelChannel {
  readonly sent: Array<{ type: string; payload?: Record<string, unknown> }> = [];
  private listener: ((message: unknown) => void) | null = null;

  send = (message: { type: string; payload?: Record<string, unknown> }) => {
    this.sent.push(message);
    return true;
  };

  on_message = (listener: (message: unknown) => void) => {
    this.listener = listener;
    return () => {
      if (this.listener === listener) this.listener = null;
    };
  };

  emit(message: unknown) {
    this.listener?.(message);
  }
}

class ReconnectingPanelChannel extends FakePanelChannel {
  state: "disconnected" | "connecting" | "connected" = "connecting";
  private stateListener:
    | ((state: "disconnected" | "connecting" | "connected") => void)
    | null = null;

  onStateChange = (
    listener: (state: "disconnected" | "connecting" | "connected") => void,
  ) => {
    this.stateListener = listener;
    return () => {
      if (this.stateListener === listener) this.stateListener = null;
    };
  };

  connect() {
    this.state = "connected";
    this.stateListener?.("connected");
  }
}

afterEach(cleanup);

describe("usePermissionRequests", () => {
  it("requests pending decisions after a channel finishes connecting", () => {
    const channel = new ReconnectingPanelChannel();
    renderHook(() => usePermissionRequests(channel));

    expect(channel.sent).toEqual([]);
    act(() => channel.connect());
    expect(channel.sent).toEqual([
      { type: "permissions_pending_list", payload: {} },
    ]);
  });

  it("restores pending requests after reconnect and de-duplicates live replay", () => {
    const channel = new FakePanelChannel();
    const { result } = renderHook(() => usePermissionRequests(channel));

    expect(channel.sent).toEqual([
      { type: "permissions_pending_list", payload: {} },
    ]);

    const first = {
      request_id: "request-1",
      category: "shell",
      summary: "运行项目任务",
      params: { tool: "workflow_spawn" },
      default_action: "prompt",
      dangerous: false,
      session_id: "session-1",
      run_id: "run-1",
    };
    const second = {
      ...first,
      request_id: "request-2",
      summary: "运行第二个任务",
    };

    act(() => {
      channel.emit({
        type: "permissions_pending_list_response",
        payload: { pending: [first] },
      });
      channel.emit({ type: "permission_request", payload: first });
      channel.emit({ type: "permission_request", payload: second });
    });

    expect(result.current.current?.request_id).toBe("request-1");
    act(() => result.current.resolve("allow"));

    expect(channel.sent[1]).toMatchObject({
      type: "permission_response",
      payload: {
        request_id: "request-1",
        decision: "allow",
        session_id: "session-1",
        run_id: "run-1",
      },
    });
    expect(result.current.current?.request_id).toBe("request-1");
    expect(result.current.resolving).toBe(true);
    act(() => {
      channel.emit({
        type: "permission_response_applied",
        payload: { ok: true, request_id: "request-1" },
      });
    });
    expect(result.current.current?.request_id).toBe("request-2");
  });

  it("uses decision identity and never reopens a resolved replay", () => {
    const channel = new FakePanelChannel();
    const { result } = renderHook(() => usePermissionRequests(channel));
    const first = {
      request_id: "root-request",
      decision_id: "decision-1",
      category: "shell",
      summary: "first",
      params: {},
      default_action: "prompt",
      dangerous: false,
      session_id: "session-1",
      run_id: "run-1",
    };
    const second = {
      ...first,
      decision_id: "decision-2",
      summary: "second",
    };

    act(() => {
      channel.emit({ type: "permission_request", payload: first });
      channel.emit({ type: "permission_request", payload: second });
    });
    expect(result.current.current?.decision_id).toBe("decision-1");

    act(() => result.current.resolve("deny"));
    act(() => {
      channel.emit({
        type: "permission_response_applied",
        payload: { ok: true, decision_id: "decision-1" },
      });
    });
    expect(result.current.current?.decision_id).toBe("decision-2");

    act(() => {
      channel.emit({ type: "permission_request", payload: first });
    });
    expect(result.current.current?.decision_id).toBe("decision-2");
  });

  it("advances when the same Run opens its next decision before the prior ACK", () => {
    const channel = new FakePanelChannel();
    const { result } = renderHook(() => usePermissionRequests(channel));
    const first = {
      request_id: "request-race-1",
      decision_id: "decision-race-1",
      category: "shell",
      summary: "first",
      params: { tool_name: "run_shell" },
      default_action: "prompt",
      dangerous: true,
      session_id: "session-race",
      run_id: "run-race",
    };
    const second = {
      ...first,
      request_id: "request-race-2",
      decision_id: "decision-race-2",
      summary: "second",
      params: { tool_name: "workflow_spawn" },
    };

    act(() => channel.emit({ type: "permission_request", payload: first }));
    act(() => result.current.resolve("allow"));
    expect(result.current.resolving).toBe(true);

    act(() => {
      channel.emit({ type: "permission_request", payload: second });
    });

    expect(result.current.current?.decision_id).toBe("decision-race-2");
    expect(result.current.resolving).toBe(false);

    // The delayed ACK for the already superseded card is harmless.
    act(() => {
      channel.emit({
        type: "permission_response_applied",
        payload: { ok: true, decision_id: "decision-race-1" },
      });
    });
    expect(result.current.current?.decision_id).toBe("decision-race-2");
  });

  it("closes a submitted card when its exact tool outcome arrives before the ACK", () => {
    const channel = new FakePanelChannel();
    const { result } = renderHook(() => usePermissionRequests(channel));
    const request = {
      request_id: "request-tool-result",
      decision_id: "decision-tool-result",
      category: "shell",
      summary: "run shell",
      // The current durable projection keeps call_id in the prompt params;
      // newer producers may also duplicate it at the top level.
      params: { tool_name: "run_shell", call_id: "call-result" },
      default_action: "prompt",
      dangerous: true,
      session_id: "session-result",
      run_id: "run-result",
    };

    act(() => channel.emit({ type: "permission_request", payload: request }));
    act(() => result.current.resolve("allow"));
    expect(result.current.resolving).toBe(true);

    // Neither a different Run nor a different call may retire the card.
    act(() => {
      channel.emit({
        type: "tool_result",
        payload: { run_id: "other-run", call_id: "call-result", ok: true },
      });
      channel.emit({
        type: "tool_result",
        payload: { run_id: "run-result", call_id: "other-call", ok: true },
      });
    });
    expect(result.current.current?.decision_id).toBe("decision-tool-result");
    expect(result.current.resolving).toBe(true);

    act(() => {
      channel.emit({
        type: "tool_result",
        payload: { run_id: "run-result", call_id: "call-result", ok: true },
      });
    });
    expect(result.current.current).toBeNull();
    expect(result.current.resolving).toBe(false);

    // A delayed ACK remains idempotent.
    act(() => {
      channel.emit({
        type: "permission_response_applied",
        payload: { ok: true, decision_id: "decision-tool-result" },
      });
    });
    expect(result.current.current).toBeNull();
  });

  it("keeps the popup open when the backend rejects the decision", () => {
    const channel = new FakePanelChannel();
    const { result } = renderHook(() => usePermissionRequests(channel));
    act(() => {
      channel.emit({
        type: "permission_request",
        payload: {
          request_id: "request-retry",
          decision_id: "decision-retry",
          category: "shell",
          summary: "retry me",
          params: {},
          default_action: "prompt",
          dangerous: false,
          session_id: "session-1",
          run_id: "run-1",
        },
      });
    });

    act(() => result.current.resolve("allow"));
    act(() => {
      channel.emit({
        type: "permission_response_applied",
        payload: {
          ok: false,
          decision_id: "decision-retry",
          error: { message: "decision fence expired" },
        },
      });
    });

    expect(result.current.current?.decision_id).toBe("decision-retry");
    expect(result.current.resolving).toBe(false);
    expect(result.current.resolveError).toBe("decision fence expired");
  });

  it("clears the permission pill when a live Run decision is sent", () => {
    const channel = new FakePanelChannel();
    const store = useSessionsStore.getState();
    store.upsert_run_projection("session-resume", "run-resume", {
      status: "waiting",
      inflight: false,
    });
    store.upsert("session-resume", { status: "permission", inflight: false });
    const { result } = renderHook(() => usePermissionRequests(channel));

    act(() => {
      channel.emit({
        type: "permission_request",
        payload: {
          request_id: "request-resume",
          decision_id: "decision-resume",
          category: "shell",
          summary: "start child",
          params: {},
          default_action: "prompt",
          dangerous: false,
          session_id: "session-resume",
          run_id: "run-resume",
        },
      });
    });
    act(() => result.current.resolve("allow"));
    expect(result.current.resolving).toBe(true);
    act(() => {
      channel.emit({
        type: "permission_response_applied",
        payload: { ok: true, decision_id: "decision-resume" },
      });
    });

    expect(useSessionsStore.getState().sessions["session-resume"]).toMatchObject({
      status: "running",
      inflight: true,
      run_projections: {
        "run-resume": { status: "running", inflight: true },
      },
    });
  });

  it("waits for interrupt ACK and clears every queued decision for that Run", () => {
    const channel = new FakePanelChannel();
    const { result } = renderHook(() => usePermissionRequests(channel));
    const first = {
      request_id: "request-stop-1",
      decision_id: "decision-stop-1",
      category: "shell",
      summary: "first",
      params: {},
      default_action: "prompt",
      dangerous: false,
      session_id: "session-stop",
      run_id: "run-stop",
    };
    const second = {
      ...first,
      request_id: "request-stop-2",
      decision_id: "decision-stop-2",
      summary: "second",
    };

    act(() => {
      channel.emit({ type: "permission_request", payload: first });
      channel.emit({ type: "permission_request", payload: second });
    });
    act(() => result.current.stopCurrentRun());

    expect(channel.sent.at(-1)).toEqual({
      type: "chat_v2_interrupt",
      payload: { session_id: "session-stop", run_id: "run-stop" },
    });
    expect(result.current.current?.decision_id).toBe("decision-stop-1");
    expect(result.current.resolving).toBe(true);

    act(() => {
      channel.emit({
        type: "chat_v2_interrupted",
        payload: { run_id: "run-stop", cancelled: true },
      });
    });
    expect(result.current.current).toBeNull();
    expect(result.current.resolving).toBe(false);

    act(() => {
      channel.emit({ type: "permission_request", payload: first });
      channel.emit({ type: "permission_request", payload: second });
    });
    expect(result.current.current).toBeNull();
  });

  it("keeps the popup open when interrupt ACK reports cancellation failure", () => {
    const channel = new FakePanelChannel();
    const { result } = renderHook(() => usePermissionRequests(channel));
    act(() => {
      channel.emit({
        type: "permission_request",
        payload: {
          request_id: "request-stop-failed",
          category: "shell",
          summary: "stop me",
          params: {},
          default_action: "prompt",
          dangerous: false,
          session_id: "session-stop",
          run_id: "run-stop-failed",
        },
      });
      result.current.stopCurrentRun();
      channel.emit({
        type: "chat_v2_interrupted",
        payload: { run_id: "run-stop-failed", cancelled: false },
      });
    });
    expect(result.current.current?.run_id).toBe("run-stop-failed");
    expect(result.current.resolving).toBe(false);
    expect(result.current.resolveError).toBe("停止任务失败，请重试");
  });
});
