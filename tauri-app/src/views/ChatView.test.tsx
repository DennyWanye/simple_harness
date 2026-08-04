// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * ChatView 测试（T8，WB-4）。
 *
 * 覆盖：发送走 controlWS（chat_v2 实际通道）/ 会话切换 hydration 四连发 /
 * mic 禁用占位存在（B9）/ 连接状态条（controlWS.state() 为源）。
 * controlWS 全 mock；重型子组件（消息流/巡检面板/上下文模态）stub 掉，
 * InputBar 用真件走真实发送路径。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatView } from "./ChatView";
import { controlWS } from "../code-panel/controlWs";
import { VOICE_UNAVAILABLE_MESSAGE } from "../voiceAvailability";

vi.mock("../code-panel/controlWs", () => ({
  controlWS: {
    send: vi.fn(() => true),
    send_command: vi.fn(() => true),
    send_companion_action: vi.fn(async () => true),
    on_message: vi.fn(),
    state: vi.fn(() => "connected"),
  },
}));

// 重型子组件 stub（本测试只验 ChatView 自身的链路与合同）。
vi.mock("../components/MessageStreamPanel", async (importOriginal) => {
  const mod = await importOriginal<Record<string, unknown>>();
  return {
    ...mod,
    MessageStreamPanel: () => <div data-testid="stub-message-stream" />,
  };
});
vi.mock("../message-panel/HarnessInspectorPanel", () => ({
  HarnessInspectorPanel: (props: { open: boolean }) =>
    props.open ? <div data-testid="stub-harness-panel" /> : null,
}));
vi.mock("../components/ContextBreakdownModal", () => ({
  ContextBreakdownModal: () => null,
}));

let listeners: Array<(msg: unknown) => void> = [];

function emit(msg: unknown) {
  act(() => {
    for (const fn of [...listeners]) fn(msg);
  });
}

describe("ChatView（WB-4）", () => {
  beforeEach(() => {
    listeners = [];
    vi.mocked(controlWS.send).mockClear();
    vi.mocked(controlWS.send).mockReturnValue(true);
    vi.mocked(controlWS.state).mockReturnValue("connected");
    vi.mocked(controlWS.on_message).mockImplementation(
      (fn: (msg: unknown) => void) => {
        listeners.push(fn);
        return () => {
          listeners = listeners.filter((l) => l !== fn);
        };
      },
    );
  });

  afterEach(cleanup);

  it("挂载即按会话发 hydration 四连发（消息/投影/上下文/provider）", () => {
    render(<ChatView activeSid="default" secret="s3cret" />);
    const types = vi
      .mocked(controlWS.send)
      .mock.calls.map(([m]) => (m as { type: string }).type);
    expect(types).toEqual(
      expect.arrayContaining([
        "session_messages_load",
        "task_projections_list",
        "context_usage_request",
        "session_provider_get",
      ]),
    );
    const hydration = vi
      .mocked(controlWS.send)
      .mock.calls.map(([m]) => m as { type: string; payload?: { session_id?: string } })
      .filter((m) => m.type === "session_messages_load");
    expect(hydration[0]?.payload?.session_id).toBe("default");
  });

  it("身份就绪后输入回车 → 发送走 controlWS 的 chat_v2", () => {
    render(<ChatView activeSid="default" secret="s3cret" />);
    emit({ type: "companion_identity_status", payload: { ready: true } });
    // 就绪促升（companion_action_ready）由 ChatView 独家发送。
    expect(controlWS.send_companion_action).toHaveBeenCalledWith(
      "companion_action_ready",
      { ready: true },
    );

    vi.mocked(controlWS.send).mockClear();
    const input = screen.getByPlaceholderText("输入消息，Enter 发送…");
    fireEvent.change(input, { target: { value: "工作台你好" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });
    expect(controlWS.send).toHaveBeenCalledWith({
      type: "chat_v2",
      payload: expect.objectContaining({
        text: "工作台你好",
        session_id: "default",
        request_id: expect.any(String),
        turn_id: expect.any(String),
      }),
    });
  });

  it("mic 禁用占位存在（B9：tooltip 说明语音待 Realtime 接入）", () => {
    render(<ChatView activeSid="default" secret="s3cret" />);
    const mic = screen.getByTestId("chat-mic-disabled") as HTMLButtonElement;
    expect(mic.disabled).toBe(true);
    expect(mic.title).toBe(VOICE_UNAVAILABLE_MESSAGE);
  });

  it("Harness 巡检面板默认关，🐞 开关可打开", () => {
    render(<ChatView activeSid="default" secret="s3cret" />);
    expect(screen.queryByTestId("stub-harness-panel")).toBeNull();
    fireEvent.click(screen.getByTestId("harness-inspector-toggle"));
    expect(screen.getByTestId("stub-harness-panel")).toBeTruthy();
  });

  it("连接状态源=controlWS.state()：断开显示状态条与重试入口", () => {
    vi.mocked(controlWS.state).mockReturnValue("disconnected");
    render(<ChatView activeSid="default" secret="s3cret" />);
    const bar = screen.getByTestId("chat-conn-status");
    expect(bar.textContent).toContain("已断开");
    vi.mocked(controlWS.send).mockClear();
    fireEvent.click(screen.getByTestId("chat-conn-retry"));
    // 重试入口通过 send 触发内部 schedule_reconnect。
    expect(controlWS.send).toHaveBeenCalledWith({ type: "sessions_list" });
  });

  it("后端未就绪（secret 为空）显示等待状态条", () => {
    render(<ChatView activeSid="default" secret="" />);
    expect(screen.getByTestId("chat-conn-status").textContent).toContain(
      "后端启动中",
    );
  });
});
