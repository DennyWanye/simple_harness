// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EvaluationPanel } from "./EvaluationPanel";
import type { EvaluationView } from "./types";

afterEach(() => cleanup());

const evaluation = (id: string, version: string, score: number): EvaluationView => ({
  evaluation_id: id,
  evaluator_name: "human",
  evaluator_version: "1",
  verdict: "pass",
  score,
  experiment_version: version,
});

describe("EvaluationPanel", () => {
  it("版本比较默认选前两个版本，评分带上版本 A；手动改选后按新选择", () => {
    const onSubmit = vi.fn();
    render(
      <EvaluationPanel
        evaluations={[evaluation("e1", "v1", 0.9), evaluation("e2", "v2", 0.5)]}
        onSubmit={onSubmit}
      />,
    );
    expect((screen.getByLabelText("版本 A") as HTMLSelectElement).value).toBe("v1");
    expect((screen.getByLabelText("版本 B") as HTMLSelectElement).value).toBe("v2");
    expect(screen.getByText(/0\.90 \/ 0\.50/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "提交评分" }));
    expect(onSubmit.mock.calls[0][0].experimentVersion).toBe("v1");

    fireEvent.change(screen.getByLabelText("版本 A"), { target: { value: "v2" } });
    expect(screen.getByText(/0\.50 \/ 0\.50/)).toBeTruthy();
  });

  it("没有实验版本时评分不带版本", () => {
    const onSubmit = vi.fn();
    render(<EvaluationPanel evaluations={[]} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole("button", { name: "提交评分" }));
    expect(onSubmit.mock.calls[0][0].experimentVersion).toBeUndefined();
  });
});
