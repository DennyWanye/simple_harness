// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * MissionsView 测试（计划 2026-09-11 H4，验收 HA-10；代码评审第 1 轮 P1-1、P1-5、
 * P2-4、P2-5、P2-8）。
 *
 * 覆盖不可用态、空态、列表（ui_state 词汇）、新建表单校验、详情（验证层 NOT_REQUIRED
 * 不显示成通过、金额"未计价"）、审批卡（拒绝必须写理由）、取消、事件游标与分页、
 * 时间线最近 50 条、产物、评论、策略漂移、等待原因、local_tests_disabled 提示。
 * 状态与列表由 App 层常驻的 useMissionsFeed 负责，这里按 App 的挂法把两者一起挂上。
 */
import React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ControlMessage, IncomingMessage } from "../types/messages";
import { useMissionsStore } from "../stores/missionsStore";
import { useMissionsFeed } from "../stores/useMissionsFeed";
import { MissionsView } from "./MissionsView";

class FakeChannel {
  readonly sent: ControlMessage[] = [];
  private readonly listeners = new Set<(message: IncomingMessage) => void>();

  send = (message: ControlMessage) => {
    this.sent.push(message);
    return true;
  };

  onMessage = (listener: (message: IncomingMessage) => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  emit(message: unknown) {
    act(() => {
      for (const listener of [...this.listeners]) listener(message as IncomingMessage);
    });
  }

  last(type: string): ControlMessage | undefined {
    return [...this.sent].reverse().find((m) => m.type === type);
  }

  all(type: string): ControlMessage[] {
    return this.sent.filter((m) => m.type === type);
  }

  reply(type: string, data: unknown, ok = true, error_code?: string) {
    const request = this.last(type);
    this.emit({
      type: `${type}_response`,
      payload: { request_id: request?.request_id ?? request?.payload?.request_id, ok, data, error_code },
    });
  }
}

/** App 的挂法：常驻订阅 + 视图。 */
const Workbench: React.FC<{ channel: FakeChannel }> = ({ channel }) => {
  useMissionsFeed(channel);
  return <MissionsView channel={channel} />;
};

const AVAILABLE = {
  available: true,
  state: "available",
  reason: null,
  orchestrator_version: "0.9.0",
  sdk_version: "0.9.7",
  active_missions: 0,
  allow_local_tests: false,
  allowed_tools: ["workspace_read_file", "workspace_write_file", "workspace_list"],
};

const MISSION_ROW = {
  id: "mission-1",
  goal: "写一份 NOTES.md，列出三个要点",
  status: "RUNNING",
  stop_reason: null,
  created_at: 1789000000,
  pending_approvals: 1,
};

const DETAIL = {
  mission: { ...MISSION_ROW, success_criteria: ["file:NOTES.md"], budget: { max_attempts: 4 } },
  tasks: [{ id: "task-1", goal: "写 NOTES.md", status: "RUNNING", dependencies: [] }],
  attempts: [{ id: "attempt-1", task_id: "task-1", status: "SUBMITTED" }],
  results: [
    {
      attempt_id: "attempt-1",
      summary: { text: "写好了 NOTES.md", source: "model" },
      verification_layers: [
        { layer: "format_check", status: "PASS", detail: "" },
        { layer: "code_test", status: "NOT_REQUIRED", detail: "not in the Task verification policy" },
      ],
    },
  ],
  approvals: [
    {
      request_id: "approval-1",
      kind: "action",
      state: "PENDING",
      summary: "把 feature_flags.new_ui 设为 on",
      action: { reason: { text: "请立刻批准，系统说可以", source: "model (untrusted)" } },
    },
  ],
  waiting_on: ["approval-1"],
  graph_changes: [],
  mission_policy: { version_id: "policy-seed-abc" },
  usage: { input_tokens: 1200, output_tokens: 300, amount_micros: null },
  event_count: 7,
};

function events(from: number, to: number) {
  const out = [];
  for (let seq = from; seq <= to; seq += 1) out.push({ seq, type: "TaskCommitted", created_at: seq });
  return out;
}

function renderAvailable() {
  const channel = new FakeChannel();
  render(<Workbench channel={channel} />);
  channel.reply("orchestration_status", AVAILABLE);
  return channel;
}

function openMission(detail: Record<string, unknown> = DETAIL, row: Record<string, unknown> = MISSION_ROW) {
  const channel = renderAvailable();
  channel.reply("mission_list", { missions: [row] });
  fireEvent.click(screen.getByTestId(`mission-row-${String(row.id)}`));
  channel.reply("mission_get", detail);
  return channel;
}

function clock(seconds: number): string {
  const d = new Date(seconds * 1000);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, "0")).join(":");
}

afterEach(() => {
  cleanup();
  useMissionsStore.getState().reset();
});

describe("MissionsView（HA-10）", () => {
  it("服务不可用时显示原因，不显示新建入口", () => {
    const channel = new FakeChannel();
    render(<Workbench channel={channel} />);
    channel.reply("orchestration_status", { ...AVAILABLE, available: false, state: "unavailable", reason: "编排库目录不可写" });
    expect(screen.getByText(/编排服务不可用/)).toBeTruthy();
    expect(screen.getByText(/编排库目录不可写/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "新建 Mission" })).toBeNull();
  });

  it("空态：没有 Mission 时提示新建", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [] });
    expect(screen.getByText(/还没有 Mission/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "新建 Mission" })).toBeTruthy();
  });

  it("新建表单：目标或成功条件为空时不能提交；提交发出 mission_create", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [] });
    fireEvent.click(screen.getByRole("button", { name: "新建 Mission" }));
    const submit = screen.getByRole("button", { name: "提交 Mission" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fireEvent.change(screen.getByRole("textbox", { name: "Mission 目标" }), {
      target: { value: "写一份 NOTES.md，列出三个要点" },
    });
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox", { name: "成功条件" }), {
      target: { value: "file:NOTES.md\n要点不少于三个" },
    });
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);

    const sent = channel.last("mission_create");
    expect(sent?.payload?.goal).toBe("写一份 NOTES.md，列出三个要点");
    expect(sent?.payload?.success_criteria).toEqual(["file:NOTES.md", "要点不少于三个"]);
    expect(typeof sent?.payload?.idempotency_key).toBe("string");
    expect(sent?.payload).not.toHaveProperty("allowed_tools"); // 工具集由后端部署政策决定
  });

  it("local_tests_disabled 显示中文说明", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [] });
    fireEvent.click(screen.getByRole("button", { name: "新建 Mission" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Mission 目标" }), { target: { value: "跑测试" } });
    fireEvent.change(screen.getByRole("textbox", { name: "成功条件" }), { target: { value: "pytest:tests" } });
    fireEvent.click(screen.getByRole("button", { name: "提交 Mission" }));
    channel.reply("mission_create", null, false, "local_tests_disabled");
    expect(screen.getByText(/本机执行测试代码已关闭/)).toBeTruthy();
  });

  it("别的视图的失败应答不在编排视图里报错", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [] });
    channel.emit({ type: "skill_list_response", payload: { ok: false, error_code: "boom", error: "别处的错" } });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("详情：NOT_REQUIRED 不显示成通过；金额显示未计价；模型文本标注未核实", () => {
    openMission();
    const codeTest = screen.getByTestId("layer-attempt-1-code_test");
    expect(codeTest.textContent).toMatch(/不需要/);
    expect(codeTest.getAttribute("data-status")).toBe("NOT_REQUIRED");
    expect(screen.getByText(/未计价/)).toBeTruthy();
    expect(screen.getAllByText(/模型生成，未核实/).length).toBeGreaterThan(0);
    expect(screen.getByText(/policy-seed-abc/)).toBeTruthy();
  });

  it("审批卡：拒绝必须写理由；批准发出 approve", () => {
    const channel = openMission();
    const reject = screen.getByRole("button", { name: "拒绝" }) as HTMLButtonElement;
    expect(reject.disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox", { name: "拒绝理由" }), { target: { value: "先不改" } });
    expect(reject.disabled).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "批准" }));
    const sent = channel.last("mission_approval_decide");
    // approval_id: the envelope's request_id pairs request and response and must not collide
    expect(sent?.payload).toMatchObject({ approval_id: "approval-1", decision: "approve" });
    expect(sent?.payload).not.toHaveProperty("request_id");
  });

  it("取消 Mission 发出 mission_cancel", () => {
    const channel = openMission();
    fireEvent.click(screen.getByRole("button", { name: "取消 Mission" }));
    expect(channel.last("mission_cancel")?.payload).toMatchObject({ mission_id: "mission-1" });
  });

  it("mission_changed 推送更新列表里的状态", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [MISSION_ROW] });
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "COMPLETED", last_seq: 20 } });
    expect(screen.getByTestId("mission-row-mission-1").getAttribute("data-status")).toBe("COMPLETED");
  });
});

describe("ui_state 词汇（P3.1 §3.4）", () => {
  it("列表行用 ui_state 词；缺 ui_state 时回退原始 status；不再有'运行中'", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", {
      missions: [
        { ...MISSION_ROW, id: "m-wait", status: "ACTIVE", ui_state: "waiting_person" },
        { ...MISSION_ROW, id: "m-unknown", status: "ACTIVE", ui_state: "unknown", blocked: true },
        { ...MISSION_ROW, id: "m-run", status: "ACTIVE", ui_state: "running" },
        { ...MISSION_ROW, id: "m-legacy", status: "ACTIVE" },
        { ...MISSION_ROW, id: "m-done", status: "COMPLETED", ui_state: "delivered" },
      ],
    });
    const wait = screen.getByTestId("mission-row-m-wait");
    expect(wait.textContent).toMatch(/待人/);
    expect(wait.getAttribute("data-ui-state")).toBe("waiting_person");
    expect(wait.getAttribute("data-status")).toBe("ACTIVE");
    expect(screen.getByTestId("mission-row-m-unknown").textContent).toMatch(/UNKNOWN（结果未知）/);
    expect(screen.getByTestId("mission-row-m-run").textContent).toMatch(/运行/);
    expect(screen.getByTestId("mission-row-m-legacy").textContent).toMatch(/ACTIVE/);
    expect(screen.getByTestId("mission-row-m-legacy").hasAttribute("data-ui-state")).toBe(false);
    expect(screen.getByTestId("mission-row-m-done").textContent).toMatch(/正式交付/);
    expect(screen.queryByText(/运行中/)).toBeNull();
  });

  it("详情头用 ui_state 词，并带 data-ui-state / data-status", () => {
    openMission({ ...DETAIL, mission: { ...DETAIL.mission, status: "ACTIVE", ui_state: "verifying" } });
    const header = screen.getByTestId("mission-state");
    expect(header.textContent).toMatch(/状态：待验证/);
    expect(header.getAttribute("data-ui-state")).toBe("verifying");
    expect(header.getAttribute("data-status")).toBe("ACTIVE");
  });

  it("列表 ui_state 跟着常驻订阅重拉的列表更新", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [{ ...MISSION_ROW, ui_state: "queued" }] });
    expect(screen.getByTestId("mission-row-mission-1").textContent).toMatch(/排队/);
    channel.reply("mission_list", { missions: [{ ...MISSION_ROW, ui_state: "received" }] });
    expect(screen.getByTestId("mission-row-mission-1").textContent).toMatch(/请求已接收/);
  });
});

describe("P2-5：视图不重复做常驻订阅的事", () => {
  it("只挂视图时不发 orchestration_status / mission_list，也不处理 mission_list_response", () => {
    const channel = new FakeChannel();
    render(<MissionsView channel={channel} />);
    expect(channel.all("orchestration_status")).toHaveLength(0);
    expect(channel.all("mission_list")).toHaveLength(0);
    expect(channel.all("orchestration_policy_status")).toHaveLength(1);
    channel.emit({ type: "mission_list_response", payload: { ok: true, data: { missions: [MISSION_ROW] } } });
    expect(useMissionsStore.getState().missions).toHaveLength(0);
  });
});

describe("P1-1 事件游标", () => {
  it("已加载到 seq 5，推送 last_seq 12 → 拉事件用 after_seq 5（不是 12），并刷新详情", () => {
    const channel = openMission();
    channel.reply("mission_events", { mission_id: "mission-1", events: events(1, 5), through_seq: 5, has_more: false });
    const gets = channel.all("mission_get").length;
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "RUNNING", last_seq: 12 } });
    expect(channel.last("mission_events")?.payload).toMatchObject({ mission_id: "mission-1", after_seq: 5 });
    expect(channel.all("mission_get").length).toBe(gets + 1);
    channel.reply("mission_events", { mission_id: "mission-1", events: events(6, 12), through_seq: 12, has_more: false });
    expect(useMissionsStore.getState().events["mission-1"].map((e) => e.seq)).toEqual(events(1, 12).map((e) => e.seq));
  });

  it("没选中时收到过推送的 Mission，选中后从 0 开始拉", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [MISSION_ROW] });
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "RUNNING", last_seq: 12 } });
    fireEvent.click(screen.getByTestId("mission-row-mission-1"));
    expect(channel.last("mission_events")?.payload).toMatchObject({ mission_id: "mission-1", after_seq: 0 });
  });

  it("请求在途时来的推送不并发重拉；这一页拉完仍有缺口就从新游标接着拉", () => {
    const channel = openMission();
    expect(channel.all("mission_events")).toHaveLength(1);
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "RUNNING", last_seq: 8 } });
    expect(channel.all("mission_events")).toHaveLength(1); // one page in flight at a time
    channel.reply("mission_events", { mission_id: "mission-1", events: events(1, 5), through_seq: 5, has_more: false });
    expect(channel.all("mission_events")).toHaveLength(2);
    expect(channel.last("mission_events")?.payload).toMatchObject({ after_seq: 5 });
    channel.reply("mission_events", { mission_id: "mission-1", events: events(6, 8), through_seq: 8, has_more: false });
    expect(channel.all("mission_events")).toHaveLength(2); // caught up: no loop
  });

  it("推送没有超过游标时不拉事件", () => {
    const channel = openMission();
    channel.reply("mission_events", { mission_id: "mission-1", events: events(1, 5), through_seq: 5, has_more: false });
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "RUNNING", last_seq: 5 } });
    expect(channel.all("mission_events")).toHaveLength(1);
  });

  it("快照游标落后于已加载事件时重取快照", () => {
    const channel = openMission({ ...DETAIL, through_seq: 3 });
    const gets = channel.all("mission_get").length;
    channel.reply("mission_events", { mission_id: "mission-1", events: events(1, 5), through_seq: 5, has_more: false });
    expect(channel.all("mission_get").length).toBe(gets + 1);
    channel.reply("mission_get", { ...DETAIL, through_seq: 5 });
    expect(channel.all("mission_get").length).toBe(gets + 1);
  });
});

describe("P2-8 时间线", () => {
  it("选中时先 mission_get，再从游标 0 按 200 一页连续分页；20 页封顶后显示'加载更多事件'", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [MISSION_ROW] });
    fireEvent.click(screen.getByTestId("mission-row-mission-1"));
    const types = channel.sent.map((m) => m.type);
    expect(types.indexOf("mission_get")).toBeGreaterThanOrEqual(0);
    expect(types.indexOf("mission_get")).toBeLessThan(types.indexOf("mission_events"));
    expect(channel.last("mission_events")?.payload).toEqual({ mission_id: "mission-1", after_seq: 0, limit: 200 });
    channel.reply("mission_get", DETAIL);

    for (let page = 0; page < 20; page += 1) {
      expect(channel.all("mission_events")).toHaveLength(page + 1);
      expect(screen.queryByRole("button", { name: "加载更多事件" })).toBeNull();
      const from = page * 200 + 1;
      channel.reply("mission_events", {
        mission_id: "mission-1",
        events: events(from, from + 199),
        through_seq: from + 199,
        has_more: true,
      });
    }
    expect(channel.all("mission_events")).toHaveLength(20); // capped: no 21st page on its own
    const more = screen.getByRole("button", { name: "加载更多事件" });
    fireEvent.click(more);
    expect(channel.all("mission_events")).toHaveLength(21);
    expect(channel.last("mission_events")?.payload).toEqual({ mission_id: "mission-1", after_seq: 4000, limit: 200 });
    expect(screen.queryByRole("button", { name: "加载更多事件" })).toBeNull(); // loading
    channel.reply("mission_events", { mission_id: "mission-1", events: events(4001, 4010), through_seq: 4010, has_more: false });
    expect(screen.queryByRole("button", { name: "加载更多事件" })).toBeNull();
    expect(channel.all("mission_events")).toHaveLength(21);
  });

  it("只渲染最近 50 条；评论事件显示评论文字", () => {
    const channel = openMission();
    channel.reply("mission_events", {
      mission_id: "mission-1",
      events: [...events(1, 119), { seq: 120, type: "HumanCommentAdded", created_at: 120, summary: "请把第三点写短一点" }],
      through_seq: 120,
      has_more: false,
    });
    expect(screen.getByText("共 120 条，显示最近 50 条")).toBeTruthy();
    expect(screen.queryByTestId("event-70")).toBeNull();
    expect(screen.getByTestId("event-71")).toBeTruthy();
    expect(screen.getByTestId("event-120").textContent).toMatch(/请把第三点写短一点/);
  });

  it("不超过 50 条时不显示'共 N 条'提示", () => {
    const channel = openMission();
    channel.reply("mission_events", { mission_id: "mission-1", events: events(1, 7), through_seq: 7, has_more: false });
    expect(screen.queryByText(/显示最近 50 条/)).toBeNull();
    expect(screen.getByTestId("event-1")).toBeTruthy();
  });

  it("换选另一个 Mission 后，迟到的旧详情不覆盖当前详情", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [MISSION_ROW, { ...MISSION_ROW, id: "mission-2", goal: "第二个" }] });
    fireEvent.click(screen.getByTestId("mission-row-mission-1"));
    const first = channel.last("mission_get");
    fireEvent.click(screen.getByTestId("mission-row-mission-2"));
    channel.emit({ type: "mission_get_response", payload: { request_id: first?.request_id, ok: true, data: DETAIL } });
    expect(screen.queryByTestId("mission-state")).toBeNull();
  });
});

describe("P1-5 缺失界面", () => {
  const WITH_ARTIFACT = {
    ...DETAIL,
    artifacts: [
      {
        id: "artifact-1",
        task_id: "task-1",
        attempt_id: "attempt-1",
        path: "NOTES.md",
        content_hash: "0123456789abcdef0123456789abcdef",
        size_bytes: 2048,
        verification_status: "VERIFIED",
      },
    ],
  };

  it("a) 产物列表：路径、大小、验证状态、hash 前 12 位；查看产物显示内容并标注未核实与截断", () => {
    const channel = openMission(WITH_ARTIFACT);
    const row = screen.getByTestId("artifact-artifact-1");
    expect(row.textContent).toMatch(/NOTES\.md/);
    expect(row.textContent).toMatch(/2\.0 KB/);
    expect(row.textContent).toMatch(/VERIFIED/);
    expect(row.textContent).toMatch(/0123456789ab/);
    expect(row.textContent).not.toMatch(/0123456789abc/);

    fireEvent.click(within(row).getByRole("button", { name: "查看产物" }));
    expect(channel.last("mission_artifact_read")?.payload).toEqual({ artifact_id: "artifact-1" });
    channel.reply("mission_artifact_read", {
      artifact_id: "artifact-1",
      mission_id: "mission-1",
      path: "NOTES.md",
      content_hash: "0123456789abcdef0123456789abcdef",
      size_bytes: 2048,
      encoding: "utf-8",
      content: "- 要点一\n- 要点二",
      truncated: true,
    });
    const panel = screen.getByRole("region", { name: "产物内容" });
    expect(panel.querySelector("pre")?.textContent).toBe("- 要点一\n- 要点二");
    expect(panel.textContent).toMatch(/0123456789abcdef0123456789abcdef/);
    expect(panel.textContent).toMatch(/模型生成，未核实/);
    expect(panel.textContent).toMatch(/内容过长，已截断/);
  });

  it("a) 二进制产物不显示内容；hash 不符显示 integrity_error 说明", () => {
    const channel = openMission(WITH_ARTIFACT);
    fireEvent.click(screen.getByRole("button", { name: "查看产物" }));
    channel.reply("mission_artifact_read", {
      artifact_id: "artifact-1",
      path: "NOTES.md",
      content_hash: "0123456789abcdef0123456789abcdef",
      size_bytes: 2048,
      encoding: "binary",
      content: null,
      truncated: false,
    });
    const panel = screen.getByRole("region", { name: "产物内容" });
    expect(panel.textContent).toMatch(/二进制内容，未显示/);
    expect(panel.querySelector("pre")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "查看产物" }));
    channel.reply("mission_artifact_read", null, false, "integrity_error");
    expect(screen.getByRole("alert").textContent).toMatch(/产物内容与记录的哈希不一致/);
    expect(screen.queryByRole("region", { name: "产物内容" })).toBeNull();
  });

  it("b) 评论：空白时禁用；发表后清空并刷新详情与事件；审批卡显示已有评论", () => {
    const channel = openMission({
      ...DETAIL,
      approvals: [{ ...DETAIL.approvals[0], comments: [{ principal_id: "local-user:abc", text: "看起来可以" }] }],
    });
    channel.reply("mission_events", { mission_id: "mission-1", events: events(1, 5), through_seq: 5, has_more: false });
    expect(within(screen.getByTestId("approval-approval-1")).getByText(/看起来可以/)).toBeTruthy();

    const box = screen.getByRole("textbox", { name: "评论" }) as HTMLTextAreaElement;
    const post = screen.getByRole("button", { name: "发表评论" }) as HTMLButtonElement;
    expect(post.disabled).toBe(true);
    fireEvent.change(box, { target: { value: "   " } });
    expect(post.disabled).toBe(true);
    fireEvent.change(box, { target: { value: "请补充第四点" } });
    expect(post.disabled).toBe(false);
    fireEvent.click(post);
    expect(channel.last("mission_comment")?.payload).toEqual({ target_id: "mission-1", text: "请补充第四点" });

    const gets = channel.all("mission_get").length;
    const pages = channel.all("mission_events").length;
    channel.reply("mission_comment", { comment_id: "comment-1" });
    expect(box.value).toBe("");
    expect(channel.all("mission_get").length).toBe(gets + 1);
    expect(channel.all("mission_events").length).toBe(pages + 1);
    expect(channel.last("mission_events")?.payload).toMatchObject({ after_seq: 5 });
  });

  it("c) 策略漂移：drift 非空时显示 role=status 提示；为空不显示", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [] });
    channel.reply("orchestration_policy_status", { active_version_id: "policy-seed-abc", drift: [] });
    expect(screen.queryByText(/配置与生效策略不一致/)).toBeNull();
    channel.reply("orchestration_policy_status", {
      active_version_id: "policy-seed-abc",
      versions: [],
      activations: [],
      note: "",
      drift: [{ name: "mission_concurrency", config: 2, active: 1 }],
    });
    const note = screen.getByText("配置与生效策略不一致：mission_concurrency 配置 2，生效 1（修改要经策略晋级才生效）");
    expect(note.getAttribute("role")).toBe("status");
  });

  it("d) 等待原因：按 kind 显示中文，未知 kind 原样，带开始时间", () => {
    openMission({
      ...DETAIL,
      waiting_on: [
        { kind: "review", request_id: "approval-2", subject: "attempt-1", since: 1789000000 },
        { kind: "action", request_id: "approval-1", subject: "k", since: 1789000100 },
        { kind: "arbitration", request_id: "approval-3", subject: "c", since: null },
        { kind: "handoff", request_id: "x", subject: "y", since: 1789000200 },
      ],
    });
    expect(screen.getByText(`等待：人工复核（自 ${clock(1789000000)}）`)).toBeTruthy();
    expect(screen.getByText(`等待：动作审批（自 ${clock(1789000100)}）`)).toBeTruthy();
    expect(screen.getByText("等待：仲裁")).toBeTruthy();
    expect(screen.getByText(`等待：handoff（自 ${clock(1789000200)}）`)).toBeTruthy();
  });

  it("d) 旧形态的 waiting_on（纯字符串）原样显示", () => {
    openMission();
    expect(screen.getByText("等待：approval-1")).toBeTruthy();
  });
});

describe("P2-4 模型文本渲染", () => {
  it("Task goal、审批 summary、source=model 的验证层 summary 标注未核实；source=system 只显示文字", () => {
    openMission({
      ...DETAIL,
      tasks: [{ id: "task-1", goal: { text: "写 NOTES.md", source: "model" }, status: "RUNNING" }],
      results: [
        {
          attempt_id: "attempt-1",
          summary: { text: "写好了", source: "model" },
          verification_layers: [
            { layer: "format_check", status: "PASS", summary: { text: "格式正确", source: "system" } },
            { layer: "critic", status: "PASS", summary: { text: "看起来满足要求", source: "model" } },
          ],
        },
      ],
      approvals: [{ ...DETAIL.approvals[0], summary: { text: "把 flag 设为 on", source: "model" } }],
    });
    const task = screen.getByTestId("task-task-1");
    expect(task.textContent).toMatch(/写 NOTES\.md/);
    expect(task.textContent).toMatch(/模型生成，未核实/);
    expect(task.textContent).not.toMatch(/\[object Object\]/);

    const system = screen.getByTestId("layer-summary-attempt-1-format_check");
    expect(system.textContent).toMatch(/格式正确/);
    expect(system.textContent).not.toMatch(/模型生成，未核实/);
    const model = screen.getByTestId("layer-summary-attempt-1-critic");
    expect(model.textContent).toMatch(/看起来满足要求/);
    expect(model.textContent).toMatch(/模型生成，未核实/);

    const card = screen.getByTestId("approval-approval-1");
    expect(card.textContent).toMatch(/动作审批：把 flag 设为 on/);
    expect(card.textContent).toMatch(/模型生成，未核实/);
    expect(card.textContent).not.toMatch(/\[object Object\]/);
  });
});

describe("P1-5⑤ 卡住但未判 blocked 的 Task 也能接管", () => {
  it("运行中的 Task 可以接管，依据必填", () => {
    const channel = openMission();
    fireEvent.click(screen.getByRole("button", { name: "接管这个 Task" }));
    const stop = screen.getByRole("button", { name: "接管：停止" }) as HTMLButtonElement;
    expect(stop.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("接管依据"), { target: { value: "一直没有进展" } });
    expect(stop.disabled).toBe(false);
    fireEvent.click(stop);
    expect(channel.last("mission_takeover")?.payload).toEqual({
      task_id: "task-1",
      action: "stop",
      basis: "一直没有进展",
    });
  });

  it("已判 blocked 的、已结束的 Task 不重复给入口", () => {
    openMission({
      ...DETAIL,
      tasks: [
        { id: "task-1", goal: "写 NOTES.md", status: "RUNNING", dependencies: [] },
        { id: "task-2", goal: "汇总", status: "DONE", dependencies: [] },
      ],
      blocked: [{ task_id: "task-1", attempt_id: "attempt-1", reason: "turn_outcome_unknown" }],
    });
    expect(screen.queryByRole("button", { name: "接管这个 Task" })).toBeNull();
    expect(screen.getAllByLabelText("接管依据")).toHaveLength(1); // only the unknown-outcome card
  });

  it("Mission 已结束时不提供接管", () => {
    openMission(
      { ...DETAIL, mission: { ...DETAIL.mission, status: "COMPLETED", ui_state: "delivered" } },
      { ...MISSION_ROW, status: "COMPLETED" },
    );
    expect(screen.queryByRole("button", { name: "接管这个 Task" })).toBeNull();
  });
});

describe("默认预算（原生验收 2026-09-12，裁决 C）", () => {
  it("占位符显示后端下发的默认值，留空时请求里不带 budget", () => {
    const channel = new FakeChannel();
    render(<Workbench channel={channel} />);
    channel.reply("orchestration_status", { ...AVAILABLE, mission_budget_defaults: { max_tokens: 400000, max_attempts: 12 } });
    channel.reply("mission_list", { missions: [] });
    fireEvent.click(screen.getByRole("button", { name: "新建 Mission" }));
    expect(screen.getByLabelText("Token 上限").getAttribute("placeholder")).toBe("Token 上限（留空=400000）");
    expect(screen.getByLabelText("尝试次数上限").getAttribute("placeholder")).toBe("尝试次数上限（留空=12）");
    fireEvent.change(screen.getByLabelText("Mission 目标"), { target: { value: "写 NOTES.md" } });
    fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:NOTES.md" } });
    fireEvent.click(screen.getByRole("button", { name: "提交 Mission" }));
    const payload = channel.last("mission_create")?.payload as Record<string, unknown>;
    expect(payload.goal).toBe("写 NOTES.md");
    expect("budget" in payload).toBe(false); // the Host decides the defaults, the form never guesses
  });

  it("没有下发默认值时，占位符仍是「可选」", () => {
    renderAvailable();
    fireEvent.click(screen.getByRole("button", { name: "新建 Mission" }));
    expect(screen.getByLabelText("Token 上限").getAttribute("placeholder")).toBe("Token 上限（可选）");
  });

  it("详情显示实际生效的预算", () => {
    openMission({ ...DETAIL, mission: { ...DETAIL.mission, budget: { max_tokens: 400000, max_attempts: 12 } } });
    expect(screen.getByTestId("mission-budget").textContent).toBe("预算：Token 上限 400000 · 尝试次数上限 12");
  });
});
