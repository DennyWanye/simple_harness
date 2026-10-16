// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 同一张工具结果卡在产物卡与普通结果之间切换时，hooks 数量不能变（React 会直接抛错）。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToolResultCard } from "../MessageBubble";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

afterEach(() => cleanup());

const plain = JSON.stringify({ ok: true, path: "/tmp/notes.txt" });
const withArtifact = JSON.stringify({
  ok: true,
  artifacts: [{ kind: "text", title: "笔记", preview: "内容" }],
});

describe("ToolResultCard", () => {
  it("普通结果变成带产物的结果时改渲染产物卡，不抛错", () => {
    const { rerender } = render(<ToolResultCard name="write_file" ok result={plain} />);
    expect(screen.getByText(/点击展开/)).toBeTruthy();

    rerender(<ToolResultCard name="write_file" ok result={withArtifact} />);
    expect(screen.getByTestId("artifact-card-list")).toBeTruthy();
  });

  it("带产物的结果变成普通结果时回到可展开的结果卡，不抛错", () => {
    const { rerender } = render(<ToolResultCard name="write_file" ok result={withArtifact} />);
    expect(screen.getByTestId("artifact-card-list")).toBeTruthy();

    rerender(<ToolResultCard name="write_file" ok={false} result={plain} />);
    expect(screen.queryByTestId("artifact-card-list")).toBeNull();
    fireEvent.click(screen.getByText(/点击展开/));
    expect(screen.queryByText(/点击展开/)).toBeNull();
  });
});
