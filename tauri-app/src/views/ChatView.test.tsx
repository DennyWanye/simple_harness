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
import { useSessionsStore } from "../stores/sessionsStore";
import { controlWS } from "../code-panel/controlWs";
import { useProvidersStore } from "../code-panel/providersStore";
import { useSessionModelsStore } from "../code-panel/sessionModelsStore";

const realtimeVoiceMock = vi.hoisted(() => ({
  start: vi.fn(async () => undefined),
  hangUp: vi.fn(),
}));

vi.mock("../hooks/useRealtimeVoice", () => ({
  useRealtimeVoice: () => ({
    state: "idle",
    errorCode: null,
    transcript: "",
    responseText: "",
    isRecording: false,
    isPlaying: false,
    start: realtimeVoiceMock.start,
    hangUp: realtimeVoiceMock.hangUp,
  }),
}));

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
    MessageStreamPanel: (props: {
      chatMessages?: Array<{ role: string; text?: string }>;
    }) => (
      <div data-testid="stub-message-stream">
        {(props.chatMessages ?? []).map((message, index) => (
          <span
            key={`${message.role}:${message.text ?? ""}:${index}`}
            data-stream-role={message.role}
          >
            {message.text}
          </span>
        ))}
      </div>
    ),
  };
});
vi.mock("../chat/HarnessInspectorPanel", () => ({
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
    // InputBar 挂载即 fetch /api/commands/help；真 fetch 打 localhost 会在
    // 测试环境卸载后才 settle（unhandled rejection）。同步 stub 掉。
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false }) as Response),
    );
    listeners = [];
    useSessionsStore.setState({ companion_owner: null });
    useProvidersStore.setState({ providers: [] });
    useSessionModelsStore.getState().set_catalog([], "none", "");
    realtimeVoiceMock.start.mockClear();
    realtimeVoiceMock.hangUp.mockClear();
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

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  // 2026-08-09 真机冷启动回归：取消保留会话 `default` 后，首启时
  // store 里零会话、activeSid 为空串。当时 messages 的兜底写成
  // `?? []`——selector 每次渲染都造新数组，zustand 按引用比较判定快照恒变化，
  // ChatView 无限重渲，React 报 Maximum update depth exceeded，
  // 连带 backend 都没能 spawn。兜底改成模块级常量后修复。
  // 本用例以前抓不到，因为所有 ChatView 测试都传了一个**存在的** sid。
  it("空态（零会话 + activeSid 为空）能稳定渲染，不触发无限重渲", () => {
    useSessionsStore.setState({ active_sid: "", sessions: {} });
    expect(() => render(<ChatView activeSid="" secret="s3cret" />)).not.toThrow();
    // 空态没有会话可回灌，不应发出任何 hydration 包。
    const hydration = vi
      .mocked(controlWS.send)
      .mock.calls.map(([m]) => (m as { type: string }).type)
      .filter((t) => t === "session_messages_load");
    expect(hydration).toEqual([]);
  });

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

  it("模型按钮直接显示当前 Provider 的模型名称", () => {
    useProvidersStore.setState({
      providers: [
        {
          id: "relay",
          name: "Relay",
          enabled: true,
          default_model: "gpt-5.6-sol",
          models: ["gpt-5.6-sol"],
        },
      ],
    });

    render(<ChatView activeSid="default" secret="s3cret" />);

    const button = screen.getByTestId("chat-model-button");
    expect(button.textContent).toContain("gpt-5.6-sol");
    expect(button.textContent).not.toContain("默认模型");
    expect(button.title).toBe("模型与参数（当前 gpt-5.6-sol）");
    expect(screen.queryByTitle("Session ID，可选中复制")).toBeNull();
  });

  it("身份就绪后输入回车 → 发送走 controlWS 的 chat_v2", () => {
    render(<ChatView activeSid="default" secret="s3cret" />);
    act(() => {
      useSessionsStore.getState().set_companion_owner({
        profile_id: "local-profile",
        profile_generation: 1,
      });
    });
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

  it("身份广播早于挂载时仍从 store authority 恢复输入可用", () => {
    useSessionsStore.setState({
      companion_owner: { profile_id: "local-profile", profile_generation: 1 },
    });
    render(<ChatView activeSid="default" secret="s3cret" />);
    expect(screen.getByPlaceholderText("输入消息，Enter 发送…")).toBeTruthy();
  });

  it("只有一个电话式 Realtime 主按钮，点击才开始通话", () => {
    render(<ChatView activeSid="default" secret="s3cret" />);
    const call = screen.getByTestId("realtime-call-button") as HTMLButtonElement;
    expect(call.disabled).toBe(false);
    expect(call.textContent).toContain("开始通话");
    expect(screen.queryByText("开始说话")).toBeNull();
    fireEvent.click(call);
    expect(realtimeVoiceMock.start).toHaveBeenCalledTimes(1);
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
    expect(controlWS.send).toHaveBeenCalledWith(expect.objectContaining({
      type: "project_catalog_page",
      payload: expect.objectContaining({ pinned_session_id: "default" }),
    }));
  });

  it("后端未就绪（secret 为空）显示等待状态条", () => {
    render(<ChatView activeSid="default" secret="" />);
    expect(screen.getByTestId("chat-conn-status").textContent).toContain(
      "后端启动中",
    );
  });

  it("keeps observable tool/progress inputs independent and removes both when both are hidden", () => {
    const original = useSessionsStore.getState().sessions.default;
    useSessionsStore.setState((state) => ({
      ...state,
      sessions: {
        ...state.sessions,
        default: {
          ...original,
          messages: [{
            id: "summary-visible",
            role: "reasoning_summary",
            text: "公开工作叙述",
            run_id: "run-visible",
            reasoning_summary_id: "summary-visible",
            reasoning_status: "running",
            ts: 1_000,
          }, {
            id: "tool-call-visible",
            role: "tool_call",
            text: "",
            run_id: "run-visible",
            tool_name: "file_read",
            tool_args: { path: "README.md" },
            ts: 2_000,
          }, {
            id: "tool-result-visible",
            role: "tool_result",
            text: "",
            run_id: "run-visible",
            tool_name: "file_read",
            tool_ok: true,
            tool_result: "done",
            ts: 3_000,
          }, {
            id: "final-visible",
            role: "assistant",
            text: "最终回复",
            run_id: "run-visible",
            ts: 4_000,
          }],
        },
      },
    }));
    render(<ChatView activeSid="default" secret="s3cret" />);
    const streamRoles = () => Array.from(
      screen.getByTestId("stub-message-stream").querySelectorAll("[data-stream-role]"),
    ).map((element) => element.getAttribute("data-stream-role"));

    expect(streamRoles()).toEqual(["progress", "tool", "tool", "assistant"]);

    fireEvent.click(screen.getByRole("button", { name: "隐藏执行进度" }));
    expect(streamRoles()).toEqual(["tool", "tool", "assistant"]);

    fireEvent.click(screen.getByRole("button", { name: "显示执行进度" }));
    fireEvent.click(screen.getByRole("button", { name: "隐藏工具消息" }));
    expect(streamRoles()).toEqual(["progress", "assistant"]);

    fireEvent.click(screen.getByRole("button", { name: "隐藏执行进度" }));
    expect(streamRoles()).toEqual(["assistant"]);
  });
});
