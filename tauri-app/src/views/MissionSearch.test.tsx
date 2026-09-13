// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MissionSearch } from "./MissionSearch";

afterEach(cleanup);
it("keeps an in-progress comparison distinct from delivery and scoped validation", () => {
  render(<MissionSearch value={{
    rounds: [{ round_id: "round", task_id: "original", state: "SYNTHESIZING", synthesis_attempt_id: "C" }],
    candidates: [
      { result_id: "A-result", attempt_id: "A", round_id: "round", state: "READY" },
      { result_id: "C-result", attempt_id: "C", round_id: "round", state: "VERIFYING" },
    ],
    fragments: [{ fragment_id: "f", origin_task_id: "failed-origin", validation_task_id: "validation", validation_status: "COMPLETED", criteria_count: 1 }],
    decisions: [{ receipt_id: "decision", round_id: "round", reason: "使用互补材料", input_results: ["A-result"],
      considered: [{ result_id: "bad-result", eligible: false, reason: "来源已撤销" }] }],
    graph_changes: [{ change_id: "patch", from_version: 1, to_version: 2, rationale: { text: "补充缺失前置" },
      affected_task_ids: ["original"], superseded: { old_route: "replacement" }, cancelled: [] }],
  }} />);
  expect(screen.getByText(/候选等待比较；综合结果需要重新验证后才能交付/)).toBeTruthy();
  expect(screen.getByText(/尝试 A · 已验证的候选输入 · 已用于综合/)).toBeTruthy();
  expect(screen.getByText(/尝试 C · 综合候选进行中 · 仍需重新验证后才能交付/)).toBeTruthy();
  expect(screen.getByText(/综合并重新验证/)).toBeTruthy();
  expect(screen.queryByText(/已正式接受/)).toBeNull();
  expect(screen.getByText(/仅覆盖所选条件/)).toBeTruthy();
  expect(screen.getByText(/failed-origin/)).toBeTruthy();
  expect(screen.getByText(/未采用 bad-result：来源已撤销/)).toBeTruthy();
  expect(screen.getByText(/补充缺失前置/)).toBeTruthy();
  expect(screen.getByText(/停止路线：old_route/)).toBeTruthy();
});

it("shows committed comparison inputs, unselected candidates, and synthesis result without calling READY delivered", () => {
  render(<MissionSearch value={{
    rounds: [{ round_id: "round", task_id: "original", state: "COMMITTED", synthesis_attempt_id: "attempt-3" }],
    candidates: [
      { result_id: "result-1", attempt_id: "attempt-1", round_id: "round", state: "READY" },
      { result_id: "result-2", attempt_id: "attempt-2", round_id: "round", state: "READY" },
      { result_id: "result-3", attempt_id: "attempt-3", round_id: "round", state: "READY" },
      { result_id: "result-4", attempt_id: "attempt-4", round_id: "round", state: "READY" },
    ],
    decisions: [{ receipt_id: "decision", round_id: "round", input_results: ["result-1", "result-2"] }],
  }} />);
  expect(screen.getByText(/比较与综合轮已提交；候选输入不作为独立交付/)).toBeTruthy();
  expect(screen.getByText(/尝试 attempt-1 · 已验证的候选输入 · 已用于综合/)).toBeTruthy();
  expect(screen.getByText(/尝试 attempt-2 · 已验证的候选输入 · 已用于综合/)).toBeTruthy();
  expect(screen.getByText(/尝试 attempt-3 · 最终综合结果 · 综合轮已提交/)).toBeTruthy();
  expect(screen.getByText(/尝试 attempt-4 · 已验证但未采用的候选/)).toBeTruthy();
  expect(screen.getByText(/综合尝试：attempt-3 · 最终综合结果已提交/)).toBeTruthy();
  expect(screen.queryByText(/READY/)).toBeNull();
  expect(screen.queryByText(/已交付/)).toBeNull();
});

it("shows stopped and failed rounds as terminal states instead of in-progress candidates", () => {
  render(<MissionSearch value={{
    rounds: [
      { round_id: "stopped", task_id: "stopped-task", state: "STOPPED", synthesis_attempt_id: "stopped-synthesis" },
      { round_id: "failed", task_id: "failed-task", state: "FAILED", synthesis_attempt_id: "failed-synthesis" },
    ],
    candidates: [
      { result_id: "unused", attempt_id: "unused", round_id: "stopped", state: "READY" },
      { result_id: "invalid", attempt_id: "invalid", round_id: "stopped", state: "INVALIDATED" },
      { result_id: "cancelled", attempt_id: "cancelled", round_id: "stopped", state: "CANCELLED" },
      { result_id: "exhausted", attempt_id: "exhausted", round_id: "stopped", state: "EXHAUSTED" },
      { result_id: "failed-candidate", attempt_id: "failed-candidate", round_id: "stopped", state: "FAILED" },
      { result_id: "stopped-result", attempt_id: "stopped-synthesis", round_id: "stopped", state: "FAILED" },
      { result_id: "failed-result", attempt_id: "failed-synthesis", round_id: "failed", state: "FAILED" },
    ],
  }} />);
  expect(screen.getByText(/比较轮已停止；未形成已提交的综合结果/)).toBeTruthy();
  expect(screen.getByText(/比较轮未通过；未形成已提交的综合结果/)).toBeTruthy();
  expect(screen.getByText(/尝试 unused · 已验证但未采用的候选/)).toBeTruthy();
  expect(screen.queryByText(/等待比较/)).toBeNull();
  expect(screen.getByText(/尝试 invalid · 已失效候选/)).toBeTruthy();
  expect(screen.getByText(/尝试 cancelled · 已取消候选/)).toBeTruthy();
  expect(screen.getByText(/尝试 exhausted · 已停止候选/)).toBeTruthy();
  expect(screen.getByText(/尝试 failed-candidate · 未通过候选/)).toBeTruthy();
  expect(screen.getByText(/尝试 stopped-synthesis · 综合结果未提交 · 已停止/)).toBeTruthy();
  expect(screen.getByText(/尝试 failed-synthesis · 综合结果未提交 · 未通过/)).toBeTruthy();
  expect(screen.queryByText(/综合候选进行中/)).toBeNull();
});
