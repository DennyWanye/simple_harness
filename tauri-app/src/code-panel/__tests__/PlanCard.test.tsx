// SPDX-License-Identifier: BUSL-1.1

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PlanCard } from "../MessageBubble";

vi.mock("../controlWs", () => ({
  controlWS: {
    send: vi.fn(),
  },
}));

afterEach(() => cleanup());

describe("PlanCard", () => {
  it("shows manual authorization scope before execution", () => {
    render(
      <PlanCard
        rationale="Create and launch the project"
        steps={[{ title: "Create", detail: "Write project files" }]}
        targetDirectory="F:/workspace/project"
        actionCategories={["filesystem_write", "application"]}
        awaiting
        planSid="session-1"
      />,
    );

    expect(
      screen.getByTestId("plan-authorization-state").textContent,
    ).toContain("Manual · 等待一次授权");
    expect(screen.getByTestId("plan-target-directory").textContent).toContain(
      "F:/workspace/project",
    );
    expect(screen.getByTestId("plan-action-categories").textContent).toContain(
      "写入文件、启动/控制应用",
    );
    expect(screen.getByTestId("plan-confirm-go")).toBeTruthy();
  });

  it("shows Auto state without an approval bar", () => {
    render(
      <PlanCard
        rationale="Run automatically"
        steps={[{ title: "Inspect", detail: "Read current state" }]}
        targetDirectory="F:/workspace/project"
        actionCategories={["filesystem_read"]}
        autoConfirmed
      />,
    );

    expect(
      screen.getByTestId("plan-authorization-state").textContent,
    ).toContain("Auto · 自动执行");
    expect(screen.queryByTestId("plan-confirm-bar")).toBeNull();
  });
});
