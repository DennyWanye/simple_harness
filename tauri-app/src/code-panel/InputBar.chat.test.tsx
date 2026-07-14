// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InputBar } from "./InputBar";
import { useSessionsStore } from "../stores/sessionsStore";
import { codePanelWS } from "./ws";

vi.mock("./ws", () => ({
  codePanelWS: {
    send: vi.fn(() => true),
    on_message: vi.fn(() => () => {}),
    state: vi.fn(() => "connected"),
  },
}));

function resetStore() {
  useSessionsStore.setState({
    active_sid: "default",
    sessions: {
      default: {
        base_session_id: "default",
        code_session_id: null,
        project_root: null,
        project_name: "(untitled)",
        messages: [],
        todos: [],
        token_usage: { prompt: 0, completion: 0 },
        context_usage: null,
        status: "idle",
        last_activity: Date.now(),
        inflight: false,
        current_iteration: 0,
        max_iterations: 50,
        tool_signature_repeat: 0,
        supervisor_severity: "green",
        supervisor_alert: null,
        supervisor_inbox: [],
        auto_resume_attempts: 0,
        provider_id: null,
        preferred_model: null,
        model_params: null,
      },
    },
    inflight_count: 0,
    inflight_max: 2,
  });
}

describe("InputBar chat send", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.mocked(codePanelWS.send).mockClear();
    vi.mocked(codePanelWS.on_message).mockClear();
    vi.mocked(codePanelWS.send).mockReturnValue(true);
    resetStore();
  });

  it("sends chat_v2 immediately when Enter is pressed", () => {
    render(<InputBar sessionId="default" placeholder="chat" />);

    const input = screen.getByPlaceholderText("chat");
    fireEvent.change(input, { target: { value: "你好啊" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });

    expect(codePanelWS.send).toHaveBeenCalledWith({
      type: "chat_v2",
      payload: { text: "你好啊", session_id: "default" },
    });
    expect(useSessionsStore.getState().sessions.default.messages.at(-1)?.text).toBe(
      "你好啊",
    );
  });

  it("clears thinking when the control channel rejects the send", () => {
    vi.mocked(codePanelWS.send).mockReturnValue(false);
    render(<InputBar sessionId="default" placeholder="chat" />);

    const input = screen.getByPlaceholderText("chat");
    fireEvent.change(input, { target: { value: "hello" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });

    const session = useSessionsStore.getState().sessions.default;
    expect(session.inflight).toBe(false);
    expect(session.status).toBe("error");
    expect(session.messages.at(-1)?.role).toBe("error");
  });

  it("creates an empty new topic when the input is blank", () => {
    render(<InputBar sessionId="default" placeholder="chat" />);

    fireEvent.click(screen.getByRole("button", { name: "新话题" }));

    expect(codePanelWS.send).toHaveBeenCalledWith({
      type: "chat_v2",
      payload: { session_id: "default", new_session: true, text: "" },
    });
    expect((screen.getByRole("button", { name: "创建中" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("uses the current draft as the first message in a new topic", () => {
    render(<InputBar sessionId="default" placeholder="chat" />);

    const input = screen.getByPlaceholderText("chat");
    fireEvent.change(input, { target: { value: "帮我重新开一个话题" } });

    const button = screen.getByRole("button", { name: "作为新话题发送" });
    expect(button.getAttribute("title")).toBe("用当前输入开启一个新话题");

    fireEvent.click(button);

    expect(codePanelWS.send).toHaveBeenCalledWith({
      type: "chat_v2",
      payload: {
        session_id: "default",
        new_session: true,
        text: "帮我重新开一个话题",
      },
    });
    expect((input as HTMLTextAreaElement).value).toBe("");
  });

  it("restores the new topic button and reports an error when creation send fails", () => {
    vi.mocked(codePanelWS.send).mockReturnValue(false);
    render(<InputBar sessionId="default" placeholder="chat" />);

    const input = screen.getByPlaceholderText("chat");
    fireEvent.change(input, { target: { value: "失败时别吞掉这句" } });
    fireEvent.click(screen.getByRole("button", { name: "作为新话题发送" }));

    const session = useSessionsStore.getState().sessions.default;
    expect(
      (screen.getByRole("button", { name: "作为新话题发送" }) as HTMLButtonElement).disabled,
    ).toBe(false);
    expect((input as HTMLTextAreaElement).value).toBe("失败时别吞掉这句");
    expect(session.status).toBe("error");
    expect(session.messages.at(-1)?.role).toBe("error");
    expect(session.messages.at(-1)?.text).toContain("新话题创建失败");
  });
});
