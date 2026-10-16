// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 数据目录区块：首次读取预填当前目录；用户改过路径后点刷新，不覆盖用户输入。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...args: unknown[]) => invoke(...args) }));

const { DataDirSection } = await import("./SettingsPanel");

afterEach(() => {
  cleanup();
  invoke.mockReset();
});

const setting = {
  effective: "/data/current",
  default: "/data/default",
  env_override: null,
  preference: null,
  externally_pinned: false,
  effective_exists: true,
  effective_size_bytes: 42,
};

describe("DataDirSection", () => {
  it("首次读取预填当前目录；改过路径后点刷新保留用户输入", async () => {
    invoke.mockResolvedValue(setting);
    render(<DataDirSection />);
    const input = (await screen.findByTestId("data-dir-input")) as HTMLInputElement;
    expect(input.value).toBe("/data/current");

    fireEvent.change(input, { target: { value: "/data/next" } });
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect((screen.getByTestId("data-dir-input") as HTMLInputElement).value).toBe("/data/next"),
    );
  });
});
