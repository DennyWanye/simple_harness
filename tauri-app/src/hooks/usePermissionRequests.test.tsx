// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { usePermissionRequests } from "./usePermissionRequests";

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

afterEach(cleanup);

describe("usePermissionRequests", () => {
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
    expect(result.current.current?.request_id).toBe("request-2");
  });
});
