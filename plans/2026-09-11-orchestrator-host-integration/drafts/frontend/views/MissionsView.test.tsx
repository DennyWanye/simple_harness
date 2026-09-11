// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * MissionsView 测试（计划 2026-09-11 H4，验收 HA-10）。
 *
 * 草稿：实现之前写的 oracle。覆盖不可用态、空态、列表、新建表单校验、
 * 详情（验证层 NOT_REQUIRED 不显示成通过、金额"未计价"）、审批卡（拒绝必须写理由）、
 * 取消、增量加载事件、mission_changed 推送、local_tests_disabled 提示。
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ControlMessage, IncomingMessage } from "../types/messages";
import { useMissionsStore } from "../stores/missionsStore";
import { MissionsView } from "./MissionsView";

class FakeChannel {
  readonly sent: ControlMessage[] = [];
  private listener: ((message: IncomingMessage) => void) | null = null;

  send = (message: ControlMessage) => {
    this.sent.push(message);
    return true;
  };

  onMessage = (listener: (message: IncomingMessage) => void) => {
    this.listener = listener;
    return () => {
      if (this.listener === listener) this.listener = null;
    };
  };

  emit(message: unknown) {
    act(() => this.listener?.(message as IncomingMessage));
  }

  last(type: string): ControlMessage | undefined {
    return [...this.sent].reverse().find((m) => m.type === type);
  }

  reply(type: string, data: unknown, ok = true, error_code?: string) {
    const request = this.last(type);
    this.emit({
      type: `${type}_response`,
      payload: { request_id: request?.request_id ?? request?.payload?.request_id, ok, data, error_code },
    });
  }
}

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

function renderAvailable() {
  const channel = new FakeChannel();
  render(<MissionsView channel={channel} />);
  channel.reply("orchestration_status", AVAILABLE);
  return channel;
}

afterEach(() => {
  cleanup();
  useMissionsStore.getState().reset();
});

describe("MissionsView（HA-10）", () => {
  it("服务不可用时显示原因，不显示新建入口", () => {
    const channel = new FakeChannel();
    render(<MissionsView channel={channel} />);
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

  it("详情：NOT_REQUIRED 不显示成通过；金额显示未计价；模型文本标注未核实", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [MISSION_ROW] });
    fireEvent.click(screen.getByText(/写一份 NOTES.md/));
    channel.reply("mission_get", DETAIL);

    const codeTest = screen.getByTestId("layer-attempt-1-code_test");
    expect(codeTest.textContent).toMatch(/不需要/);
    expect(codeTest.getAttribute("data-status")).toBe("NOT_REQUIRED");
    expect(screen.getByText(/未计价/)).toBeTruthy();
    expect(screen.getAllByText(/模型生成，未核实/).length).toBeGreaterThan(0);
    expect(screen.getByText(/policy-seed-abc/)).toBeTruthy();
  });

  it("审批卡：拒绝必须写理由；批准发出 approve", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [MISSION_ROW] });
    fireEvent.click(screen.getByText(/写一份 NOTES.md/));
    channel.reply("mission_get", DETAIL);

    const reject = screen.getByRole("button", { name: "拒绝" }) as HTMLButtonElement;
    expect(reject.disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox", { name: "拒绝理由" }), { target: { value: "先不改" } });
    expect(reject.disabled).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "批准" }));
    const sent = channel.last("mission_approval_decide");
    expect(sent?.payload).toMatchObject({ request_id: "approval-1", decision: "approve" });
  });

  it("取消 Mission 发出 mission_cancel", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [MISSION_ROW] });
    fireEvent.click(screen.getByText(/写一份 NOTES.md/));
    channel.reply("mission_get", DETAIL);
    fireEvent.click(screen.getByRole("button", { name: "取消 Mission" }));
    expect(channel.last("mission_cancel")?.payload).toMatchObject({ mission_id: "mission-1" });
  });

  it("加载更多事件带上 after_seq", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [MISSION_ROW] });
    fireEvent.click(screen.getByText(/写一份 NOTES.md/));
    channel.reply("mission_get", DETAIL);
    channel.reply("mission_events", {
      events: [{ seq: 1, type: "MissionCreated", created_at: 1, summary: "" }],
      last_seq: 1,
      has_more: true,
    });
    fireEvent.click(screen.getByRole("button", { name: "加载更多事件" }));
    expect(channel.last("mission_events")?.payload).toMatchObject({ mission_id: "mission-1", after_seq: 1 });
  });

  it("mission_changed 推送更新列表里的状态", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [MISSION_ROW] });
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "COMPLETED", last_seq: 20 } });
    expect(screen.getByTestId("mission-row-mission-1").getAttribute("data-status")).toBe("COMPLETED");
  });
});
