import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ChatMissionCard } from "./ChatMissionCard";
import { MissionsChannelContext, missionIdFromToolResult } from "./chatMission";
import { shouldHideToolTrace } from "../chat/messageVisibility";

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

const DETAIL = {
  mission: { id: "m-1", status: "ACTIVE", goal: "写词频模块并发布 README.md" },
  tasks: [{ status: "COMPLETED", kind: "work" }, { status: "RUNNING", kind: "work" }],
  approvals: [{ request_id: "approval-1", state: "PENDING", kind: "action", summary: {
    connector: "file_publish", operation: "publish", target: "README.md",
    params: { artifact_path: "README.md" }, reason: "用户在确认页批准的操作", reason_source: "system" } }],
  actions: [{ action_key: "a-0", state: "SUCCEEDED", target: "wordfreq.py", published_path: "/pub/wordfreq.v1.py" }],
};

function mount() {
  const fake = fakeChannel();
  render(
    <MissionsChannelContext.Provider value={fake.channel as never}>
      <ChatMissionCard missionId="m-1" />
    </MissionsChannelContext.Provider>,
  );
  const get = fake.sent.find((m) => m.type === "mission_get")!;
  fake.push({ type: "mission_get_response", payload: { ok: true, request_id: get.request_id, data: DETAIL } });
  return fake;
}

describe("对话里的后台任务卡片", () => {
  afterEach(cleanup);
  it("读出进度、已发布文件，待批准的发布由人亲手点批准，走任务页同一条消息", () => {
    const fake = mount();
    expect(screen.getByText("后台任务：进行中")).toBeTruthy();
    expect(screen.getByText("步骤：已完成 1 / 2")).toBeTruthy();
    expect(screen.getByText("已发布：wordfreq.py → /pub/wordfreq.v1.py")).toBeTruthy();
    expect(screen.getByText("动作审批：发布 README.md")).toBeTruthy();
    expect(screen.getByText("（系统生成）")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "批准" }));
    const decide = fake.sent.find((m) => m.type === "mission_approval_decide")!;
    expect(decide.payload).toEqual({ approval_id: "approval-1", decision: "approve" });
  });

  it("拒绝必须写理由；任务有变化就重新读取", () => {
    const fake = mount();
    const reject = screen.getByRole("button", { name: "拒绝" }) as HTMLButtonElement;
    expect(reject.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("拒绝理由"), { target: { value: "文件名不对" } });
    fireEvent.click(reject);
    expect(fake.sent.find((m) => m.type === "mission_approval_decide")!.payload)
      .toEqual({ approval_id: "approval-1", decision: "reject", reason: "文件名不对" });
    const before = fake.sent.filter((m) => m.type === "mission_get").length;
    fake.push({ type: "mission_changed", payload: { mission_id: "m-1", status: "ACTIVE" } });
    fake.push({ type: "mission_changed", payload: { mission_id: "other", status: "ACTIVE" } });
    expect(fake.sent.filter((m) => m.type === "mission_get").length).toBe(before + 1);
  });

  it("从工具结果里认出任务号；任务卡片不会被「隐藏工具消息」藏掉", () => {
    expect(missionIdFromToolResult(JSON.stringify({ mission_id: "m-9", created: true }))).toBe("m-9");
    expect(missionIdFromToolResult(JSON.stringify({ value: { mission_id: "m-8" } }))).toBe("m-8");
    expect(missionIdFromToolResult("not json")).toBe("");
    expect(shouldHideToolTrace({ role: "tool_result", tool_name: "mission_start", tool_ok: true,
      tool_result: JSON.stringify({ mission_id: "m-9" }) } as never, true)).toBe(false);
  });
});
