// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MissionsChannel } from "../stores/missionsStore";
import { PlanningQuestions } from "./PlanningQuestions";

afterEach(cleanup);

function channel() {
  const send = vi.fn(() => true);
  return { send, channel: { send, onMessage: vi.fn(() => () => {}) } as unknown as MissionsChannel };
}

const textQuestion = { decision_id: "d-1", question: "缺 data/sales.csv，请给出数据", state: "PENDING", version: 3, options: [] };
const choiceQuestion = { decision_id: "d-2", question: "选哪个？", state: "PENDING", version: 1,
  options: [{ key: "a", label: "方案 A" }, { key: "b", label: "方案 B" }] };

describe("PlanningQuestions", () => {
  it("文字回答默认同时作为资料附上，可以取消勾选", () => {
    const { send, channel: ch } = channel();
    render(<PlanningQuestions questions={[textQuestion]} channel={ch} onAnswered={() => {}} />);
    fireEvent.change(screen.getByLabelText("规划问题回答"), { target: { value: "2026-07,华东,120" } });
    expect((screen.getByLabelText("作为资料附上") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByText("提交回答"));
    expect(send).toHaveBeenCalledTimes(1);
    const sent = (send.mock.calls[0] as unknown[])[0] as { payload: Record<string, unknown> };
    expect(sent.payload).toMatchObject({ decision_id: "d-1", answer: "2026-07,华东,120", expected_version: 3, attach_as_source: true });
    cleanup();

    const again = channel();
    render(<PlanningQuestions questions={[textQuestion]} channel={again.channel} onAnswered={() => {}} />);
    fireEvent.change(screen.getByLabelText("规划问题回答"), { target: { value: "先不附" } });
    fireEvent.click(screen.getByLabelText("作为资料附上"));
    fireEvent.click(screen.getByText("提交回答"));
    const second = (again.send.mock.calls[0] as unknown[])[0] as { payload: Record<string, unknown> };
    expect(second.payload.attach_as_source).toBe(false);
  });

  it("选项式问题没有资料勾选框，也不附资料", () => {
    const { send, channel: ch } = channel();
    render(<PlanningQuestions questions={[choiceQuestion]} channel={ch} onAnswered={() => {}} />);
    expect(screen.queryByLabelText("作为资料附上")).toBeNull();
    fireEvent.change(screen.getByLabelText("选择规划问题回答"), { target: { value: "b" } });
    fireEvent.click(screen.getByText("提交回答"));
    const sent = (send.mock.calls[0] as unknown[])[0] as { payload: Record<string, unknown> };
    expect(sent.payload).toMatchObject({ answer: "b", attach_as_source: false });
  });
});
