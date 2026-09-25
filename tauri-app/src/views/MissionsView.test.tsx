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
import { act, cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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
  deployment_manifest: { features: { domains: { atomic_source_create: true, citation_read: true, items: [{ id: "doc-research-v1", version: "4" }], source_commands: ["register", "supersede", "revoke"] } } },
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

function fillValidSynthesisForm({ missionBudget = true }: { missionBudget?: boolean } = {}) {
  fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "完成研究" } });
  fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:REPORT.md" } });
  if (missionBudget) {
    fireEvent.change(screen.getByLabelText("Token 上限"), { target: { value: "100" } });
    fireEvent.change(screen.getByLabelText("尝试次数上限"), { target: { value: "2" } });
  }
  fireEvent.click(screen.getByLabelText("最终独立综合"));
  fireEvent.change(screen.getByLabelText("最终独立综合目标"), { target: { value: "独立汇总" } });
  fireEvent.change(screen.getByLabelText("最终独立综合成功条件"), { target: { value: "file:FINAL.md" } });
  fireEvent.change(screen.getByLabelText("最终独立综合 Token 上限"), { target: { value: "50" } });
  fireEvent.change(screen.getByLabelText("最终独立综合尝试次数上限"), { target: { value: "1" } });
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

// P33 G oracle, written before UI implementation. These are frontend controls;
// P33-20 native/provider acceptance remains the main task's separate gate.
describe("P33 G creation and explicit approval branches", () => {
  it.each(["met", "unmet"])("preserves legacy code judgment arbitration and dispatches %s", (ruling) => {
    const channel = openMission({ ...DETAIL, approvals: [{
      request_id: "code-judgment", kind: "arbitration", topic: "judgment", state: "PENDING", options: ["met", "unmet"],
    }] });
    const approval = screen.getByTestId("approval-code-judgment");
    const choice = within(approval).getByRole("button", { name: `裁决：${ruling}` }) as HTMLButtonElement;
    expect(choice.disabled).toBe(true);
    fireEvent.change(within(approval).getByLabelText("仲裁依据"), { target: { value: "实际验收依据" } });
    fireEvent.click(choice);
    expect(channel.last("mission_approval_decide")?.payload).toEqual({
      approval_id: "code-judgment", decision: "arbitrate", ruling, basis: "实际验收依据",
    });
  });

  it("an older Host without document capabilities keeps the code entry only", () => {
    const channel = new FakeChannel();
    render(<Workbench channel={channel} />);
    channel.reply("orchestration_status", { ...AVAILABLE, deployment_manifest: null });
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    expect(screen.queryByRole("option", { name: "文档研究" })).toBeNull();
    expect((screen.getByLabelText("任务类型") as HTMLSelectElement).value).toBe("code");
  });
  it("shows the local shared256K window and submits its default profile", () => {
    const channel = renderAvailable();
    channel.reply("orchestration_status", { ...AVAILABLE,
      default_context_profile_id: "local-context-256k-v1",
      context_profiles: [{ profile_id: "local-context-256k-v1", max_input_tokens: 228352,
        max_total_tokens: 262144, output_reserve: 32768, safety_margin: 1024,
        default_max_output_tokens: 8192, max_output_tokens_ceiling: 32768, mission_max_tokens: 4000000 }],
    });
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    expect(screen.getByRole("option", { name: "256K 总窗口" })).toBeTruthy();
    expect(screen.getByText(/输入与输出共享 256K 总窗口；输入最多 223K/)).toBeTruthy();
    expect(screen.queryByText(/单次输出另计/)).toBeNull();
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "local model report" } });
    fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:REPORT.md" } });
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    expect(channel.last("mission_create")?.payload?.runtime_profile_id).toBe("local-context-256k-v1");
  });

  it("selects the actual long context and announces its separate total budget", () => {
    const channel = renderAvailable();
    channel.reply("orchestration_status", { ...AVAILABLE,
      default_context_profile_id: "deepseek-context-256k-v1",
      context_profiles: [262144, 524288].map((tokens) => ({
        profile_id: `deepseek-context-${tokens / 1024}k-v1`, max_input_tokens: tokens,
        default_max_output_tokens: 8192, max_output_tokens_ceiling: 32768,
        mission_max_tokens: tokens === 262144 ? 4000000 : 8000000,
      })),
    });
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    expect((screen.getByLabelText("输入上下文容量") as HTMLSelectElement).value).toBe("deepseek-context-256k-v1");
    expect(screen.getByLabelText("Token 上限").getAttribute("placeholder")).toContain("4000000");
    fireEvent.change(screen.getByLabelText("输入上下文容量"), { target: { value: "deepseek-context-512k-v1" } });
    expect(screen.getByLabelText("Token 上限").getAttribute("placeholder")).toContain("8000000");
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "long references" } });
    fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:REPORT.md" } });
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    expect(channel.last("mission_create")?.payload?.runtime_profile_id).toBe("deepseek-context-512k-v1");
  });

  it("keeps the creation key and capacity when a lost receipt is retried after default drift", () => {
    vi.useFakeTimers();
    try {
      const channel = renderAvailable();
      const profiles = [262144, 524288].map((tokens) => ({
        profile_id: `deepseek-context-${tokens / 1024}k-v1`, max_input_tokens: tokens,
        default_max_output_tokens: 8192, max_output_tokens_ceiling: 32768,
        mission_max_tokens: 8000000,
      }));
      channel.reply("orchestration_status", { ...AVAILABLE, context_profiles: profiles, default_context_profile_id: profiles[0].profile_id });
      fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
      fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "lost receipt" } });
      fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:REPORT.md" } });
      fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
      const original = channel.last("mission_create")?.payload;
      act(() => { vi.advanceTimersByTime(30001); });
      channel.reply("orchestration_status", { ...AVAILABLE, context_profiles: profiles, default_context_profile_id: profiles[1].profile_id });
      const send = channel.send;
      channel.send = () => false;
      fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
      channel.send = send;
      fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
      expect(channel.last("mission_create")?.payload).toEqual(original);
    } finally { vi.useRealTimers(); }
  });

  it("clears an unsent context selection when the replacement Host has no long profiles", () => {
    const channel = renderAvailable();
    channel.reply("orchestration_status", { ...AVAILABLE,
      default_context_profile_id: "deepseek-context-512k-v1", context_profiles: [{
        profile_id: "deepseek-context-512k-v1", max_input_tokens: 524288,
        default_max_output_tokens: 8192, max_output_tokens_ceiling: 32768, mission_max_tokens: 8000000,
      }],
    });
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fireEvent.change(screen.getByLabelText("输入上下文容量"), { target: { value: "deepseek-context-512k-v1" } });
    channel.reply("orchestration_status", AVAILABLE);
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "legacy entry" } });
    fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:REPORT.md" } });
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    expect(channel.all("mission_create")).toHaveLength(1);
    expect(channel.last("mission_create")?.payload).not.toHaveProperty("runtime_profile_id");
  });

  it("allows creation on a legacy Host after the initial long-context send was refused", () => {
    const channel = renderAvailable();
    channel.reply("orchestration_status", { ...AVAILABLE,
      default_context_profile_id: "deepseek-context-512k-v1", context_profiles: [{
        profile_id: "deepseek-context-512k-v1", max_input_tokens: 524288,
        default_max_output_tokens: 8192, max_output_tokens_ceiling: 32768, mission_max_tokens: 8000000,
      }],
    });
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "never sent" } });
    fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:REPORT.md" } });
    const send = channel.send;
    channel.send = () => false;
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    expect(channel.all("mission_create")).toHaveLength(0);
    channel.send = send;
    channel.reply("orchestration_status", AVAILABLE);
    expect(screen.queryByLabelText("输入上下文容量")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    expect(channel.all("mission_create")).toHaveLength(1);
    expect(channel.last("mission_create")?.payload).not.toHaveProperty("runtime_profile_id");
  });

  it("only offers approved search policies and submits the selected registry identity", () => {
    const channel = renderAvailable();
    channel.reply("orchestration_policy_status", { eligible_search_policies: [
      { version_id: "approved-v2", policy: { max_candidates: 2 } },
    ] });
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    expect((screen.getByLabelText("执行方式") as HTMLSelectElement).value).toBe("");
    fireEvent.change(screen.getByLabelText("执行方式"), { target: { value: "approved-v2" } });
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "compare" } });
    fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:REPORT.md" } });
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    expect(channel.last("mission_create")?.payload?.search_policy_version_id).toBe("approved-v2");
  });
  it("ignores a duplicate late snapshot/error even for the same selected Mission", () => {
    const channel = openMission();
    const old = channel.last("mission_get")!;
    channel.emit({ type: "mission_get_response", payload: { request_id: old.request_id, ok: true, data: { ...DETAIL, mission: { ...DETAIL.mission, goal: "late replacement" } } } });
    expect(screen.queryByText("late replacement")).toBeNull();
    channel.emit({ type: "mission_get_response", payload: { request_id: old.request_id, ok: false, error_code: "not_found" } });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("late artifact content cannot replace a newly selected artifact", () => {
    const channel = openMission({ ...DETAIL, artifacts: [
      { id: "a", path: "a.md" }, { id: "b", path: "b.md" },
    ] });
    fireEvent.click(within(screen.getByTestId("artifact-a")).getByRole("button"));
    const old = channel.last("mission_artifact_read")!;
    fireEvent.click(within(screen.getByTestId("artifact-b")).getByRole("button"));
    channel.reply("mission_artifact_read", { artifact_id: "b", path: "b.md", encoding: "utf-8", content: "new analysis" });
    channel.emit({ type: "mission_artifact_read_response", payload: { request_id: old.request_id, ok: true, data: { artifact_id: "a", path: "a.md", content: "old analysis" } } });
    expect(screen.getByText("new analysis")).toBeTruthy();
    expect(screen.queryByText("old analysis")).toBeNull();
  });

  it("renders document conclusions separately from Worker analysis", () => {
    openMission({ ...DETAIL, document: { schema_version: 1, claims: [{ id: "c", content: "正式主张", status: "SUPPORTED" }], reviews: [], criteria: [] } });
    expect(within(screen.getByRole("region", { name: "系统结论" })).getByText("正式主张")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "系统结论" })).queryByText(/写好了 NOTES/)).toBeNull();
    expect(screen.getByText(/分析 \/ 非结论（正文非结论陈述不做覆盖核对）/)).toBeTruthy();
  });

  it("defaults to code and preserves the existing mission_create request", () => {
    const channel = renderAvailable();
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    expect((screen.getByLabelText("任务类型") as HTMLSelectElement).value).toBe("code");
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "write code" } });
    fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:a.py" } });
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    expect(channel.last("mission_create")?.payload).toEqual({
      goal: "write code", success_criteria: ["file:a.py"], idempotency_key: expect.any(String),
    });
    expect(channel.all("mission_create_with_sources")).toHaveLength(0);
  });

  it.each(["code", "doc-research-v1"] as const)("在 %s Mission 中用相同的最终独立综合章程提交", (domain) => {
    const channel = renderAvailable();
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    if (domain === "doc-research-v1") {
      fireEvent.change(screen.getByLabelText("任务类型"), { target: { value: domain } });
      fireEvent.click(screen.getByRole("button", { name: "添加来源" }));
      fireEvent.change(screen.getByLabelText("来源路径 1"), { target: { value: "sources/a.md" } });
      fireEvent.change(screen.getByLabelText("来源正文 1"), { target: { value: "可供综合的资料" } });
    }
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "完成研究" } });
    fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:REPORT.md" } });
    fireEvent.change(screen.getByLabelText("Token 上限"), { target: { value: "240000" } });
    fireEvent.change(screen.getByLabelText("尝试次数上限"), { target: { value: "12" } });
    fireEvent.click(screen.getByLabelText("最终独立综合"));
    fireEvent.change(screen.getByLabelText("最终独立综合目标"), { target: { value: "独立汇总结论" } });
    fireEvent.change(screen.getByLabelText("最终独立综合成功条件"), { target: { value: "file:FINAL.md\n结论可追溯" } });
    fireEvent.change(screen.getByLabelText("最终独立综合 Token 上限"), { target: { value: "60000" } });
    fireEvent.change(screen.getByLabelText("最终独立综合尝试次数上限"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));

    const payload = domain === "code"
      ? channel.last("mission_create")?.payload
      : (channel.last("mission_create_with_sources")?.payload as { mission: Record<string, unknown> }).mission;
    expect(payload).toMatchObject({
      goal: "完成研究",
      success_criteria: ["file:REPORT.md"],
      budget: { max_tokens: 240000, max_attempts: 12 },
      synthesis: {
        goal: "独立汇总结论",
        success_criteria: ["file:FINAL.md", "结论可追溯"],
        budget: { max_tokens: 60000, max_attempts: 2 },
      },
    });
    expect(Object.keys((payload as { synthesis: Record<string, unknown> }).synthesis).sort()).toEqual(["budget", "goal", "success_criteria"]);
  });

  it.each([
    ["目标为空", "最终独立综合目标", ""],
    ["成功条件为空", "最终独立综合成功条件", ""],
    ["Token 为零", "最终独立综合 Token 上限", "0"],
    ["Token 为负数", "最终独立综合 Token 上限", "-1"],
    ["Token 为小数", "最终独立综合 Token 上限", "1.5"],
    ["Token 非数字", "最终独立综合 Token 上限", "NaN"],
    ["尝试次数为零", "最终独立综合尝试次数上限", "0"],
    ["尝试次数为负数", "最终独立综合尝试次数上限", "-1"],
    ["尝试次数为小数", "最终独立综合尝试次数上限", "1.5"],
    ["尝试次数非数字", "最终独立综合尝试次数上限", "NaN"],
  ])("最终独立综合%s时禁止提交", (_case, label, value) => {
    const channel = renderAvailable();
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fillValidSynthesisForm();
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
    const submit = screen.getByRole("button", { name: "提交任务" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toContain("最终独立综合");
    expect(channel.all("mission_create")).toHaveLength(0);
  });

  it("最终独立综合超过 Mission 上限或系统 Token 预留时禁止提交", () => {
    const channel = renderAvailable();
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fillValidSynthesisForm();
    const submit = screen.getByRole("button", { name: "提交任务" }) as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText("最终独立综合 Token 上限"), { target: { value: "101" } });
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("最终独立综合 Token 上限"), { target: { value: "100" } });
    fireEvent.change(screen.getByLabelText("最终独立综合尝试次数上限"), { target: { value: "3" } });
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("最终独立综合尝试次数上限"), { target: { value: "2" } });
    expect(submit.disabled).toBe(false);
    fireEvent.change(screen.getByLabelText("冲突核对预留 Token"), { target: { value: "1" } });
    expect(submit.disabled).toBe(true);
    expect(channel.all("mission_create")).toHaveLength(0);
  });

  it("最终独立综合失败时保留同一请求，成功后重置", () => {
    const channel = renderAvailable();
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fillValidSynthesisForm();
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    const first = channel.last("mission_create")?.payload as Record<string, unknown>;
    channel.reply("mission_create", {}, false, "invalid_request");
    expect((screen.getByLabelText("最终独立综合") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("最终独立综合目标") as HTMLTextAreaElement).value).toBe("独立汇总");
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    expect(channel.last("mission_create")?.payload).toEqual(first);
    channel.reply("mission_create", {}, false, "invalid_request");
    fireEvent.change(screen.getByLabelText("最终独立综合目标"), { target: { value: "修订后的独立汇总" } });
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    const changed = channel.last("mission_create")?.payload as Record<string, unknown>;
    expect(changed.idempotency_key).not.toBe(first.idempotency_key);
    expect((changed.synthesis as Record<string, unknown>).goal).toBe("修订后的独立汇总");
    channel.reply("mission_create", { mission_id: "mission-new" });
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    expect((screen.getByLabelText("最终独立综合") as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByLabelText("最终独立综合"));
    expect((screen.getByLabelText("最终独立综合目标") as HTMLTextAreaElement).value).toBe("");
    expect((screen.getByLabelText("最终独立综合成功条件") as HTMLTextAreaElement).value).toBe("");
    expect((screen.getByLabelText("最终独立综合 Token 上限") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("最终独立综合尝试次数上限") as HTMLInputElement).value).toBe("");
  });

  it("pastes sources and sends one atomic doc batch; failure preserves the draft", () => {
    const channel = renderAvailable();
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fireEvent.change(screen.getByLabelText("任务类型"), { target: { value: "doc-research-v1" } });
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "比较文档" } });
    fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "说明否定条件" } });
    expect((screen.getByRole("button", { name: "提交任务" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "添加来源" }));
    fireEvent.change(screen.getByLabelText("来源路径 1"), { target: { value: "sources/a.md" } });
    fireEvent.change(screen.getByLabelText("来源正文 1"), { target: { value: "条件：不支持。\r\n| A | B |" } });
    fireEvent.change(screen.getByLabelText("冲突核对预留 Token"), { target: { value: "30000" } });
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    expect(channel.all("mission_create")).toHaveLength(0);
    expect(channel.all("mission_source_register")).toHaveLength(0);
    const batch = channel.last("mission_create_with_sources")?.payload;
    expect(batch).toEqual({
      mission: { domain: "doc-research-v1", goal: "比较文档", success_criteria: ["说明否定条件"], conflict_reserve_tokens: 30000, idempotency_key: expect.any(String) },
      sources: [{ path: "sources/a.md", content: "条件：不支持。\n| A | B |", kind: "markdown" }],
    });
    expect((screen.getByRole("button", { name: "提交任务" }) as HTMLButtonElement).disabled).toBe(true);
    channel.reply("mission_create_with_sources", {}, false, "invalid_request");
    expect((screen.getByLabelText("任务目标") as HTMLTextAreaElement).value).toBe("比较文档");
    expect(screen.getByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    expect(channel.last("mission_create_with_sources")?.payload).toEqual(batch);
  });

  it("unknown approval kind cannot inherit arbitration actions even with options", () => {
    openMission({ ...DETAIL, approvals: [{ request_id: "unknown", kind: "future_kind", state: "PENDING", options: ["keep:c1", "unresolved"] }] });
    const approval = screen.getByTestId("approval-unknown");
    expect(within(approval).queryAllByRole("button")).toHaveLength(0);
    expect(approval.textContent).toContain("future_kind");
  });

  it("source_change shows its exact old/new binding and uses existing approve/reject", () => {
    const channel = openMission({ ...DETAIL, approvals: [{
      request_id: "source-approval", kind: "source_change", state: "PENDING",
      source_change: { operation: "supersede", path: "sources/a.md", expected_version_hash: "old-hash", version_hash: "new-hash", old_revision: 3, kind: "markdown", reason: "replace" },
    }] });
    const approval = screen.getByTestId("approval-source-approval");
    expect(approval.textContent).toContain("old-hash");
    expect(approval.textContent).toContain("new-hash");
    expect(within(approval).queryByLabelText("仲裁依据")).toBeNull();
    fireEvent.click(within(approval).getByRole("button", { name: "批准" }));
    expect(channel.last("mission_approval_decide")?.payload).toEqual({ approval_id: "source-approval", decision: "approve" });
  });

  it("arbitration sends only actual keep/contextual/unresolved options with basis", () => {
    const channel = openMission({ ...DETAIL, approvals: [{ request_id: "arb", kind: "arbitration", state: "PENDING", options: ["keep:c1", "contextual", "unresolved", "approve"] }] });
    const approval = screen.getByTestId("approval-arb");
    expect(within(approval).queryByRole("button", { name: "裁决：approve" })).toBeNull();
    fireEvent.change(within(approval).getByLabelText("仲裁依据"), { target: { value: "条件不同" } });
    fireEvent.click(within(approval).getByRole("button", { name: "裁决：contextual" }));
    expect(channel.last("mission_approval_decide")?.payload).toEqual({ approval_id: "arb", decision: "arbitrate", ruling: "contextual", basis: "条件不同" });
  });
});

describe("MissionsView（HA-10）", () => {
  it("服务不可用时显示原因，不显示新建入口", () => {
    const channel = new FakeChannel();
    render(<Workbench channel={channel} />);
    channel.reply("orchestration_status", { ...AVAILABLE, available: false, state: "unavailable", reason: "编排库目录不可写" });
    expect(screen.getByText(/编排服务不可用/)).toBeTruthy();
    expect(screen.getByText(/编排库目录不可写/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "新建任务" })).toBeNull();
  });

  it("空态：没有 Mission 时提示新建", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [] });
    expect(screen.getByText(/还没有任务/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "新建任务" })).toBeTruthy();
  });

  it("新建表单：目标或成功条件为空时不能提交；提交发出 mission_create", () => {
    const channel = renderAvailable();
    channel.reply("mission_list", { missions: [] });
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    const submit = screen.getByRole("button", { name: "提交任务" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fireEvent.change(screen.getByRole("textbox", { name: "任务目标" }), {
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
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fireEvent.change(screen.getByRole("textbox", { name: "任务目标" }), { target: { value: "跑测试" } });
    fireEvent.change(screen.getByRole("textbox", { name: "成功条件" }), { target: { value: "pytest:tests" } });
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
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

  it.each([
    [{ settled_tokens: 86732, reserved_tokens: 0 }, /Token 已结算 86732 · 当前预留 0/],
    [{ settled_tokens: null, reserved_tokens: null }, /Token 已结算 未知 · 当前预留 未知/],
  ])("显示账本当前用量，未知不冒充零 %j", (usage, expected) => {
    openMission({
      ...DETAIL,
      usage: { ...usage, amount_micros: null },
    });
    expect(screen.getByText(expected)).toBeTruthy();
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

  it("人工复核把未通过理由原子提交；失败应答保留理由和可重试状态", () => {
    const channel = openMission({
      ...DETAIL,
      approvals: [{ request_id: "review-1", kind: "review", state: "PENDING", summary: "请确认结果" }],
    });
    const approval = screen.getByTestId("approval-review-1");
    const reject = within(approval).getByRole("button", { name: "复核不通过" }) as HTMLButtonElement;
    expect(reject.disabled).toBe(true);

    const reason = within(approval).getByRole("textbox", { name: "复核理由" }) as HTMLTextAreaElement;
    fireEvent.change(reason, { target: { value: "引用未覆盖第二项准则" } });
    fireEvent.click(reject);
    expect(channel.last("mission_approval_decide")?.payload).toEqual({
      approval_id: "review-1", decision: "review_fail", note: "引用未覆盖第二项准则",
    });
    expect(reject.disabled).toBe(true);

    channel.reply("mission_approval_decide", null, false, "invalid_request");
    expect(reason.value).toBe("引用未覆盖第二项准则");
    expect(reject.disabled).toBe(false);
  });

  it("人工复核的 transport 拒绝不留下 pending 状态或丢失理由", () => {
    const channel = openMission({
      ...DETAIL,
      approvals: [{ request_id: "review-transport", kind: "review", state: "PENDING", summary: "请确认结果" }],
    });
    const approval = screen.getByTestId("approval-review-transport");
    const reason = within(approval).getByRole("textbox", { name: "复核理由" }) as HTMLTextAreaElement;
    const reject = within(approval).getByRole("button", { name: "复核不通过" }) as HTMLButtonElement;
    fireEvent.change(reason, { target: { value: "结果未满足准则" } });
    const send = channel.send;
    channel.send = () => false;
    fireEvent.click(reject);
    channel.send = send;

    expect(channel.all("mission_approval_decide")).toHaveLength(0);
    expect(reason.value).toBe("结果未满足准则");
    expect(reject.disabled).toBe(false);
    expect(screen.getByRole("alert").textContent).toContain("复核请求未发送");
  });

  it("取消任务 先确认，确认后才发出 mission_cancel", async () => {
    const channel = openMission();
    fireEvent.click(screen.getByRole("button", { name: "取消任务" }));
    const dialog = await screen.findByRole("dialog");
    expect(channel.last("mission_cancel")).toBeUndefined();
    fireEvent.click(within(dialog).getByRole("button", { name: "取消任务" }));
    await waitFor(() => expect(channel.last("mission_cancel")?.payload).toMatchObject({ mission_id: "mission-1" }));
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
    expect(wait.textContent).toMatch(/等你处理/);
    expect(wait.getAttribute("data-ui-state")).toBe("waiting_person");
    expect(wait.getAttribute("data-status")).toBe("ACTIVE");
    expect(screen.getByTestId("mission-row-m-unknown").textContent).toMatch(/UNKNOWN（结果未知）/);
    expect(screen.getByTestId("mission-row-m-run").textContent).toMatch(/运行/);
    expect(screen.getByTestId("mission-row-m-legacy").textContent).toMatch(/运行/); // 缺 ui_state：原始 status 译成中文，不再显示英文代码
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

  it("P3.2 P32-15：审批卡如实说明动作在世界上的状态——已生成未发布 / 已发布 / 核对中", () => {
    const withAction = (action: Record<string, unknown>) => ({
      ...DETAIL,
      approvals: [{ ...DETAIL.approvals[0], action: { ...DETAIL.approvals[0].action, ...action } }],
    });

    openMission(withAction({ state: "AWAITING_APPROVAL", published_path: null, published_hash: null }));
    const pending = within(screen.getByTestId("approval-approval-1")).getByTestId("action-outcome");
    expect(pending.textContent).toMatch(/已生成，未发布/);
    expect(pending.getAttribute("data-action-state")).toBe("AWAITING_APPROVAL");
    cleanup();

    openMission(
      withAction({
        state: "SUCCEEDED",
        published_path: "/Users/me/reports/weekly.2f8a1c4d9e0b.v1.md",
        published_hash: "a".repeat(64),
      }),
    );
    const done = within(screen.getByTestId("approval-approval-1")).getByTestId("action-outcome");
    expect(done.textContent).toMatch(/已发布/);
    expect(done.textContent).toMatch(/weekly\.2f8a1c4d9e0b\.v1\.md/); // 实际落盘路径
    expect(done.textContent).toMatch(/内容 /); // 回读的内容哈希
    cleanup();

    // UNKNOWN 绝不能写成成功或失败：系统自己也还不知道
    openMission(withAction({ state: "UNKNOWN", published_path: null, published_hash: null }));
    const unknown = within(screen.getByTestId("approval-approval-1")).getByTestId("action-outcome");
    expect(unknown.textContent).toMatch(/核对中/);
    expect(unknown.textContent).not.toMatch(/已发布|失败/);
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

describe("Result final rejection", () => {
  it("shows final stale rejection above historical PASS without upgrading a reviewed Claim", () => {
    openMission({
      ...DETAIL,
      mission: { ...DETAIL.mission, status: "FAILED", stop_reason: "max_attempts_reached" },
      tasks: [{ id: "task-1", goal: "原任务", status: "FAILED" }],
      results: [{
        result_id: "rejected", attempt_id: "attempt-1", verdict: "FAIL", verification_state: "DONE",
        summary: { text: "原报告", source: "model" },
        final_rejection: { reason: "stale_source", source_issues: [{
          code: "stale_source", reason: "revoked", path: "sources/A.md", version: "old-version",
        }] },
        verification_layers: [
          { layer: "rule_check", status: "PASS" }, { layer: "human_review", status: "PASS" },
        ],
      }, {
        result_id: "other", attempt_id: "attempt-2", verdict: "FAIL", verification_state: "DONE",
        final_rejection: null, verification_layers: [],
      }],
      document: {
        claims: [{ id: "claim-1", status: "UNDER_REVIEW", content: "尚未被系统接受的主张", review_refs: ["review-r1"] }],
        reviews: [{ request_id: "review-r1", state: "GRANTED" }],
      },
      approvals: [{ request_id: "review-r1", kind: "review", state: "GRANTED" }],
    });
    const rejected = screen.getByTestId("result-final-rejected");
    expect(rejected.textContent).toContain("结果最终未接受（FAIL / DONE）");
    expect(rejected.textContent).toContain("来源已失效（stale_source）");
    expect(rejected.textContent).toContain("sources/A.md · 已撤销（revoked）");
    expect(rejected.textContent).toContain("old-version");
    expect(screen.getByTestId("layer-attempt-1-rule_check").getAttribute("data-status")).toBe("PASS");
    expect(screen.getByTestId("layer-attempt-1-human_review").getAttribute("data-status")).toBe("PASS");
    const other = screen.getByTestId("result-final-other");
    expect(other.textContent).toContain("FAIL / DONE");
    expect(other.textContent).not.toMatch(/stale_source|sources\/A.md|拒绝原因/);
    const claim = screen.getByTestId("document-claim-claim-1");
    expect(claim.textContent).toContain("待核验（UNDER_REVIEW）");
    expect(claim.textContent).not.toContain("已核验（VERIFIED）");
    expect(claim.textContent).not.toContain("有依据支持（SUPPORTED）");
    expect(screen.getByText(/停止原因：max_attempts_reached/)).toBeTruthy();
  });

  it.each([["PASS", "DONE"], ["FAIL", "PENDING"]])("does not render rejection for %s/%s", (verdict, verificationState) => {
    openMission({ ...DETAIL, results: [{
      result_id: "not-final-failure", attempt_id: "attempt-1", verdict, verification_state: verificationState,
      final_rejection: { reason: "stale_source", source_issues: [{ path: "sources/A.md" }] },
      verification_layers: [],
    }] });
    const status = screen.getByTestId("result-final-not-final-failure");
    expect(status.textContent).toContain(`${verdict} / ${verificationState}`);
    expect(status.textContent).not.toMatch(/结果最终未接受|拒绝原因|stale_source|sources\/A.md/);
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
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    expect(screen.getByLabelText("Token 上限").getAttribute("placeholder")).toBe("Token 上限（留空=400000）");
    expect(screen.getByLabelText("尝试次数上限").getAttribute("placeholder")).toBe("尝试次数上限（留空=12）");
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "写 NOTES.md" } });
    fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:NOTES.md" } });
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    const payload = channel.last("mission_create")?.payload as Record<string, unknown>;
    expect(payload.goal).toBe("写 NOTES.md");
    expect("budget" in payload).toBe(false); // the Host decides the defaults, the form never guesses
  });

  it("最终独立综合可使用 Host 下发的默认上限，而不补写 Mission budget", () => {
    const channel = new FakeChannel();
    render(<Workbench channel={channel} />);
    channel.reply("orchestration_status", { ...AVAILABLE, mission_budget_defaults: { max_tokens: 400000, max_attempts: 12 } });
    channel.reply("mission_list", { missions: [] });
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fillValidSynthesisForm({ missionBudget: false });
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    const payload = channel.last("mission_create")?.payload as Record<string, unknown>;
    expect("budget" in payload).toBe(false);
    expect(payload.synthesis).toEqual({
      goal: "独立汇总",
      success_criteria: ["file:FINAL.md"],
      budget: { max_tokens: 50, max_attempts: 1 },
    });
  });

  it("从原总预算中显式预留冲突核对额度", () => {
    const channel = renderAvailable();
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "核对两份资料" } });
    fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:REPORT.md" } });
    fireEvent.change(screen.getByLabelText("Token 上限"), { target: { value: "240000" } });
    fireEvent.change(screen.getByLabelText("冲突核对预留 Token"), { target: { value: "30000" } });
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    expect(channel.last("mission_create")?.payload).toMatchObject({
      budget: { max_tokens: 240000 }, conflict_reserve_tokens: 30000,
    });
  });

  it("成功创建后新表单不继承上一任务的冲突预留", () => {
    const channel = renderAvailable();
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "核对资料" } });
    fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:REPORT.md" } });
    fireEvent.change(screen.getByLabelText("冲突核对预留 Token"), { target: { value: "30000" } });
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    channel.reply("mission_create", { mission_id: "mission-new" });
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    expect((screen.getByLabelText("冲突核对预留 Token") as HTMLInputElement).value).toBe("");
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "写新报告" } });
    fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:NEW.md" } });
    fireEvent.click(screen.getByRole("button", { name: "提交任务" }));
    expect(channel.all("mission_create")).toHaveLength(2);
    expect(channel.last("mission_create")?.payload).not.toHaveProperty("conflict_reserve_tokens");
  });

  it.each(["-1", "1.5", "NaN", "240001"])("拒绝无效冲突预留 %s", (reserve) => {
    const channel = renderAvailable();
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "核对资料" } });
    fireEvent.change(screen.getByLabelText("成功条件"), { target: { value: "file:REPORT.md" } });
    fireEvent.change(screen.getByLabelText("Token 上限"), { target: { value: "240000" } });
    fireEvent.change(screen.getByLabelText("冲突核对预留 Token"), { target: { value: reserve } });
    expect((screen.getByRole("button", { name: "提交任务" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toContain("冲突核对预留");
    expect(channel.all("mission_create")).toHaveLength(0);
  });

  it("没有下发默认值时，占位符仍是「可选」", () => {
    renderAvailable();
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    expect(screen.getByLabelText("Token 上限").getAttribute("placeholder")).toBe("Token 上限（可选）");
  });

  it("详情显示实际生效的预算", () => {
    openMission({ ...DETAIL, mission: { ...DETAIL.mission, budget: { max_tokens: 400000, max_attempts: 12 } } });
    expect(screen.getByTestId("mission-budget").textContent).toBe("预算：Token 上限 400000 · 尝试次数上限 12");
  });
});

describe("兜底刷新（2026-09-25 真机点击：等授权时画面不刷新）", () => {
  it("打开的任务未结束时，每 5 秒重取一次详情；没有推送也能跟上", () => {
    vi.useFakeTimers();
    try {
      const channel = openMission();
      const gets = channel.all("mission_get").length;
      act(() => { vi.advanceTimersByTime(5001); });
      expect(channel.all("mission_get").length).toBe(gets + 1);
      expect(channel.last("mission_get")?.payload).toMatchObject({ mission_id: "mission-1" });
    } finally { vi.useRealTimers(); }
  });

  it("任务已结束就不再轮询", () => {
    vi.useFakeTimers();
    try {
      const channel = openMission({ ...DETAIL, mission: { ...(DETAIL as { mission: Record<string, unknown> }).mission, status: "COMPLETED" } });
      const gets = channel.all("mission_get").length;
      act(() => { vi.advanceTimersByTime(15001); });
      expect(channel.all("mission_get").length).toBe(gets);
    } finally { vi.useRealTimers(); }
  });
});

describe("下一步提示（2026-09-25 真机点击：要人操作的按钮埋在长页面中间）", () => {
  it("等授权时顶部直接提示去授权", () => {
    openMission({ ...DETAIL, planning_authorization_requests: [{ mission_id: "mission-1", request_id: "r1", intent_id: "i1", state: "AUTHORIZATION_REQUIRED" }] });
    const banner = screen.getByTestId("mission-next-step");
    expect(banner.textContent).toMatch(/授权本轮规划/);
    // 授权按钮就在提示条里，页面上只有这一个
    expect(within(banner).getByRole("button", { name: "授权本轮规划" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "授权本轮规划" })).toHaveLength(1);
  });

  it("完成要求可确认时优先提示去确认", () => {
    openMission({ ...DETAIL, operation_workspace: { mission_id: "mission-1", state: "PROPOSED", editable: true, criteria: [] },
      planning_authorization_requests: [{ request_id: "r1" }] });
    expect(screen.getByTestId("mission-next-step").textContent).toMatch(/确认上述完成要求/);
    expect(screen.getByRole("button", { name: "去确认" })).toBeTruthy();
  });

  it("任务完成后提示查看产物；运行中只说明会提示", () => {
    openMission({ ...DETAIL, approvals: [], mission: { ...(DETAIL as { mission: Record<string, unknown> }).mission, status: "COMPLETED" } });
    expect(screen.getByTestId("mission-next-step").textContent).toMatch(/任务已完成/);
    cleanup();
    openMission({ ...DETAIL, approvals: [] });
    expect(screen.getByTestId("mission-next-step").textContent).toMatch(/自动进行|等你审批/);
  });
});

describe("结束态与产物排序（2026-09-25 真机点击）", () => {
  it("任务结束后不再显示取消；停止原因与 Task 状态显示中文", () => {
    openMission({ ...DETAIL, approvals: [], mission: { ...(DETAIL as { mission: Record<string, unknown> }).mission, status: "COMPLETED", stop_reason: "verification_passed" },
      tasks: [{ id: "task-1", goal: "写 NOTES.md", status: "BLOCKED", dependencies: [] }] });
    expect(screen.queryByRole("button", { name: "取消任务" })).toBeNull();
    expect(screen.getByTestId("mission-state").textContent).toMatch(/停止原因：验证通过/);
    expect(document.body.textContent).toMatch(/等待前置步骤/);
  });

  it("交付物排在前面，系统检查记录默认收起、可展开", () => {
    openMission({ ...DETAIL, artifacts: [
      { id: "a-check", path: ".assurance/checks/x/0.json", size_bytes: 10, verification_status: "UNVERIFIED", content_hash: "h1" },
      { id: "a-notes", path: "NOTES.md", size_bytes: 20, verification_status: "PASS", content_hash: "h2" },
    ] });
    const rows = screen.getAllByTestId(/^artifact-/);
    expect(rows.map((row) => row.getAttribute("data-testid"))).toEqual(["artifact-a-notes", "artifact-a-check"]);
    expect(screen.getByTestId("artifact-a-check").hidden).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /显示系统检查记录（1 个/ }));
    expect(screen.getByTestId("artifact-a-check").hidden).toBe(false);
  });
});
