import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PlanChangePanel } from "./PlanChangePanel";

type Sent = { type: string; request_id: string; payload: Record<string, unknown> };

function fakeChannel() {
  const sent: Sent[] = [];
  const handlers = new Set<(m: unknown) => void>();
  return {
    sent,
    channel: {
      send: (message: unknown) => { sent.push(message as Sent); return true; },
      onMessage: (handler: (m: unknown) => void) => { handlers.add(handler); return () => handlers.delete(handler); },
    },
    reply: (type: string, data: unknown, ok = true, error = "") => act(() => {
      const request = [...sent].reverse().find((m) => m.type === type)!;
      handlers.forEach((h) => h({ type: type + "_response", payload: { request_id: request.request_id, ok, data, error } }));
    }),
    push: (message: unknown) => act(() => { handlers.forEach((h) => h(message)); }),
  };
}

const STUCK = {
  jobs: [{ job_id: "job-1", state: "READY", row_version: 4 }, { job_id: "job-0", state: "ABANDONED", row_version: 8 }],
  blocked_notifications: [{ message_id: "msg-1", row_version: 6, kind: "CONVERGE", subject_key: "job-1",
    error_code: "FOLLOWUP_HANDLER_FAILED", attempts: 5 }],
};

function mount() {
  const fake = fakeChannel();
  render(<PlanChangePanel missionId="m-1" channel={fake.channel as never} />);
  return fake;
}

describe("改计划进度面板", () => {
  afterEach(cleanup);

  it("没卡住时什么都不显示", () => {
    const fake = mount();
    expect(fake.sent[0]).toMatchObject({ type: "taskgraph.convergence", payload: { mission_id: "m-1" } });
    fake.reply("taskgraph.convergence", { jobs: [{ job_id: "job-0", state: "APPLIED", row_version: 3 }], blocked_notifications: [] });
    expect(screen.queryByTestId("plan-change-panel")).toBeNull();
  });

  it("卡住时说清原因；放弃要写理由，点了才发，带期望版本", () => {
    const fake = mount();
    fake.reply("taskgraph.convergence", STUCK);
    expect(screen.getByText(/旧尝试已停、计划结构没变/)).toBeTruthy();
    const abandon = screen.getByRole("button", { name: "放弃这次改计划" }) as HTMLButtonElement;
    expect(abandon.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("放弃理由"), { target: { value: "先按原计划做完" } });
    fireEvent.click(abandon);
    const sent = fake.sent.find((m) => m.type === "taskgraph.abandon_convergence")!;
    expect(sent.payload).toEqual({ mission_id: "m-1", job_id: "job-1", expected_version: 4, reason: "先按原计划做完" });
    fake.reply("taskgraph.abandon_convergence", {});
    expect(fake.sent.filter((m) => m.type === "taskgraph.convergence")).toHaveLength(2);
  });

  it("被挡的通知可以重新发送；被拒时如实显示原因", () => {
    const fake = mount();
    fake.reply("taskgraph.convergence", STUCK);
    expect(screen.getByText(/推进通知连续失败 5 次/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重新发送" }));
    const sent = fake.sent.find((m) => m.type === "taskgraph.retry_notification")!;
    expect(sent.payload).toMatchObject({ mission_id: "m-1", message_id: "msg-1", expected_version: 6 });
    fake.reply("taskgraph.retry_notification", null, false, "这条通知的状态已经变了，请刷新后再试");
    expect(screen.getByRole("alert").textContent).toContain("请刷新后再试");
  });

  it("读失败时保留上次画面并标已过期；本任务有变化就重读", () => {
    const fake = mount();
    fake.reply("taskgraph.convergence", STUCK);
    fake.push({ type: "mission_changed", payload: { mission_id: "other" } });
    expect(fake.sent.filter((m) => m.type === "taskgraph.convergence")).toHaveLength(1);
    fake.push({ type: "mission_changed", payload: { mission_id: "m-1" } });
    fake.reply("taskgraph.convergence", null, false, "执行图来源不可读");
    expect(screen.getByText(/已过期/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新发送" })).toBeTruthy();
  });

  it("收到不合合同的回复（协议错）时保留上次画面，用一句大白话标已过期", () => {
    const fake = mount();
    fake.reply("taskgraph.convergence", STUCK);
    fake.push({ type: "mission_changed", payload: { mission_id: "m-1" } });
    const request = [...fake.sent].reverse().find((m) => m.type === "taskgraph.convergence")!;
    fake.push({ type: "taskgraph.convergence_response", payload: { request_id: request.request_id, ok: false,
      error_code: "protocol_error", error: "收到的数据格式不对，没有显示，请稍后重新读取。" } });
    expect(screen.getByText(/已过期：收到的数据格式不对，显示的是上次的情况/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新发送" })).toBeTruthy();
  });
});
