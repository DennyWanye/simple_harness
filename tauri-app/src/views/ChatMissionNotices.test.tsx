import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ChatMissionNotices } from "./ChatMissionNotices";
import { MissionsChannelContext } from "./chatMission";

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
    push: (message: unknown) => act(() => { handlers.forEach((h) => h(message)); }),
  };
}

const NOTICES = [
  { notice_id: "ev-1", mission_id: "m-1", status: "COMPLETED", status_zh: "已完成", goal: "写 NOTES.md", stop_reason: null },
  { notice_id: "ev-2", mission_id: "m-2", status: "FAILED", status_zh: "未完成", goal: "发布 README.md", stop_reason: "planning_failed" },
];

describe("主对话里的后台任务结束通知", () => {
  afterEach(cleanup);
  it("列出没收到的通知；人点“已收到”才发确认，成功后卡片消失；任务有变化就重读", () => {
    const fake = fakeChannel();
    render(
      <MissionsChannelContext.Provider value={fake.channel as never}>
        <ChatMissionNotices />
      </MissionsChannelContext.Provider>,
    );
    const first = fake.sent.find((m) => m.type === "mission_notices")!;
    fake.push({ type: "mission_notices_response", payload: { ok: true, request_id: first.request_id, data: NOTICES } });
    expect(screen.getByText("后台任务已完成：写 NOTES.md")).toBeTruthy();
    expect(screen.getByText("停止原因：planning_failed")).toBeTruthy();
    const goal = screen.getByText("后台任务已完成：写 NOTES.md");
    expect(goal.style.whiteSpace).toBe("nowrap");
    expect(goal.getAttribute("title")).toBe("写 NOTES.md");
    expect(fake.sent.some((m) => m.type === "mission_notice_ack")).toBe(false);

    fireEvent.click(screen.getAllByRole("button", { name: "已收到" })[0]);
    const ack = fake.sent.find((m) => m.type === "mission_notice_ack")!;
    expect(ack.payload).toEqual({ notice_id: "ev-1" });
    fake.push({ type: "mission_notice_ack_response", payload: { ok: true, request_id: ack.request_id, data: {} } });
    expect(screen.queryByText("后台任务已完成：写 NOTES.md")).toBeNull();
    expect(screen.getByText("后台任务未完成：发布 README.md")).toBeTruthy();

    fake.push({ type: "mission_changed", payload: { mission_id: "m-3" } });
    expect(fake.sent.filter((m) => m.type === "mission_notices").length).toBe(2);
  });

  it("停下时有结果不明的对外操作就写明；之后核对出结果单独一张卡说已生效还是未生效", () => {
    const fake = fakeChannel();
    render(
      <MissionsChannelContext.Provider value={fake.channel as never}>
        <ChatMissionNotices />
      </MissionsChannelContext.Provider>,
    );
    const first = fake.sent.find((m) => m.type === "mission_notices")!;
    fake.push({ type: "mission_notices_response", payload: { ok: true, request_id: first.request_id, data: [
      { notice_id: "ev-3", mission_id: "m-3", status: "CANCELLED", status_zh: "已取消", goal: "写周报并发布", unresolved_actions: 1 },
      { notice_id: "ev-4", mission_id: "m-3", status: "CANCELLED", status_zh: "已取消", goal: "写周报并发布", unresolved_actions: 1,
        action_settled: { operation: "publish", target: "reports/weekly.md", applied: false } },
    ] } });
    expect(screen.getByText("后台任务已取消：写周报并发布")).toBeTruthy();
    expect(screen.getByText("还有 1 个对外操作结果不明，系统会继续核对")).toBeTruthy();
    expect(screen.getByText("后台任务的对外操作有结果了：写周报并发布")).toBeTruthy();
    expect(screen.getByText("publish reports/weekly.md 核对结果：未生效")).toBeTruthy();
    expect(screen.getAllByText(/结果不明/).length).toBe(1);
  });

  it("确认失败时卡片留着并提示", () => {
    const fake = fakeChannel();
    render(
      <MissionsChannelContext.Provider value={fake.channel as never}>
        <ChatMissionNotices />
      </MissionsChannelContext.Provider>,
    );
    const first = fake.sent.find((m) => m.type === "mission_notices")!;
    fake.push({ type: "mission_notices_response", payload: { ok: true, request_id: first.request_id, data: NOTICES.slice(0, 1) } });
    fireEvent.click(screen.getByRole("button", { name: "已收到" }));
    const ack = fake.sent.find((m) => m.type === "mission_notice_ack")!;
    fake.push({ type: "mission_notice_ack_response", payload: { ok: false, request_id: ack.request_id, error: "没有这条通知" } });
    expect(screen.getByText("后台任务已完成：写 NOTES.md")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toBe("没有这条通知");
  });

  it("通知多时只摆最新三条，人点“全部已收到”一次清空", () => {
    const fake = fakeChannel();
    render(
      <MissionsChannelContext.Provider value={fake.channel as never}>
        <ChatMissionNotices />
      </MissionsChannelContext.Provider>,
    );
    const many = Array.from({ length: 5 }, (_, i) => ({ ...NOTICES[0], notice_id: `ev-${i}`, goal: `任务 ${i}` }));
    const first = fake.sent.find((m) => m.type === "mission_notices")!;
    fake.push({ type: "mission_notices_response", payload: { ok: true, request_id: first.request_id, data: many } });
    expect(screen.queryByText("后台任务已完成：任务 0")).toBeNull();
    expect(screen.getByText("后台任务已完成：任务 4")).toBeTruthy();
    expect(screen.getByText("还有 2 条较早的通知")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "全部已收到" }));
    const ack = fake.sent.find((m) => m.type === "mission_notice_ack")!;
    expect(ack.payload).toEqual({ all: true });
    fake.push({ type: "mission_notice_ack_response", payload: { ok: true, request_id: ack.request_id, data: { acked: 5 } } });
    expect(screen.queryByText("后台任务已完成：任务 4")).toBeNull();
  });
});
