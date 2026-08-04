// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAudioChannel } from "./useAudioChannel";

describe("useAudioChannel disabled mode", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not construct a WebSocket or connect when voice is disabled", () => {
    const websocket = vi.fn();
    vi.stubGlobal("WebSocket", websocket);

    const { result } = renderHook(() =>
      useAudioChannel(8100, "secret", false),
    );

    expect(websocket).not.toHaveBeenCalled();
    expect(result.current.state).toBe("disconnected");
    expect(result.current.lastMessage).toBeNull();
    expect(result.current.getChannel()).toBeNull();
  });
});
