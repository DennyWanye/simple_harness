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
});
