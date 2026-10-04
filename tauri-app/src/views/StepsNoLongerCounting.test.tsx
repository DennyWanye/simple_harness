import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { StepsNoLongerCounting } from "./MissionsView";

describe("任务详情里不再算数的步骤", () => {
  afterEach(cleanup);
  it("列出按旧版要求通过的步骤和它的版本；没有就不显示", () => {
    render(<StepsNoLongerCounting rows={[
      { occurrence_id: "o1", task_id: "t1", acceptance_id: "a1", requirements_revision: 1, label: "写 a.md" },
    ]} />);
    expect(screen.getByTestId("mission-steps-no-longer-counting").textContent).toBe(
      "不再算数：写 a.md（按第 1 版要求通过）——要求改过，规划器会决定重做还是沿用");
    cleanup();
    const { container } = render(<StepsNoLongerCounting rows={[]} />);
    expect(container.innerHTML).toBe("");
  });
});
