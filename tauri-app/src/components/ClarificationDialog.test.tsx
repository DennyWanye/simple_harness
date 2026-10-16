// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ClarificationDialog } from "./ClarificationDialog";

afterEach(() => cleanup());

describe("ClarificationDialog", () => {
  it("换成下一条澄清请求时清空上一条没提交的回答", () => {
    const onResolve = vi.fn();
    const first = { request_id: "q1", question: "要用哪个目录？" };
    const { rerender } = render(<ClarificationDialog current={first} onResolve={onResolve} />);
    const input = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "src" } });
    expect(input.value).toBe("src");

    rerender(<ClarificationDialog current={first} onResolve={onResolve} />);
    expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("src");

    rerender(
      <ClarificationDialog
        current={{ request_id: "q2", question: "要不要覆盖？" }}
        onResolve={onResolve}
      />,
    );
    expect(screen.getByText("要不要覆盖？")).toBeTruthy();
    expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("");
  });
});
