// @vitest-environment jsdom
// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * MM-D4 回归锁：「自动模式（推荐）」复选框必须渲染后端权威策略
 * （workflow.db / CapabilityStore），不得从 localStorage 缓存或任何默认值
 * 起手；快照到达前不可交互；写入必须显式携带目标模式。
 */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  AutoModeToggle,
  buildAutoModeSetMessage,
  readAutoModePolicy,
} from "./SettingsPanel";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ControlChannel, ConnectionState } from "../ws/ControlChannel";

class FakeControlChannel {
  state: ConnectionState = "connected";
  sent: ControlMessage[] = [];
  sendable = true;
  latest = new Map<string, IncomingMessage>();
  private listeners = new Set<(message: IncomingMessage) => void>();

  send(message: ControlMessage) {
    if (!this.sendable) return false; // ControlChannel 在 socket 未 OPEN 时静默丢帧
    this.sent.push(message);
    return true;
  }

  onMessage(listener: (message: IncomingMessage) => void) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onStateChange() {
    return () => undefined;
  }

  getLatestMessage(type: string): IncomingMessage | null {
    return this.latest.get(type) ?? null;
  }

  emit(message: IncomingMessage) {
    this.latest.set(message.type, message);
    for (const listener of this.listeners) listener(message);
  }
}

function policyResponse(
  mode: "auto" | "manual",
  generation: number,
): IncomingMessage {
  return {
    type: "permission_auto_mode_response",
    payload: {
      enabled: mode === "auto",
      mode,
      generation,
      provenance: generation === 0 ? "factory_default" : "user_explicit",
      authoritative: true,
    },
  } as IncomingMessage;
}

function renderToggle(channel: FakeControlChannel) {
  render(
    <AutoModeToggle getChannel={() => channel as unknown as ControlChannel} />,
  );
  return screen.getByTestId("permission-auto-mode") as HTMLInputElement;
}

describe("AutoModeToggle（MM-D4）", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it("快照到达前处于加载态：未勾选且不可交互", () => {
    const channel = new FakeControlChannel();
    const box = renderToggle(channel);

    expect(box.disabled).toBe(true);
    expect(box.checked).toBe(false);
    expect(
      screen.getByTestId("permission-auto-mode-status").textContent,
    ).toBe("正在读取授权策略…");
    expect(channel.sent.map((m) => m.type)).toEqual([
      "permission_auto_mode_get",
    ]);
  });

  it("后端 auto → 勾选；后端 manual → 不勾选（渲染代次与来源）", () => {
    const channel = new FakeControlChannel();
    const box = renderToggle(channel);

    act(() => channel.emit(policyResponse("auto", 0)));
    expect(box.checked).toBe(true);
    expect(box.disabled).toBe(false);
    expect(
      screen.getByTestId("permission-auto-mode-status").textContent,
    ).toBe("当前：自动（策略代次 0 · factory_default）");

    act(() => channel.emit(policyResponse("manual", 1)));
    expect(box.checked).toBe(false);
    expect(
      screen.getByTestId("permission-auto-mode-status").textContent,
    ).toBe("当前：手动（策略代次 1 · user_explicit）");
  });

  it("localStorage 缓存与后端相反时，以后端为准（缓存不再作为初始值）", () => {
    localStorage.setItem("deskpet.auto_mode", "false");
    const channel = new FakeControlChannel();
    const box = renderToggle(channel);

    // 缓存说 manual，但加载态仍然是"不可交互"，而不是"已呈现 manual"。
    expect(box.disabled).toBe(true);

    act(() => channel.emit(policyResponse("auto", 0)));
    expect(box.checked).toBe(true);
    expect(localStorage.getItem("deskpet.auto_mode")).toBe("true");
  });

  it("写入携带用户点击的目标模式，而不是翻转本地值", () => {
    const channel = new FakeControlChannel();
    const box = renderToggle(channel);

    act(() => channel.emit(policyResponse("auto", 0)));
    channel.sent.length = 0;

    fireEvent.click(box); // auto → 用户要 manual
    const written = channel.sent.at(-1)!;
    expect(written).toEqual(
      buildAutoModeSetMessage(false, String(written.request_id)),
    );
    expect(written.payload).toEqual({ enabled: false });
    expect(
      screen.getByTestId("permission-auto-mode-status").textContent,
    ).toBe("保存中…");

    // 写入后重新渲染的是落盘状态 + 代次，而不是乐观值。
    act(() => channel.emit(policyResponse("manual", 1)));
    expect(box.checked).toBe(false);
    expect(
      screen.getByTestId("permission-auto-mode-status").textContent,
    ).toBe("当前：手动（策略代次 1 · user_explicit）");

    fireEvent.click(box); // manual → 用户要 auto
    expect(channel.sent.at(-1)!.payload).toEqual({ enabled: true });
  });

  it("加载态下的点击不会写后端", () => {
    const channel = new FakeControlChannel();
    const box = renderToggle(channel);
    channel.sent.length = 0;

    fireEvent.click(box);
    expect(channel.sent).toHaveLength(0);
  });

  it("落后代次的快照不会把界面拉回写前状态", () => {
    const channel = new FakeControlChannel();
    const box = renderToggle(channel);

    act(() => channel.emit(policyResponse("manual", 3)));
    expect(box.checked).toBe(false);

    act(() => channel.emit(policyResponse("auto", 2)));
    expect(box.checked).toBe(false);
  });

  it("重挂载时先用通道缓存的最新快照渲染", () => {
    const channel = new FakeControlChannel();
    channel.latest.set(
      "permission_auto_mode_response",
      policyResponse("manual", 4),
    );
    const box = renderToggle(channel);

    expect(box.disabled).toBe(false);
    expect(box.checked).toBe(false);
  });

  it("readAutoModePolicy 兼容只有 enabled 的旧回执", () => {
    expect(
      readAutoModePolicy({
        type: "permission_auto_mode_response",
        payload: { enabled: true },
      } as IncomingMessage),
    ).toEqual({ mode: "auto", generation: 0, provenance: "unknown" });
  });
});
