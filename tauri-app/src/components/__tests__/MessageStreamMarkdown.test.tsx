// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 回归测试：桌宠消息大框里 assistant 回复必须按 markdown 渲染，
 * 而不是把 ```code``` / **bold** / 列表标记当裸文本显示。
 * （用户 2026-06-29 报：消息框 LLM 返回的 markdown 没被渲染。）
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MessageStreamPanel, type ChatStreamMessage } from "../MessageStreamPanel";

afterEach(cleanup);

beforeEach(() => {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});

function renderStream(messages: ChatStreamMessage[]) {
  return render(
    <MessageStreamPanel
      embedded
      filter="all"
      chatMessages={messages}
      warnings={[]}
      errors={[]}
      onSetFilter={() => {}}
      onDismiss={() => {}}
      onDismissAll={() => {}}
      onJumpToSession={() => {}}
      onChoice={() => {}}
    />,
  );
}

describe("MessageStreamPanel — assistant markdown", () => {
  it("renders fenced code blocks as <code>, not raw ``` text", () => {
    const md = "金价模型:\n\n```text\n金价 ≈ 反美元资产\n```";
    const { container } = renderStream([
      { role: "assistant", text: md, ts: 1 },
    ]);
    const assistantRow = container.querySelector('[data-role="assistant"]')!;
    // markdown 渲染后会有真实的 <code> 元素，且正文里不再出现裸的 ``` 围栏。
    expect(assistantRow.querySelector("code")).not.toBeNull();
    expect(assistantRow.textContent).not.toContain("```");
  });

  it("renders bold + lists as real elements", () => {
    const md = "**核心关系**:\n\n- 实际利率↓ → 金价↑\n- 美元指数↓ → 金价↑";
    const { container } = renderStream([
      { role: "assistant", text: md, ts: 2 },
    ]);
    const assistantRow = container.querySelector('[data-role="assistant"]')!;
    expect(assistantRow.querySelector("strong")).not.toBeNull();
    expect(assistantRow.querySelectorAll("li").length).toBe(2);
    // 裸标记不应残留。
    expect(assistantRow.textContent).not.toContain("**");
  });

  it("renders GFM tables as real <table>, not raw pipes", () => {
    const md = [
      "| 事件 | 时间 | 金价表现 |",
      "|---|---|---|",
      "| 9·11 | 2001 | +20% |",
    ].join("\n");
    const { container } = renderStream([
      { role: "assistant", text: md, ts: 4 },
    ]);
    const assistantRow = container.querySelector('[data-role="assistant"]')!;
    const table = assistantRow.querySelector("table");
    expect(table).not.toBeNull();
    // 表头 + 数据单元格真的生成了。
    expect(assistantRow.querySelectorAll("th").length).toBe(3);
    expect(assistantRow.querySelectorAll("td").length).toBe(3);
    // 分隔行 |---| 不应作为可见文本残留。
    expect(assistantRow.textContent).not.toContain("---");
  });

  it("keeps user input as plain text (no markdown rendering)", () => {
    const { container } = renderStream([
      { role: "user", text: "**这是我打的字**", ts: 3 },
    ]);
    const userRow = container.querySelector('[data-role="user"]')!;
    // user 气泡保持纯文本：星号原样保留，不生成 <strong>。
    expect(userRow.querySelector("strong")).toBeNull();
    expect(userRow.textContent).toContain("**这是我打的字**");
  });

  it("shows whether a running task has read a continuation", () => {
    const { rerender } = renderStream([
      {
        role: "user",
        text: "不要重建，打开已有项目",
        ts: 3,
        continuationStatus: "waiting",
      },
    ]);
    expect(screen.getByRole("status").textContent).toContain(
      "等待 Agent 读取",
    );

    rerender(
      <MessageStreamPanel
        embedded
        filter="all"
        chatMessages={[
          {
            role: "user",
            text: "不要重建，打开已有项目",
            ts: 3,
            continuationStatus: "bound",
          },
        ]}
        warnings={[]}
        errors={[]}
        onSetFilter={() => {}}
        onDismiss={() => {}}
        onDismissAll={() => {}}
        onJumpToSession={() => {}}
        onChoice={() => {}}
      />,
    );
    expect(screen.getByRole("status").textContent).toContain(
      "Agent 已读取",
    );
  });

  it("copies the corresponding message text from each copy button", async () => {
    renderStream([
      { role: "user", text: "用户消息", ts: 1 },
      { role: "assistant", text: "**助手消息**", ts: 2 },
    ]);

    const buttons = screen.getAllByTestId("message-copy-button");
    expect(buttons[0].textContent).toBe("");
    expect(buttons[1].textContent).toBe("");
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);

    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenNthCalledWith(1, "用户消息");
      expect(navigator.clipboard.writeText).toHaveBeenNthCalledWith(2, "**助手消息**");
    });
  });

  it("does not render copy buttons for tool trace rows", () => {
    renderStream([
      { role: "tool", text: "tool_call: web_fetch", ts: 1 },
    ]);

    expect(screen.queryByTestId("message-copy-button")).toBeNull();
  });
});
