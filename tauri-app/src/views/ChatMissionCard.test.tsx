import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ChatMissionCard } from "./ChatMissionCard";
import { MISSION_CARD_TOOLS, MissionsChannelContext, missionIdFromToolResult } from "./chatMission";
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
  // 根任务（desktop-root-…）是整体汇总，不是步骤，不计入（真机第十四局卡片显示 2 / 3）
  tasks: [{ id: "desktop-root-mission-1", status: "BLOCKED", kind: "work" },
    { status: "COMPLETED", kind: "work" }, { status: "RUNNING", kind: "work" }],
  approvals: [{ request_id: "approval-1", state: "PENDING", kind: "action", summary: {
    connector: "file_publish", operation: "publish", target: "README.md",
    params: { artifact_path: "README.md" }, reason: "用户在确认页批准的操作", reason_source: "system" } }],
  actions: [{ action_key: "a-0", state: "SUCCEEDED", target: "wordfreq.py", published_path: "/pub/wordfreq.v1.py" }],
};

function mount(detail: Record<string, unknown> = DETAIL) {
  const fake = fakeChannel();
  render(
    <MissionsChannelContext.Provider value={fake.channel as never}>
      <ChatMissionCard missionId="m-1" />
    </MissionsChannelContext.Provider>,
  );
  const get = fake.sent.find((m) => m.type === "mission_get")!;
  fake.push({ type: "mission_get_response", payload: { ok: true, request_id: get.request_id, data: detail } });
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

  it("改要求的工具结果也出任务卡片；要求改过之后卡片写明是第几版", () => {
    expect(MISSION_CARD_TOOLS.has("mission_amend")).toBe(true);
    expect(shouldHideToolTrace({ role: "tool_result", tool_name: "mission_amend", tool_ok: true,
      tool_result: JSON.stringify({ mission_id: "m-1", requirements_revision: 2 }) } as never, true)).toBe(false);
    mount();
    expect(screen.queryByTestId("chat-mission-requirements-revision")).toBeNull(); // 第 1 版不提
    cleanup();
    mount({ ...DETAIL, operation_workspace: { state: "APPROVED", requirements_ref: { id: "r", revision: 2, content_hash: "h" } } });
    expect(screen.getByTestId("chat-mission-requirements-revision").textContent).toBe("要求第 2 版");
  });

  it("换资料的工具结果也出任务卡片；资料变更在卡片上用一句话说明，由人批准", () => {
    expect(MISSION_CARD_TOOLS.has("mission_source_update")).toBe(true);
    expect(shouldHideToolTrace({ role: "tool_result", tool_name: "mission_source_update", tool_ok: true,
      tool_result: JSON.stringify({ mission_id: "m-1", state: "PENDING" }) } as never, true)).toBe(false);
    const fake = mount({ ...DETAIL, approvals: [{ request_id: "approval-s", state: "PENDING", kind: "source_change",
      source_change: { operation: "supersede", path: "sources/requirements.md" } }] });
    expect(screen.getByTestId("chat-approval-approval-s").textContent)
      .toContain("把资料换成新版本：sources/requirements.md。批准后，用到旧版的步骤会重新规划。");
    fireEvent.click(screen.getByRole("button", { name: "批准" }));
    expect(fake.sent.find((m) => m.type === "mission_approval_decide")!.payload)
      .toEqual({ approval_id: "approval-s", decision: "approve" });
  });

  it("规划器问用户的问题在卡片上就能回答，走任务页同一条消息；已回答的不再显示", () => {
    const question = { decision_id: "pd-1", state: "PENDING", version: 1, question: "资料换了版本，以哪一份为准？",
      options: [{ key: "follow-new-source", label: "以新版资料为准" }, { key: "keep", label: "维持现行要求" }] };
    const fake = mount({ ...DETAIL, approvals: [], planning_questions: [question,
      { decision_id: "pd-0", state: "ANSWERED", version: 2, question: "早先的问题", answer: "x", options: [] }] });
    expect(screen.getByText("资料换了版本，以哪一份为准？")).toBeTruthy();
    expect(screen.queryByText("早先的问题")).toBeNull();
    fireEvent.change(screen.getByLabelText("选择规划问题回答"), { target: { value: "follow-new-source" } });
    fireEvent.click(screen.getByRole("button", { name: "提交回答" }));
    const sent = fake.sent.find((m) => m.type === "mission_planning_answer")!;
    expect(sent.payload).toMatchObject({ decision_id: "pd-1", answer: "follow-new-source", expected_version: 1,
      attach_as_source: false });
    const before = fake.sent.filter((m) => m.type === "mission_get").length;
    fake.push({ type: "mission_planning_answer_response", payload: { ok: true, request_id: sent.request_id } });
    expect(fake.sent.filter((m) => m.type === "mission_get").length).toBe(before + 1);
  });
});
