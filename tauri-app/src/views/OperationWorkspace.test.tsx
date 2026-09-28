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
    fireEvent.click(screen.getByLabelText("生成报告（必需）"));
    fireEvent.click(screen.getByLabelText("发送报告（必需）"));
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

  it("sends an explicit successor and hides replacement once materialized", () => {
    const channel = new Channel();
    const predecessor = { intent_id: "intent-1", effect_key: "publish-report", spec_hash: "d".repeat(64),
      state: "PROPOSED", current: true, can_replace: true };
    const view = render(<OperationWorkspace value={approved([predecessor])} channel={channel} onChanged={vi.fn()} />);
    const choices = screen.getByRole("radiogroup", { name: "选择已接受的操作准备产物" });
    expect(choices.textContent).toContain("候选 1：file_publish → REPORT.md（deliver）");
    fireEvent.click(within(choices).getByRole("radio"));
    fireEvent.click(screen.getByRole("button", { name: "提交修订版并替代原请求" }));
    const request = channel.sent.find(message => message.type === "mission_operation_intent_submit")!;
    expect(request.payload!.supersedes_intent_id).toBe("intent-1");

    view.rerender(<OperationWorkspace value={approved([{ ...predecessor, state: "MATERIALIZED", can_replace: false }])}
      channel={channel} onChanged={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "提交修订版并替代原请求" })).toBeNull();
    expect(screen.getByText(/不能重复提交/)).toBeTruthy();
  });

  it("要求写明目标文件时只列目标对得上的候选", () => {
    const publishReadme = { id: "criterion-readme", statement: "action:file_publish.publish:README.md", required: true };
    const other = { artifact_path: "actions/b.json",
      candidate: { operation: "publish", target: "wordfreq.py", reason: "code", params: { path: "wordfreq.py" } },
      candidate_artifact_ref: { kind: "artifact", id: "artifact-2", revision: 1, content_hash: "1".repeat(64) },
      prepared_acceptance_refs: [] };
    const readme = { ...other, candidate: { ...other.candidate, target: "docs/README.md", reason: "doc" },
      candidate_artifact_ref: { ...other.candidate_artifact_ref, id: "artifact-3" } };
    const base = approved();
    const value = { ...base, criteria: [publishReadme], candidates: [other, readme],
      spec: { ...base.spec, effects: [{ ...base.spec.effects[0], criterion_ids: [publishReadme.id] }] } };
    const view = render(<OperationWorkspace value={value} channel={new Channel()} onChanged={vi.fn()} />);
    const choices = screen.getByRole("radiogroup", { name: "选择已接受的操作准备产物" });
    expect(within(choices).getAllByRole("radio")).toHaveLength(1);
    expect(choices.textContent).toContain("publish → docs/README.md");
    expect(choices.textContent).not.toContain("wordfreq.py");
    view.rerender(<OperationWorkspace value={{ ...value, candidates: [other] }} channel={new Channel()} onChanged={vi.fn()} />);
    expect(screen.getByText("还没有审核通过的 README.md 操作准备产物。")).toBeTruthy();
  });

  it("recovers a lost reply without changing the command and ignores a late response", () => {
    vi.useFakeTimers();
    const channel = new Channel();
    const changed = vi.fn();
    const view = render(<OperationWorkspace value={confirmation()} channel={channel} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByLabelText("生成报告（必需）"));
    fireEvent.click(screen.getByLabelText("发送报告（必需）"));
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

describe("全选（2026-09-25 真机点击：8 条要求要逐个勾）", () => {
  it("一键勾选全部内容类要求并确认；再点全部取消", () => {
    const channel = new Channel();
    render(<OperationWorkspace value={confirmation()} channel={channel} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "全选" }));
    for (const box of screen.getAllByRole("checkbox")) expect((box as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "确认上述完成要求" }));
    const sent = channel.sent.filter(message => message.type === "mission_operation_completion_approve");
    expect((sent[0].payload as Record<string, any>).proposal.content_criterion_ids).toEqual(criteria.map(item => item.id));
    fireEvent.click(screen.getByRole("button", { name: "全部取消" }));
    for (const box of screen.getAllByRole("checkbox")) expect((box as HTMLInputElement).checked).toBe(false);
  });
});
