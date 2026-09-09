// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WorkbenchShell 测试（T6，WB-3）。
 *
 * 覆盖：四视图切换、当前项高亮、紧凑（800×560 min）尺寸下的关键布局
 * 样式断言。jsdom 断言样式而非真布局 —— 真布局归真机测试（WB-3）。
 */
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// controlWS 单例 mock：Sidebar 连接徽章（useControlWsState）与 ChatView/
// SessionList 的订阅在 jsdom 下不真正开 socket（T13 聚合断言可控源）。
vi.mock("../code-panel/controlWs", () => ({
  controlWS: {
    send: vi.fn(() => true),
    send_command: vi.fn(() => true),
    send_companion_action: vi.fn(async () => true),
    on_message: vi.fn(() => () => {}),
    state: vi.fn(() => "connected"),
  },
}));

import { controlWS } from "../code-panel/controlWs";

// vitest 未开 globals — testing-library 的自动 cleanup 不生效，手动挂。
afterEach(cleanup);

// 2026-09-09 改版：令牌以 CSS 变量下发（`var(--sh-*, 兜底)`），jsdom 原样
// 保留字符串；断言直接比对令牌值，测试内仍零硬编码色值。

import {
  WorkbenchShell,
  type WorkbenchView,
} from "./WorkbenchShell";
import { SIDEBAR_WIDTH } from "./Sidebar";
import { dark } from "../theme/components";

function Harness({ initial = "chat" as WorkbenchView }) {
  const [view, setView] = useState<WorkbenchView>(initial);
  return (
    <WorkbenchShell
      view={view}
      onViewChange={setView}
      connectionState="connected"
      chatProps={{ activeSid: "default", secret: "" }}
      skillsProps={{ channel: null }}
      settingsProps={{
        getChannel: () => null,
        lastMessage: null,
        secret: "",
        onConfigChanged: () => undefined,
        autostart: { ready: false, enabled: false, toggle: () => undefined },
      }}
    />
  );
}

describe("WorkbenchShell 视图切换（WB-3）", () => {
  it("默认显示 chat 视图，其余视图不挂载", () => {
    render(<Harness />);
    expect(screen.getByTestId("view-chat")).toBeTruthy();
    expect(screen.queryByTestId("view-skills")).toBeNull();
    expect(screen.queryByTestId("view-artifacts")).toBeNull();
    expect(screen.queryByTestId("view-settings")).toBeNull();
  });

  it("点击侧栏导航在四个视图间切换；chat 常挂载（D3）", () => {
    render(<Harness />);

    fireEvent.click(screen.getByTestId("nav-skills"));
    expect(screen.getByTestId("view-skills")).toBeTruthy();
    // D3：chat 常挂载 — 切走后仍在 DOM，仅容器 display:none。
    const chatWrap = screen.getByTestId("view-chat")
      .parentElement as HTMLElement;
    expect(chatWrap.style.display).toBe("none");

    fireEvent.click(screen.getByTestId("nav-artifacts"));
    expect(screen.getByTestId("view-artifacts")).toBeTruthy();
    expect(screen.queryByTestId("view-skills")).toBeNull();

    fireEvent.click(screen.getByTestId("nav-settings"));
    expect(screen.getByTestId("view-settings")).toBeTruthy();
    expect(screen.queryByTestId("view-artifacts")).toBeNull();

    fireEvent.click(screen.getByTestId("nav-chat"));
    const chatWrapBack = screen.getByTestId("view-chat")
      .parentElement as HTMLElement;
    expect(chatWrapBack.style.display).not.toBe("none");
    expect(screen.queryByTestId("view-settings")).toBeNull();
  });

  it("当前项高亮：aria-current + 左侧 2px 强调条 + 强调色极淡面色", () => {
    render(<Harness />);
    const chatBtn = screen.getByTestId("nav-chat");
    const skillsBtn = screen.getByTestId("nav-skills");

    // 2026-09-09「克制的高级感」：选中态不再靠整块强调色文字/填充，
    // 改为 `.sh-nav-item` 的左侧 2px 强调条 + accentSoft 面色 + 正文色。
    expect(chatBtn.getAttribute("aria-current")).toBe("page");
    expect(chatBtn.className).toContain("sh-nav-item");
    expect(chatBtn.style.background).toBe(dark.accentSoft);
    expect(chatBtn.style.color).toBe(dark.text);

    expect(skillsBtn.getAttribute("aria-current")).toBeNull();
    expect(skillsBtn.style.background).toBe("transparent");
    expect(skillsBtn.style.color).toBe(dark.textMuted);

    // 所有项统一高度 36，hover/active/focus 由 CSS 三态负责（不再挂 JS）。
    expect(chatBtn.style.height).toBe("36px");
    expect(skillsBtn.className).toContain("sh-interactive");

    fireEvent.click(skillsBtn);
    expect(skillsBtn.getAttribute("aria-current")).toBe("page");
    expect(skillsBtn.style.background).toBe(dark.accentSoft);
    expect(chatBtn.getAttribute("aria-current")).toBeNull();
  });
});

describe("WorkbenchShell 紧凑尺寸样式（WB-3 min 800×560）", () => {
  it("侧栏固定宽度，内容区 flex:1 + min-width:0，壳不出横向滚动", () => {
    render(<Harness />);

    const sidebar = screen.getByTestId("workbench-sidebar");
    expect(sidebar.style.width).toBe(`${SIDEBAR_WIDTH}px`);
    expect(sidebar.style.minWidth).toBe(`${SIDEBAR_WIDTH}px`);
    expect(sidebar.style.maxWidth).toBe(`${SIDEBAR_WIDTH}px`);

    const content = screen.getByTestId("workbench-content");
    // flex:1 shorthand 在 jsdom 中展开为 flexGrow=1；min-width:0 防
    // 子内容把布局撑破 —— 紧凑尺寸下布局不破的关键约束。
    expect(content.style.flexGrow).toBe("1");
    // jsdom 对 0 长度可能省略单位 — 两种规格化都接受。
    expect(["0", "0px"]).toContain(content.style.minWidth);

    const shell = screen.getByTestId("workbench-shell");
    expect(shell.style.overflow).toBe("hidden");
    expect(shell.style.display).toBe("flex");
    expect(shell.style.flexDirection).toBe("row");
  });

  it("banner 插槽：传入 banner 时渲染在内容区顶部", () => {
    render(
      <WorkbenchShell
        view="chat"
        onViewChange={() => undefined}
        banner={<div data-testid="test-banner">出错了</div>}
        chatProps={{ activeSid: "default", secret: "" }}
        skillsProps={{ channel: null }}
        settingsProps={{
          getChannel: () => null,
          lastMessage: null,
          secret: "",
          autostart: { ready: false, enabled: false, toggle: () => undefined },
        }}
      />,
    );
    const slot = screen.getByTestId("workbench-banner");
    expect(slot).toBeTruthy();
    expect(screen.getByTestId("test-banner").textContent).toBe("出错了");
    // 插槽是内容区第一个子元素（视图之上）。
    const content = screen.getByTestId("workbench-content");
    expect(content.firstElementChild).toBe(slot);
  });
});

describe("Sidebar T13 — 「更多」入口与连接徽章聚合（WB-3）", () => {
  function renderWithMore(overrides?: {
    connectionState?: "disconnected" | "connecting" | "connected";
    routeKind?: "cloud" | "local" | null;
  }) {
    const actions = {
      onMemory: vi.fn(),
      onTrace: vi.fn(),
      onFeedback: vi.fn(),
    };
    render(
      <WorkbenchShell
        view="chat"
        onViewChange={() => undefined}
        connectionState={overrides?.connectionState ?? "connected"}
        routeKind={overrides?.routeKind ?? null}
        moreActions={actions}
        chatProps={{ activeSid: "default", secret: "" }}
        skillsProps={{ channel: null }}
        settingsProps={{
          getChannel: () => null,
          lastMessage: null,
          secret: "",
          autostart: { ready: false, enabled: false, toggle: () => undefined },
        }}
      />,
    );
    return actions;
  }

  it("「更多」折叠组展开后 记忆/Trace/反馈 入口逐一可达", () => {
    vi.mocked(controlWS.state).mockReturnValue("connected");
    const actions = renderWithMore();
    expect(screen.queryByTestId("sidebar-more-group")).toBeNull();

    fireEvent.click(screen.getByTestId("sidebar-more-toggle"));
    expect(screen.getByTestId("sidebar-more-group")).toBeTruthy();
    expect(screen.getByTestId("memory-toggle").textContent).toContain("记忆管理");
    expect(screen.getByTestId("trace-toggle").textContent).toContain("ContextTrace");
    expect(screen.getByTestId("feedback-toggle").textContent).toContain("反馈问题");
    fireEvent.click(screen.getByTestId("memory-toggle"));
    fireEvent.click(screen.getByTestId("trace-toggle"));
    fireEvent.click(screen.getByTestId("feedback-toggle"));
    expect(actions.onMemory).toHaveBeenCalledOnce();
    expect(actions.onTrace).toHaveBeenCalledOnce();
    expect(actions.onFeedback).toHaveBeenCalledOnce();
  });

  it("连接徽章：双通道都连上 + routeKind=local → 本地", () => {
    vi.mocked(controlWS.state).mockReturnValue("connected");
    renderWithMore({ routeKind: "local" });
    expect(screen.getByTestId("sidebar-conn-badge").textContent).toContain(
      "本地",
    );
  });

  it("连接徽章聚合取最差态：ControlChannel 已连但 controlWS 断开 → 未连接", () => {
    vi.mocked(controlWS.state).mockReturnValue("disconnected");
    renderWithMore({ connectionState: "connected", routeKind: "cloud" });
    expect(screen.getByTestId("sidebar-conn-badge").textContent).toContain(
      "未连接",
    );
  });
});

describe("S6 primary navigation cutover", () => {
  it("keeps one primary entry and settings without exposing Session management", () => {
    render(<Harness />);
    expect(screen.getByRole("button", { name: "主对话" })).toBeTruthy();
    expect(screen.queryByTestId("sidebar-session-area")).toBeNull();
    expect(screen.queryByText("新建普通会话")).toBeNull();
    expect(screen.queryByText("添加项目")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "模型与设置" }));
    expect(screen.getByTestId("view-settings")).toBeTruthy();
  });
});
