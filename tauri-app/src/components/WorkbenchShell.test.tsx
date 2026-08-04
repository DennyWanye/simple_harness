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
import { afterEach, describe, expect, it } from "vitest";

// vitest 未开 globals — testing-library 的自动 cleanup 不生效，手动挂。
afterEach(cleanup);

// jsdom 把 hex 色值规格化为 rgb() — 断言前统一转换（色值仍单源自
// theme/components.ts 的 dark 套件，测试内零硬编码色值）。
function hexToRgb(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
}
const ACCENT_RGB = hexToRgb(dark.accent);

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
        relayAdapter: null,
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

  it("当前项高亮使用 dark.accent 且带 aria-current", () => {
    render(<Harness />);
    const chatBtn = screen.getByTestId("nav-chat");
    const skillsBtn = screen.getByTestId("nav-skills");

    expect(chatBtn.getAttribute("aria-current")).toBe("page");
    expect(chatBtn.style.color).toBe(ACCENT_RGB);
    expect(skillsBtn.getAttribute("aria-current")).toBeNull();
    expect(skillsBtn.style.color).not.toBe(ACCENT_RGB);

    fireEvent.click(skillsBtn);
    expect(skillsBtn.getAttribute("aria-current")).toBe("page");
    expect(skillsBtn.style.color).toBe(ACCENT_RGB);
    expect(chatBtn.getAttribute("aria-current")).toBeNull();
  });
});

describe("WorkbenchShell 紧凑尺寸样式（WB-3 min 800×560）", () => {
  it("侧栏固定 240px，内容区 flex:1 + min-width:0，壳不出横向滚动", () => {
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
          relayAdapter: null,
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
