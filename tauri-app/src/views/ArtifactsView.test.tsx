// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * ArtifactsView 测试（T12，WB-7）。
 *
 * 覆盖：列表渲染（数据源 list_artifacts + 顺序照后端倒序返回）、空态、
 * 动作 invoke shape（artifact_open / artifact_show_in_folder）、错误态
 * 与刷新。mock @tauri-apps/api —— 与 code-panel/ArtifactCard.test.ts
 * 同款 invokeMock 模式。
 */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ArtifactsView, type ArtifactListEntry } from "./ArtifactsView";

const invokeMock = vi.fn();

vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

// Rust list_artifacts 已按 modified_at 倒序返回 —— 前端按原序渲染。
const ENTRIES: ArtifactListEntry[] = [
  {
    name: "report.pdf",
    path: "/data/artifacts/report.pdf",
    size: 2048,
    modified_at: 1_754_300_000_000,
  },
  {
    name: "notes.md",
    path: "/data/artifacts/notes.md",
    size: 100,
    modified_at: 1_754_200_000_000,
  },
];

beforeEach(() => {
  invokeMock.mockReset();
});

afterEach(cleanup);

describe("ArtifactsView（T12，WB-7）", () => {
  it("挂载即拉 list_artifacts 并按返回顺序渲染列表", async () => {
    invokeMock.mockResolvedValue(ENTRIES);
    render(<ArtifactsView />);

    expect(invokeMock).toHaveBeenCalledWith("list_artifacts");
    await waitFor(() =>
      expect(screen.getAllByTestId("artifact-item")).toHaveLength(2),
    );
    // 顺序：后端倒序返回，前端不重排。
    const names = screen
      .getAllByTestId("artifact-name")
      .map((el) => el.textContent ?? "");
    expect(names[0]).toContain("report.pdf");
    expect(names[1]).toContain("notes.md");
    // meta：大小人类可读。
    expect(screen.getByText(/2\.0 KB/)).toBeTruthy();
    expect(screen.getByText(/100 B/)).toBeTruthy();
  });

  it("空目录渲染空态文案", async () => {
    invokeMock.mockResolvedValue([]);
    render(<ArtifactsView />);

    await waitFor(() =>
      expect(screen.getByTestId("artifacts-empty")).toBeTruthy(),
    );
    expect(screen.getByText("暂无产物")).toBeTruthy();
    expect(screen.queryByTestId("artifact-item")).toBeNull();
  });

  it("「打开」「在文件夹中显示」按既有 command shape 发 invoke", async () => {
    invokeMock.mockResolvedValue(ENTRIES);
    render(<ArtifactsView />);
    await waitFor(() =>
      expect(screen.getAllByTestId("artifact-item")).toHaveLength(2),
    );
    invokeMock.mockClear();
    invokeMock.mockResolvedValue(undefined);

    fireEvent.click(screen.getAllByTestId("artifact-action-open")[0]);
    expect(invokeMock).toHaveBeenCalledWith("artifact_open", {
      path: "/data/artifacts/report.pdf",
    });
    await waitFor(() =>
      expect(
        screen.getByText("已请求系统打开文件"),
      ).toBeTruthy(),
    );

    fireEvent.click(
      screen.getAllByTestId("artifact-action-show_in_folder")[1],
    );
    expect(invokeMock).toHaveBeenCalledWith("artifact_show_in_folder", {
      path: "/data/artifacts/notes.md",
    });
    await waitFor(() =>
      expect(screen.getByText("已在文件夹中定位")).toBeTruthy(),
    );
  });

  it("动作失败显示 alert 状态行，不破列表", async () => {
    invokeMock.mockResolvedValue(ENTRIES);
    render(<ArtifactsView />);
    await waitFor(() =>
      expect(screen.getAllByTestId("artifact-item")).toHaveLength(2),
    );
    invokeMock.mockRejectedValue("[open_failed] no handler");

    fireEvent.click(screen.getAllByTestId("artifact-action-open")[0]);
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByRole("alert").textContent).toContain(
      "[open_failed] no handler",
    );
    expect(screen.getAllByTestId("artifact-item")).toHaveLength(2);
  });

  it("list_artifacts 失败显示错误横幅，刷新可重试", async () => {
    invokeMock.mockRejectedValueOnce("scan failed");
    render(<ArtifactsView />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByRole("alert").textContent).toContain("scan failed");

    invokeMock.mockResolvedValue(ENTRIES);
    fireEvent.click(screen.getByTestId("artifacts-refresh"));
    await waitFor(() =>
      expect(screen.getAllByTestId("artifact-item")).toHaveLength(2),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
