// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RuntimeBackendBanner } from "./RuntimeBackendBanner";

afterEach(cleanup);

describe("RuntimeBackendBanner", () => {
  it("keeps recovery actions available in an in-workbench alert", () => {
    const onRetry = vi.fn();
    const onOpenLogDir = vi.fn();
    const onDismiss = vi.fn();
    render(
      <RuntimeBackendBanner
        message="supervisor stopped"
        onRetry={onRetry}
        onOpenLogDir={onOpenLogDir}
        onDismiss={onDismiss}
      />,
    );

    expect(screen.getByTestId("runtime-backend-banner").textContent).toContain(
      "supervisor stopped",
    );
    fireEvent.click(screen.getByRole("button", { name: "重试后端" }));
    fireEvent.click(screen.getByRole("button", { name: "打开日志" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭后端错误提示" }));
    expect(onRetry).toHaveBeenCalledOnce();
    expect(onOpenLogDir).toHaveBeenCalledOnce();
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});
