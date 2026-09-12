// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MissionSearch } from "./MissionSearch";

afterEach(cleanup);
it("keeps waiting candidates separate from final acceptance and scoped validation", () => {
  render(<MissionSearch value={{
    rounds: [{ round_id: "round", task_id: "original", state: "SYNTHESIZING", synthesis_attempt_id: "C" }],
    candidates: [{ result_id: "A-result", attempt_id: "A", round_id: "round", state: "READY" }],
    fragments: [{ fragment_id: "f", origin_task_id: "failed-origin", validation_task_id: "validation", validation_status: "COMPLETED", criteria_count: 1 }],
    decisions: [{ receipt_id: "decision", round_id: "round", reason: "使用互补材料", input_results: ["A-result"],
      considered: [{ result_id: "bad-result", eligible: false, reason: "来源已撤销" }] }],
    graph_changes: [{ change_id: "patch", from_version: 1, to_version: 2, rationale: { text: "补充缺失前置" },
      affected_task_ids: ["original"], superseded: { old_route: "replacement" }, cancelled: [] }],
  }} />);
  expect(screen.getByText(/尝试 A · 待比较/)).toBeTruthy();
  expect(screen.getByText(/综合并重新验证/)).toBeTruthy();
  expect(screen.queryByText(/已正式接受/)).toBeNull();
  expect(screen.getByText(/仅覆盖所选条件/)).toBeTruthy();
  expect(screen.getByText(/failed-origin/)).toBeTruthy();
  expect(screen.getByText(/未采用 bad-result：来源已撤销/)).toBeTruthy();
  expect(screen.getByText(/补充缺失前置/)).toBeTruthy();
  expect(screen.getByText(/停止路线：old_route/)).toBeTruthy();
});
