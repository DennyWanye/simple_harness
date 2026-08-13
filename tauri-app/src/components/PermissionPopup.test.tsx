import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PermissionPopup } from "./PermissionPopup";

afterEach(cleanup);

describe("PermissionPopup", () => {
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
