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
import { useSessionsStore } from "../stores/sessionsStore";

vi.mock("../code-panel/controlWs", () => ({
  // 占位值即可：被测逻辑只用它做"不是控制通道自己的 sid"这一层排除判断，
  // 不依赖具体字符串。真实常量的值由 controlWs.ts 独家持有（D1 历史命名），
  // 这里刻意不复刻，避免同一字符串散落到第二个文件。
  CONTROL_SESSION_ID: "control-channel-sid",
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
    session_id: "s-older",
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
    useSessionsStore.setState({ companion_owner: null });
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
    render(<SessionList activeSid="s-older" onSwitchSid={() => {}} />);
    expect(controlWS.send).toHaveBeenCalledWith({ type: "sessions_list" });

    emit({ type: "sessions_list_response", payload: { sessions: [] } });
    expect(screen.getByTestId("session-list-empty").textContent).toContain(
      "暂无历史会话",
    );
  });

  it("渲染会话列表：自定义标题优先、时间倒序", () => {
    render(<SessionList activeSid="s-older" onSwitchSid={() => {}} />);
    emit({ type: "sessions_list_response", payload: { sessions: SESSIONS } });

    const rows = screen
      .getAllByTestId(/^session-row-/)
      .map((el) => el.getAttribute("data-testid"));
    // last_message_at 倒序：s-newer(200) 在 s-older(100) 之前。
    expect(rows).toEqual(["session-row-s-newer", "session-row-s-older"]);
    expect(screen.getByText("我的标题")).toBeTruthy();
    // 无自定义标题 → 回落到 preview（不再有「默认话题」这种特权标签）。
    expect(screen.getByText("旧的默认话题")).toBeTruthy();
    expect(screen.queryByTestId("session-list-empty")).toBeNull();
  });

  /**
   * TC-WB-05 步骤1 要求条目含「标题（或首句摘要）**与时间**」。r6 真机复测发现
   * 后端 last_message_at 一直下发、排序也在用，但行内从没渲染出来 —— 这条测试
   * 钉住修复后的行为，防止再被摘掉。
   */
  it("会话行渲染相对时间（TC-WB-05 步骤1）", () => {
    // 用真实时钟相对构造：小时/天粒度不会因用例跑的这几毫秒翻档，
    // 比 setSystemTime 少一层 fake timers 依赖。
    const nowSec = Date.now() / 1000;
    render(<SessionList activeSid="s-older" onSwitchSid={() => {}} />);
    emit({
      type: "sessions_list_response",
      payload: {
        sessions: [
          { ...SESSIONS[1], last_message_at: nowSec - 14 * 3600 },
          { ...SESSIONS[0], last_message_at: nowSec - 3 * 86400 },
        ],
      },
    });

    expect(screen.getByText("14h 前")).toBeTruthy();
    expect(screen.getByText("3d 前")).toBeTruthy();
  });

  it("last_message_at 缺失时不渲染时间块（不出现 1970 噪声）", () => {
    render(<SessionList activeSid="s-older" onSwitchSid={() => {}} />);
    emit({
      type: "sessions_list_response",
      payload: { sessions: [{ ...SESSIONS[1], last_message_at: 0 }] },
    });

    expect(screen.getByTestId("session-row-s-newer")).toBeTruthy();
    expect(screen.queryByText(/前$/)).toBeNull();
  });

  it("点击会话行触发 onSwitchSid 回调", () => {
    const onSwitch = vi.fn();
    render(<SessionList activeSid="s-older" onSwitchSid={onSwitch} />);
    emit({ type: "sessions_list_response", payload: { sessions: SESSIONS } });

    fireEvent.click(screen.getByTestId("session-switch-s-newer"));
    expect(onSwitch).toHaveBeenCalledWith("s-newer");
  });

  it("身份就绪后「新建会话」发 chat_v2+new_session 包（shape 校验）", () => {
    render(<SessionList activeSid="s-older" onSwitchSid={() => {}} />);

    // 身份未就绪 → 按钮禁用，点击不发包。
    const btn = screen.getByTestId("session-new-topic") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);

    act(() => {
      useSessionsStore.getState().set_companion_owner({
        profile_id: "local-profile",
        profile_generation: 1,
      });
    });
    expect(btn.disabled).toBe(false);

    vi.mocked(controlWS.send).mockClear();
    fireEvent.click(btn);
    expect(controlWS.send).toHaveBeenCalledWith({
      type: "chat_v2",
      payload: expect.objectContaining({
        session_id: "s-older",
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
    render(<SessionList activeSid="s-older" onSwitchSid={onSwitch} />);
    act(() => {
      useSessionsStore.getState().set_companion_owner({
        profile_id: "local-profile",
        profile_generation: 1,
      });
    });
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

  it("身份广播早于挂载时新建会话入口仍从 store authority 启用", () => {
    useSessionsStore.setState({
      companion_owner: { profile_id: "local-profile", profile_generation: 1 },
    });
    render(<SessionList activeSid="s-older" onSwitchSid={() => {}} />);
    expect(
      (screen.getByTestId("session-new-topic") as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  // r4 S05 真机回归：后端 list_sessions_with_preview 以「已有消息」为会话进清单的
  // 条件，而 session_switched 触发的那次刷新时新会话还是空的，必然拉不到它。
  // 首轮消息收尾（chat_v2_final / chat_response）必须再刷一次，否则新建的会话
  // 在本次运行内永远不进侧栏、切走就回不去。
  it("一轮对话收尾后重新拉清单（新会话首条消息落库后才进得了列表）", () => {
    render(<SessionList activeSid="s-older" onSwitchSid={vi.fn()} />);
    emit({ type: "session_switched", payload: { new_sid: "s-born" } });

    vi.mocked(controlWS.send).mockClear();
    emit({ type: "chat_v2_final", payload: { session_id: "s-born" } });
    expect(controlWS.send).toHaveBeenCalledWith({ type: "sessions_list" });

    vi.mocked(controlWS.send).mockClear();
    emit({ type: "chat_response", payload: { session_id: "s-born" } });
    expect(controlWS.send).toHaveBeenCalledWith({ type: "sessions_list" });

    // 中间态事件不应造成清单抖动（每个 token/工具事件都刷会打爆控制通道）。
    vi.mocked(controlWS.send).mockClear();
    emit({ type: "tool_call", payload: { session_id: "s-born" } });
    emit({ type: "tool_result", payload: { session_id: "s-born" } });
    expect(controlWS.send).not.toHaveBeenCalledWith({ type: "sessions_list" });
  });
});
