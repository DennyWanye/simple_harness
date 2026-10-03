import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { BudgetByDuty } from "./MissionsView";

describe("任务详情里的预算去向", () => {
  afterEach(cleanup);
  it("默认收起；只列花过或试过的；逐行写清已用、尝试与失败次数，用量未知另说明", () => {
    render(<BudgetByDuty rows={[
      { obligation_id: "d0", depth: 0, label: "整个任务", attempts: 3, failed_attempts: 1, settled_tokens: 1200, unknown_usage_attempts: 1 },
      { obligation_id: "d1", depth: 1, label: "写出 notes/a.md", attempts: 2, failed_attempts: 1, settled_tokens: 800, unknown_usage_attempts: 0 },
      { obligation_id: "d2", depth: 1, label: "还没动的部分", attempts: 0, failed_attempts: 0, settled_tokens: 0, unknown_usage_attempts: 0 },
    ]} />);
    const box = screen.getByTestId("mission-budget-by-duty") as HTMLDetailsElement;
    expect(box.open).toBe(false);
    expect(screen.getByText("整个任务 · 已用 1200 token · 尝试 3 次（失败 1 次），另有 1 次用量未知")).toBeTruthy();
    expect(screen.getByText("写出 notes/a.md · 已用 800 token · 尝试 2 次（失败 1 次）")).toBeTruthy();
    expect(screen.queryByText(/还没动的部分/)).toBeNull();
  });

  it("没有花费就不显示；超过 8 行合成其余几项", () => {
    const { container } = render(<BudgetByDuty rows={[{ obligation_id: "d0", attempts: 0, settled_tokens: 0 }]} />);
    expect(container.innerHTML).toBe("");
    cleanup();
    render(<BudgetByDuty rows={Array.from({ length: 10 }, (_, n) => ({
      obligation_id: `d${n}`, depth: 0, label: `第 ${n} 项`, attempts: 1, failed_attempts: 0, settled_tokens: 1 }))} />);
    expect(screen.getByText("其余 2 项")).toBeTruthy();
  });
});
