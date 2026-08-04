// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * SessionList 测试（T7，WB-5）。
 *
 * 覆盖：列表渲染（时间倒序）/ 空态引导 / 切换回调 / 新建会话发包 shape。
 * controlWS 全 mock —— 通过捕获 on_message 监听器回放后端消息。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionList } from "./SessionList";
import { controlWS } from "../code-panel/controlWs";

vi.mock("../code-panel/controlWs", () => ({
  controlWS: {
    send: vi.fn(() => true),
    send_command: vi.fn(() => true),
    send_companion_action: vi.fn(async () => true),
    on_message: vi.fn(),
    state: vi.fn(() => "connected"),
  },
}));

/** 捕获全部 on_message 监听器，测试里对其回放后端消息。 */
let listeners: Array<(msg: unknown) => void> = [];

function emit(msg: unknown) {
  act(() => {
    for (const fn of [...listeners]) fn(msg);
  });
}

const SESSIONS = [
  {
    session_id: "default",
    turn_count: 4,
    last_message_at: 100,
    preview: "旧的默认话题",
    title: "",
  },
  {
    session_id: "s-newer",
    turn_count: 2,
    last_message_at: 200,
    preview: "较新的会话",
    title: "我的标题",
  },
];

describe("SessionList（WB-5）", () => {
  beforeEach(() => {
    listeners = [];
    vi.mocked(controlWS.send).mockClear();
    vi.mocked(controlWS.send).mockReturnValue(true);
    vi.mocked(controlWS.on_message).mockImplementation((fn: (msg: unknown) => void) => {
      listeners.push(fn);
      return () => {
        listeners = listeners.filter((l) => l !== fn);
      };
    });
  });

  afterEach(cleanup);

  it("挂载即拉取清单；空清单显示空态引导", () => {
    render(<SessionList activeSid="default" onSwitchSid={() => {}} />);
    expect(controlWS.send).toHaveBeenCalledWith({ type: "sessions_list" });

    emit({ type: "sessions_list_response", payload: { sessions: [] } });
    expect(screen.getByTestId("session-list-empty").textContent).toContain(
      "暂无历史会话",
    );
  });

  it("渲染会话列表：自定义标题优先、时间倒序", () => {
    render(<SessionList activeSid="default" onSwitchSid={() => {}} />);
    emit({ type: "sessions_list_response", payload: { sessions: SESSIONS } });

    const rows = screen
      .getAllByTestId(/^session-row-/)
      .map((el) => el.getAttribute("data-testid"));
    // last_message_at 倒序：s-newer(200) 在 default(100) 之前。
    expect(rows).toEqual(["session-row-s-newer", "session-row-default"]);
    expect(screen.getByText("我的标题")).toBeTruthy();
    expect(screen.getByText("默认话题")).toBeTruthy();
    expect(screen.queryByTestId("session-list-empty")).toBeNull();
  });

  it("点击会话行触发 onSwitchSid 回调", () => {
    const onSwitch = vi.fn();
    render(<SessionList activeSid="default" onSwitchSid={onSwitch} />);
    emit({ type: "sessions_list_response", payload: { sessions: SESSIONS } });

    fireEvent.click(screen.getByTestId("session-switch-s-newer"));
    expect(onSwitch).toHaveBeenCalledWith("s-newer");
  });

  it("身份就绪后「新建会话」发 chat_v2+new_session 包（shape 校验）", () => {
    render(<SessionList activeSid="default" onSwitchSid={() => {}} />);

    // 身份未就绪 → 按钮禁用，点击不发包。
    const btn = screen.getByTestId("session-new-topic") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);

    emit({ type: "companion_identity_status", payload: { ready: true } });
    expect(btn.disabled).toBe(false);

    vi.mocked(controlWS.send).mockClear();
    fireEvent.click(btn);
    expect(controlWS.send).toHaveBeenCalledWith({
      type: "chat_v2",
      payload: expect.objectContaining({
        session_id: "default",
        new_session: true,
        text: "",
        request_id: expect.any(String),
        turn_id: expect.any(String),
      }),
    });
    // 发包成功 → pending 态（按钮禁用防连点）。
    expect(btn.disabled).toBe(true);
  });

  it("session_switched 消解 pending、刷新清单并上抛新 sid", () => {
    const onSwitch = vi.fn();
    render(<SessionList activeSid="default" onSwitchSid={onSwitch} />);
    emit({ type: "companion_identity_status", payload: { ready: true } });
    fireEvent.click(screen.getByTestId("session-new-topic"));

    vi.mocked(controlWS.send).mockClear();
    emit({ type: "session_switched", payload: { new_sid: "s-born" } });

    expect(onSwitch).toHaveBeenCalledWith("s-born");
    // 新会话诞生 → 重新拉清单。
    expect(controlWS.send).toHaveBeenCalledWith({ type: "sessions_list" });
    expect(
      (screen.getByTestId("session-new-topic") as HTMLButtonElement).disabled,
    ).toBe(false);
  });
});
