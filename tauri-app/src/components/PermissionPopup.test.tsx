import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PermissionPopup } from "./PermissionPopup";

afterEach(cleanup);

describe("PermissionPopup", () => {
  it("describes workflow spawning as a child task instead of a shell command", () => {
    render(
      <PermissionPopup
        request={{
          request_id: "permission-child",
          run_id: "run-child",
          category: "shell",
          summary: "允许 DeskPet 执行 workflow_spawn",
          params: { tool_name: "workflow_spawn", arguments: { objective: "test" } },
          default_action: "prompt",
          dangerous: false,
          session_id: "session-child",
        }}
        onResolve={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "启动子任务" })).toBeTruthy();
    expect(screen.getByText(/受约束的子任务/)).toBeTruthy();
    expect(screen.queryByText(/运行 shell 命令/)).toBeNull();
  });

  it("shows exact tool arguments and stops the fenced Run without a deny race", () => {
    const order: string[] = [];
    const onStopRun = vi.fn(() => order.push("stop"));
    const onResolve = vi.fn((decision: string) =>
      order.push(`resolve:${decision}`),
    );

    render(
      <PermissionPopup
        request={{
          request_id: "permission-1",
          run_id: "run-1",
          category: "shell",
          summary: "允许 DeskPet 执行 run_shell",
          params: {
            tool_name: "run_shell",
            arguments: {
              command: "python -m pytest -q",
              cwd: "F:/projects/demo",
            },
          },
          default_action: "prompt",
          dangerous: true,
          session_id: "session-1",
        }}
        onResolve={onResolve}
        onStopRun={onStopRun}
      />,
    );

    fireEvent.click(screen.getByText("查看详细参数"));
    expect(screen.getByText(/python -m pytest -q/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "停止当前任务" }));

    expect(order).toEqual(["stop"]);
    expect(onResolve).not.toHaveBeenCalled();
  });
});
