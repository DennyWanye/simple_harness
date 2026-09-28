import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ControlMessage, IncomingMessage } from "../types/messages";
import { OperationWorkspace } from "./OperationWorkspace";

class Channel {
  sent: ControlMessage[] = [];
  listeners = new Set<(message: IncomingMessage) => void>();
  send = (message: ControlMessage) => { this.sent.push(message); return true; };
  onMessage = (listener: (message: IncomingMessage) => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  reply(type: string, ok = true) {
    const request = [...this.sent].reverse().find(message => message.type === type)!;
    act(() => this.listeners.forEach(listener => listener({
      type: `${type}_response`, payload: { request_id: request.request_id, ok, error: ok ? null : "stale" },
    } as unknown as IncomingMessage)));
  }
}

const requirements = { kind: "requirements", id: "requirements-2", revision: 2, content_hash: "a".repeat(64) };
const criteria = [
  { id: "criterion-created", statement: "生成报告", required: true },
  { id: "criterion-sent", statement: "发送报告", required: true },
];
const milestone = {
  id: "FILE_PUBLISHED", label: "文件已发布，并已读取核对内容",
  milestone_policy_ref: { id: "milestone-policy", revision: 1, content_hash: "b".repeat(64) },
  evidence_policy_ref: { id: "evidence-policy", revision: 1, content_hash: "c".repeat(64) },
};

function confirmation() {
  return {
    mission_id: "mission-1", state: "CONFIRMATION_REQUIRED", editable: true,
    requirements_ref: requirements, criteria,
    obligations: [{ id: "obligation-1", label: "deliver", criterion_ids: criteria.map(item => item.id) }],
    milestones: [milestone], spec: null, spec_hash: null, candidates: [], intents: [],
  };
}

function approved(intents: unknown[] = []) {
  return {
    ...confirmation(), state: "APPROVED", spec_hash: "d".repeat(64), intents,
    spec: { mode: "REQUIRED_EFFECTS", effects: [{
      effect_key: "publish-report", criterion_ids: criteria.map(item => item.id),
      required_milestone: milestone.id,
    }] },
    candidates: [{
      artifact_path: "actions/publish.json",
      candidate: { operation: "file_publish", target: "REPORT.md", reason: "deliver", params: { path: "REPORT.md" } },
      candidate_artifact_ref: { kind: "artifact", id: "artifact-1", revision: 1, content_hash: "e".repeat(64) },
      prepared_acceptance_refs: [{ kind: "acceptance", id: "acceptance-1", revision: 1, content_hash: "f".repeat(64) }],
    }],
  };
}

afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("OperationWorkspace", () => {
  it("maps multiple criteria to one effect and sends one CAS-bound approval", () => {
    const channel = new Channel();
    render(<OperationWorkspace value={confirmation()} channel={channel} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "全部取消" }));  // 默认预填了内容要求，这里改成全放进效果
    fireEvent.click(screen.getByRole("button", { name: "添加必须完成的效果" }));
    const effect = screen.getByRole("group", { name: "这一次效果覆盖的要求（可多选）" });
    for (const item of criteria) fireEvent.click(within(effect).getByText(item.statement));
    // NEXT-TG-1.0 §9: only one choice each → already selected, no dropdown to operate
    const obligationGroup = screen.getByRole("radiogroup", { name: "效果所属目标" });
    const milestoneGroup = screen.getByRole("radiogroup", { name: "效果完成标准" });
    expect((within(obligationGroup).getByRole("radio") as HTMLInputElement).checked).toBe(true);
    expect((within(milestoneGroup).getByRole("radio") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "确认上述完成要求" }));

    const sent = channel.sent.filter(message => message.type === "mission_operation_completion_approve");
    expect(sent).toHaveLength(1);
    // 2026-09-26 真机：回应要几秒，按钮只变灰看不出在处理，被当成"要点两次"。
    expect(screen.getByRole("button", { name: "正在确认…" })).toBeTruthy();
    const payload = sent[0].payload as Record<string, any>;
    expect(payload.expected_requirements_ref).toEqual(requirements);
    expect(payload.proposal.effects).toHaveLength(1);
    expect(payload.proposal.effects[0].criterion_ids).toEqual(criteria.map(item => item.id));
  });

  it("retries an unchanged requirements body with the same command identity", () => {
    const channel = new Channel();
    render(<OperationWorkspace value={confirmation()} channel={channel} onChanged={vi.fn()} />);
    // 2026-09-29：普通要求默认已勾成内容交付，直接确认
    const confirm = screen.getByRole("button", { name: "确认上述完成要求" });
    fireEvent.click(confirm);
    channel.reply("mission_operation_completion_approve", false);
    fireEvent.click(confirm);
    const requests = channel.sent.filter(message => message.type === "mission_operation_completion_approve");
    expect(requests).toHaveLength(2);
    expect(requests[0].request_id).not.toBe(requests[1].request_id);
    expect(requests[0].payload!.command_id).toBe(requests[1].payload!.command_id);
    expect(requests[0].payload!.proposal).toEqual(requests[1].payload!.proposal);
  });

  it("发布申请由系统准备：不再挑候选、点提交，只显示进度（2026-09-29）", () => {
    const publishReadme = { id: "criterion-readme", statement: "action:file_publish.publish:README.md", required: true };
    const base = approved();
    const value = { ...base, criteria: [publishReadme],
      spec: { ...base.spec, effects: [{ ...base.spec.effects[0], criterion_ids: [publishReadme.id] }] } };
    const channel = new Channel();
    const view = render(<OperationWorkspace value={value} channel={channel} onChanged={vi.fn()} />);
    expect(screen.getByText(/系统会自动准备发布 README.md 的申请/)).toBeTruthy();
    expect(screen.queryByRole("radiogroup", { name: "选择已接受的操作准备产物" })).toBeNull();
    expect(screen.queryByRole("button", { name: /提交/ })).toBeNull();

    const intent = { intent_id: "intent-1", effect_key: "publish-report", spec_hash: "d".repeat(64),
      state: "AWAITING_APPROVAL", current: true, can_replace: false };
    view.rerender(<OperationWorkspace value={{ ...value, intents: [intent] }} channel={channel} onChanged={vi.fn()} />);
    expect(screen.getByText("进度：等你批准（请在批准卡片上点「批准」）")).toBeTruthy();
    view.rerender(<OperationWorkspace value={{ ...value, intents: [{ ...intent, current: false }] }}
      channel={channel} onChanged={vi.fn()} />);
    expect(screen.getByText(/系统会自动准备/)).toBeTruthy();  // 被替代的旧申请不算当前进度
    expect(channel.sent.filter(message => message.type === "mission_operation_intent_submit")).toEqual([]);
  });

  it("recovers a lost reply without changing the command and ignores a late response", () => {
    vi.useFakeTimers();
    const channel = new Channel();
    const changed = vi.fn();
    const view = render(<OperationWorkspace value={confirmation()} channel={channel} onChanged={vi.fn()} />);
    // 2026-09-29：普通要求默认已勾成内容交付，直接确认
    const confirm = screen.getByRole("button", { name: "确认上述完成要求" });
    fireEvent.click(confirm);
    const first = channel.sent[0];
    view.rerender(<OperationWorkspace value={confirmation()} channel={channel} onChanged={changed} />);
    expect((confirm.closest("fieldset") as HTMLFieldSetElement).disabled).toBe(true);
    act(() => vi.advanceTimersByTime(30000));
    expect(screen.getByRole("alert").textContent).toContain("结果尚未确认");
    fireEvent.click(confirm);
    expect(channel.sent).toHaveLength(2);
    expect(channel.sent[1].payload).toEqual(first.payload);
    expect(channel.sent[1].request_id).not.toBe(first.request_id);
    act(() => channel.listeners.forEach(listener => listener({
      type: `${first.type}_response`, payload: { request_id: first.request_id, ok: true },
    } as unknown as IncomingMessage)));
    expect(changed).not.toHaveBeenCalled();
    expect((confirm.closest("fieldset") as HTMLFieldSetElement).disabled).toBe(true);
    channel.reply("mission_operation_completion_approve");
    expect(changed).toHaveBeenCalledOnce();
    expect((confirm.closest("fieldset") as HTMLFieldSetElement).disabled).toBe(false);
  });
});

describe("默认预填（2026-09-29 真机：对话卡片里确认要点十几下）", () => {
  it("普通要求默认算内容交付；全部取消后可再全选", () => {
    const channel = new Channel();
    render(<OperationWorkspace value={confirmation()} channel={channel} onChanged={vi.fn()} />);
    for (const box of screen.getAllByRole("checkbox")) expect((box as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "全部取消" }));
    for (const box of screen.getAllByRole("checkbox")) expect((box as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "全选" }));
    fireEvent.click(screen.getByRole("button", { name: "确认上述完成要求" }));
    const sent = channel.sent.filter(message => message.type === "mission_operation_completion_approve");
    expect((sent[0].payload as Record<string, any>).proposal.content_criterion_ids).toEqual(criteria.map(item => item.id));
  });

  it("每条发布要求默认各配一个效果（目标唯一时自动选好、完成标准默认哈希一致），一次点确认", () => {
    const withPublish = [
      { id: "c-1", statement: "写 README", required: true },
      { id: "c-2", statement: "file:README.md", required: true },
      { id: "c-3", statement: "action:file_publish.publish:README.md", required: true },
      { id: "c-4", statement: "action:file_publish.publish:wordfreq.py", required: true },
    ];
    const hash = { ...milestone, id: "PUBLISHED_HASH_MATCHES", label: "已发布文件的内容哈希与批准产物一致" };
    const value = { ...confirmation(), criteria: withPublish, milestones: [milestone, hash] };
    const channel = new Channel();
    render(<OperationWorkspace value={value} channel={channel} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "确认上述完成要求" }));
    const proposal = (channel.sent[0].payload as Record<string, any>).proposal;
    expect(proposal.mode).toBe("REQUIRED_EFFECTS");
    expect(proposal.content_criterion_ids).toEqual(["c-1", "c-2"]);
    expect(proposal.effects.map((e: Record<string, any>) => e.criterion_ids)).toEqual([["c-3"], ["c-4"]]);
    expect(proposal.effects.every((e: Record<string, any>) =>
      e.required_milestone === "PUBLISHED_HASH_MATCHES" && e.obligation_id === "obligation-1")).toBe(true);
  });
});
