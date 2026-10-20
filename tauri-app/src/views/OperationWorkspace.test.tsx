import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ControlMessage, IncomingMessage } from "../types/messages";
import { OperationWorkspace } from "./OperationWorkspace";

/** 完成要求确认请求里本测试检查的字段。 */
interface ApprovePayload {
  [key: string]: unknown;
  expected_requirements_ref?: unknown;
  proposal: {
    mode?: string;
    content_criterion_ids?: string[];
    effects: Array<{
      criterion_ids?: string[];
      required_milestone?: string;
      obligation_id?: string;
    }>;
  };
}

class Channel {
  sent: ControlMessage[] = [];
  listeners = new Set<(message: IncomingMessage) => void>();
  send = (message: ControlMessage) => {
    this.sent.push(message);
    return true;
  };
  onMessage = (listener: (message: IncomingMessage) => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  reply(type: string, ok = true) {
    const request = [...this.sent].reverse().find((message) => message.type === type)!;
    act(() =>
      this.listeners.forEach((listener) =>
        listener({
          type: `${type}_response`,
          payload: { request_id: request.request_id, ok, error: ok ? null : "stale" },
        } as unknown as IncomingMessage),
      ),
    );
  }
}

const requirements = {
  kind: "requirements",
  id: "requirements-2",
  revision: 2,
  content_hash: "a".repeat(64),
};
const criteria = [
  { id: "criterion-created", statement: "生成报告", required: true },
  { id: "criterion-sent", statement: "action:mail.send:boss@example.com", required: true },
];
const milestone = {
  id: "APPLIED",
  label: "连接器回执确认已执行",
  milestone_policy_ref: { id: "milestone-policy", revision: 1, content_hash: "b".repeat(64) },
  evidence_policy_ref: { id: "evidence-policy", revision: 1, content_hash: "c".repeat(64) },
};

/** SDK 给每条 action: 要求的操作：它能用的完成标准与默认标准（2026-10-20 起按操作给）。 */
function operation(criterionId: string, extra: Record<string, unknown> = {}) {
  return {
    criterion_id: criterionId,
    connector: "mail",
    operation: "send",
    milestones: [milestone],
    default_milestone: milestone.id,
    description: "",
    always_needs_approval: false,
    ...extra,
  };
}

function confirmation() {
  return {
    mission_id: "mission-1",
    state: "CONFIRMATION_REQUIRED",
    editable: true,
    requirements_ref: requirements,
    criteria,
    obligations: [
      { id: "obligation-1", label: "deliver", criterion_ids: criteria.map((item) => item.id) },
    ],
    operations: [operation("criterion-sent")],
    spec: null,
    spec_hash: null,
    intents: [],
  };
}

function approved(intents: unknown[] = []) {
  return {
    ...confirmation(),
    state: "APPROVED",
    spec_hash: "d".repeat(64),
    intents,
    spec: {
      mode: "REQUIRED_EFFECTS",
      effects: [
        {
          effect_key: "publish-report",
          criterion_ids: criteria.map((item) => item.id),
          required_milestone: milestone.id,
        },
      ],
    },
  };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("OperationWorkspace", () => {
  it("maps multiple criteria to one effect and sends one CAS-bound approval", () => {
    const channel = new Channel();
    render(<OperationWorkspace value={confirmation()} channel={channel} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "全部取消" })); // 默认预填了内容要求，这里改成全放进效果
    // 操作要求默认已配好一个效果；把内容要求也勾进同一个效果
    const effect = screen.getByRole("group", { name: "这一次效果覆盖的要求（可多选）" });
    fireEvent.click(within(effect).getByText("生成报告"));
    // NEXT-TG-1.0 §9: only one choice each → already selected, no dropdown to operate
    const obligationGroup = screen.getByRole("radiogroup", { name: "效果所属目标" });
    const milestoneGroup = screen.getByRole("radiogroup", { name: "效果完成标准" });
    expect((within(obligationGroup).getByRole("radio") as HTMLInputElement).checked).toBe(true);
    expect((within(milestoneGroup).getByRole("radio") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "确认上述完成要求" }));

    const sent = channel.sent.filter(
      (message) => message.type === "mission_operation_completion_approve",
    );
    expect(sent).toHaveLength(1);
    // 2026-09-26 真机：回应要几秒，按钮只变灰看不出在处理，被当成"要点两次"。
    expect(screen.getByRole("button", { name: "正在确认…" })).toBeTruthy();
    const payload = sent[0].payload as ApprovePayload;
    expect(payload.expected_requirements_ref).toEqual(requirements);
    expect(payload.proposal.effects).toHaveLength(1);
    expect(payload.proposal.effects[0].criterion_ids).toEqual([
      "criterion-sent",
      "criterion-created",
    ]);
    expect(payload.proposal.effects[0].required_milestone).toBe("APPLIED");
  });

  it("retries an unchanged requirements body with the same command identity", () => {
    const channel = new Channel();
    render(<OperationWorkspace value={confirmation()} channel={channel} onChanged={vi.fn()} />);
    // 2026-09-29：普通要求默认已勾成内容交付，直接确认
    const confirm = screen.getByRole("button", { name: "确认上述完成要求" });
    fireEvent.click(confirm);
    channel.reply("mission_operation_completion_approve", false);
    fireEvent.click(confirm);
    const requests = channel.sent.filter(
      (message) => message.type === "mission_operation_completion_approve",
    );
    expect(requests).toHaveLength(2);
    expect(requests[0].request_id).not.toBe(requests[1].request_id);
    expect(requests[0].payload!.command_id).toBe(requests[1].payload!.command_id);
    expect(requests[0].payload!.proposal).toEqual(requests[1].payload!.proposal);
  });

  it("操作申请由系统准备：没有提交按钮，只显示进度", () => {
    const publishReadme = {
      id: "criterion-readme",
      statement: "action:file_publish.publish:README.md",
      required: true,
    };
    const base = approved();
    const value = {
      ...base,
      criteria: [publishReadme],
      operations: [
        operation(publishReadme.id, { connector: "file_publish", operation: "publish" }),
      ],
      spec: {
        ...base.spec,
        effects: [{ ...base.spec.effects[0], criterion_ids: [publishReadme.id] }],
      },
    };
    const channel = new Channel();
    const view = render(<OperationWorkspace value={value} channel={channel} onChanged={vi.fn()} />);
    expect(screen.getByText(/系统会自动准备对 README.md 的操作申请/)).toBeTruthy();
    expect(screen.queryByRole("radiogroup", { name: "选择已接受的操作准备产物" })).toBeNull();
    expect(screen.queryByRole("button", { name: /提交/ })).toBeNull();

    const intent = {
      intent_id: "intent-1",
      effect_key: "publish-report",
      spec_hash: "d".repeat(64),
      state: "AWAITING_APPROVAL",
      current: true,
      can_replace: false,
    };
    view.rerender(
      <OperationWorkspace
        value={{ ...value, intents: [intent] }}
        channel={channel}
        onChanged={vi.fn()}
      />,
    );
    expect(screen.getByText("进度：等你批准（请在批准卡片上点「批准」）")).toBeTruthy();
    view.rerender(
      <OperationWorkspace
        value={{ ...value, intents: [{ ...intent, current: false }] }}
        channel={channel}
        onChanged={vi.fn()}
      />,
    );
    expect(screen.getByText(/系统会自动准备/)).toBeTruthy(); // 被替代的旧申请不算当前进度
    expect(channel.sent).toEqual([]);
  });

  it("显示操作说明；标了总要人批的操作提示要人点批准", () => {
    const base = approved();
    const value = {
      ...base,
      operations: [
        operation("criterion-sent", {
          description: "给老板发周报邮件",
          always_needs_approval: true,
        }),
      ],
    };
    render(<OperationWorkspace value={value} channel={new Channel()} onChanged={vi.fn()} />);
    expect(screen.getByText("说明：给老板发周报邮件")).toBeTruthy();
    expect(screen.getByText(/总要人批准/)).toBeTruthy();
  });

  it("recovers a lost reply without changing the command and ignores a late response", () => {
    vi.useFakeTimers();
    const channel = new Channel();
    const changed = vi.fn();
    const view = render(
      <OperationWorkspace value={confirmation()} channel={channel} onChanged={vi.fn()} />,
    );
    // 2026-09-29：普通要求默认已勾成内容交付，直接确认
    const confirm = screen.getByRole("button", { name: "确认上述完成要求" });
    fireEvent.click(confirm);
    const first = channel.sent[0];
    view.rerender(
      <OperationWorkspace value={confirmation()} channel={channel} onChanged={changed} />,
    );
    expect((confirm.closest("fieldset") as HTMLFieldSetElement).disabled).toBe(true);
    act(() => vi.advanceTimersByTime(30000));
    expect(screen.getByRole("alert").textContent).toContain("结果尚未确认");
    fireEvent.click(confirm);
    expect(channel.sent).toHaveLength(2);
    expect(channel.sent[1].payload).toEqual(first.payload);
    expect(channel.sent[1].request_id).not.toBe(first.request_id);
    act(() =>
      channel.listeners.forEach((listener) =>
        listener({
          type: `${first.type}_response`,
          payload: { request_id: first.request_id, ok: true },
        } as unknown as IncomingMessage),
      ),
    );
    expect(changed).not.toHaveBeenCalled();
    expect((confirm.closest("fieldset") as HTMLFieldSetElement).disabled).toBe(true);
    channel.reply("mission_operation_completion_approve");
    expect(changed).toHaveBeenCalledOnce();
    expect((confirm.closest("fieldset") as HTMLFieldSetElement).disabled).toBe(false);
  });

  it("换了通道就解除处理中：按钮恢复可点，再点从新通道发出", () => {
    const oldChannel = new Channel();
    const view = render(
      <OperationWorkspace value={confirmation()} channel={oldChannel} onChanged={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "确认上述完成要求" }));
    const pendingButton = screen.getByRole("button", { name: "正在确认…" });
    expect((pendingButton.closest("fieldset") as HTMLFieldSetElement).disabled).toBe(true);

    // 旧通道上的请求不会再有回执。换一个通道对象重渲染。
    const newChannel = new Channel();
    view.rerender(
      <OperationWorkspace value={confirmation()} channel={newChannel} onChanged={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "正在确认…" })).toBeNull();
    const confirm = screen.getByRole("button", { name: "确认上述完成要求" });
    expect((confirm.closest("fieldset") as HTMLFieldSetElement).disabled).toBe(false);
    fireEvent.click(confirm);
    expect(
      newChannel.sent.filter((message) => message.type === "mission_operation_completion_approve"),
    ).toHaveLength(1);
  });
});

describe("默认预填（2026-09-29 真机：对话卡片里确认要点十几下）", () => {
  it("普通要求默认算内容交付；全部取消后可再全选", () => {
    const contentOnly = [
      { id: "criterion-created", statement: "生成报告", required: true },
      { id: "criterion-checked", statement: "核对报告", required: true },
    ];
    const value = { ...confirmation(), criteria: contentOnly, operations: [] };
    const channel = new Channel();
    render(<OperationWorkspace value={value} channel={channel} onChanged={vi.fn()} />);
    for (const box of screen.getAllByRole("checkbox"))
      expect((box as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "全部取消" }));
    for (const box of screen.getAllByRole("checkbox"))
      expect((box as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "全选" }));
    fireEvent.click(screen.getByRole("button", { name: "确认上述完成要求" }));
    const sent = channel.sent.filter(
      (message) => message.type === "mission_operation_completion_approve",
    );
    expect((sent[0].payload as ApprovePayload).proposal.content_criterion_ids).toEqual(
      contentOnly.map((item) => item.id),
    );
  });

  it("每条操作要求默认各配一个效果（目标唯一时自动选好、完成标准取操作的默认标准），一次点确认", () => {
    const withPublish = [
      { id: "c-1", statement: "写 README", required: true },
      { id: "c-2", statement: "file:README.md", required: true },
      { id: "c-3", statement: "action:file_publish.publish:README.md", required: true },
      { id: "c-4", statement: "action:file_publish.publish:wordfreq.py", required: true },
    ];
    const hash = { ...milestone, id: "CONTENT_HASH_VERIFIED", label: "内容哈希一致" };
    const publish = (id: string) =>
      operation(id, {
        connector: "file_publish",
        operation: "publish",
        milestones: [milestone, hash],
        default_milestone: hash.id,
      });
    const value = {
      ...confirmation(),
      criteria: withPublish,
      operations: [publish("c-3"), publish("c-4")],
    };
    const channel = new Channel();
    render(<OperationWorkspace value={value} channel={channel} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "确认上述完成要求" }));
    const proposal = (channel.sent[0].payload as ApprovePayload).proposal;
    expect(proposal.mode).toBe("REQUIRED_EFFECTS");
    expect(proposal.content_criterion_ids).toEqual(["c-1", "c-2"]);
    expect(proposal.effects.map((e) => e.criterion_ids)).toEqual([["c-3"], ["c-4"]]);
    expect(
      proposal.effects.every(
        (e) =>
          e.required_milestone === "CONTENT_HASH_VERIFIED" && e.obligation_id === "obligation-1",
      ),
    ).toBe(true);
  });
});
