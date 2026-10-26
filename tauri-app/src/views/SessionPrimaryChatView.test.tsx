import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PrimaryChatView } from "./PrimaryChatView";
import type { ControlChannel } from "../ws/ControlChannel";
vi.mock("../code-panel/InputBar", () => ({
  InputBar: ({
    primary,
  }: {
    primary: { submit: (text: string, attachments: unknown[]) => Promise<void> };
  }) => (
    <>
      <button onClick={() => void primary.submit("hello", []).catch(() => {})}>发送测试消息</button>
      <button onClick={() => void primary.submit("different", []).catch(() => {})}>
        发送不同消息
      </button>
    </>
  ),
}));
vi.mock("../code-panel/controlWs", () => ({
  controlWS: { send: vi.fn(), on_message: () => () => {} },
}));
afterEach(cleanup);
function fixture(
  initialInbox: Record<string, unknown>[] = [],
  initialTasks: Record<string, unknown>[] = [],
) {
  const listeners = new Set<(message: unknown) => void>();
  const states = new Set<(state: string) => void>();
  const sent: Array<{ type: string; request_id: string; payload: Record<string, unknown> }> = [];
  const emit = (m: unknown) => act(() => listeners.forEach((fn) => fn(m)));
  const channel = {
    state: "connected",
    getLatestMessage: () => null,
    onMessage: (fn: (m: unknown) => void) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    onStateChange: (fn: (s: string) => void) => {
      states.add(fn);
      return () => states.delete(fn);
    },
    send: (m: (typeof sent)[number]) => {
      sent.push(m);
      if (m.type !== "session_send")
        queueMicrotask(() =>
          emit({
            type: `${m.type}_response`,
            payload: {
              request_id: m.request_id,
              ok: true,
              data:
                m.type === "session_history"
                  ? [{ turn_id: "old", input: "prior", text: "stored reply" }]
                  : m.type === "session_inbox"
                    ? initialInbox
                    : m.type === "session_tasks"
                      ? initialTasks
                      : [],
            },
          }),
        );
      return true;
    },
  } as unknown as ControlChannel;
  const event = (kind: string, payload: Record<string, unknown>, seq: number) =>
    emit({
      type: "session_event",
      payload: {
        schema_version: 1,
        session_id: "desktop-main",
        event_id: `e${seq}`,
        seq,
        kind,
        mission_id: "m",
        payload,
      },
    });
  return {
    channel,
    sent,
    emit,
    event,
    reconnect: () => act(() => states.forEach((fn) => fn("connected"))),
    disconnect: () => act(() => states.forEach((fn) => fn("disconnected"))),
  };
}
describe("desktop SDK Session", () => {
  it.each([
    ["通过", "pass"],
    ["打回", "fail"],
  ])("displays SDK key choices and submits %s as %s", async (label, key) => {
    const h = fixture(
      [],
      [
        {
          mission_id: "m",
          status: "ACTIVE",
          detail: {
            planning_questions: [{ decision_id: "choice", version: 1, state: "PENDING" }],
          },
        },
      ],
    );
    render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("stored reply");
    h.event(
      "needs_response",
      {
        request_id: "choice",
        version: "1",
        request_kind: "planning",
        question: "选择结论",
        options: [
          { key: "pass", label: "通过" },
          { key: "fail", label: "打回" },
        ],
      },
      1,
    );
    fireEvent.click(await screen.findByRole("button", { name: label }));
    expect(h.sent.find((message) => message.type === "session_respond")?.payload).toEqual({
      request_id: "choice",
      version: "1",
      decision: "answer",
      answer: key,
    });
  });
  it("does not accept legacy id choices", async () => {
    const h = fixture();
    render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("stored reply");
    h.event("needs_response", {
      request_id: "legacy-choice", version: "1", request_kind: "planning",
      options: [{ id: "pass", label: "通过" }],
    }, 1);
    expect(screen.queryByRole("button", { name: "通过" })).toBeNull();
  });
  it("a new generation replaces the prior text preview", async () => {
    const h = fixture();
    render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("stored reply");
    h.event("text_delta", { text: "old preview", generation: 1 }, 1);
    h.event("text_delta", { text: "new preview", generation: 2 }, 2);
    expect(screen.getByLabelText("回复预览").textContent).toBe("new preview");
  });
  it("a committed turn queues history recovery behind an in-flight task refresh", async () => {
    const h = fixture();
    render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("stored reply");
    h.event("task_progress", { type: "TaskStarted", seq: 10 }, 10);
    h.event("turn_committed", { turn_id: "other", state: "committed", text: "peer reply" }, 11);
    await waitFor(() =>
      expect(h.sent.filter((message) => message.type === "session_history")).toHaveLength(2),
    );
  });
  it("loads SDK history, sends text once and replaces preview with committed text", async () => {
    const h = fixture();
    render(<PrimaryChatView channel={h.channel} />);
    expect(await screen.findByText("stored reply")).toBeTruthy();
    fireEvent.click(screen.getByText("发送测试消息"));
    await waitFor(() => expect(h.sent.some((m) => m.type === "session_send")).toBe(true));
    const send = h.sent.find((m) => m.type === "session_send")!;
    expect(send.payload).toMatchObject({ text: "hello" });
    h.event("text_delta", { text: "preview" }, 1);
    expect(await screen.findByText("preview")).toBeTruthy();
    h.event("turn_committed", { turn_id: "new", state: "committed", text: "final reply" }, 2);
    h.emit({
      type: "session_send_response",
      payload: {
        request_id: send.request_id,
        ok: true,
        data: { turn_id: "new", state: "committed", text: "final reply" },
      },
    });
    expect(await screen.findByText("final reply")).toBeTruthy();
    expect(screen.queryByText("preview")).toBeNull();
    expect(h.sent.some((m) => m.type === "human_memory_request")).toBe(false);
  });
  it("reconnect uses the received cursor and does not resend text", async () => {
    const h = fixture();
    render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("stored reply");
    h.event("task_progress", { type: "TaskStarted", seq: 100 }, 7);
    h.reconnect();
    expect(h.sent.filter((m) => m.type === "session_events").at(-1)?.payload.after).toBe(7);
    expect(h.sent.filter((m) => m.type === "session_send")).toHaveLength(0);
  });
  it("an uncertain input retains its key and blocks a different draft until resolved", async () => {
    const h = fixture();
    render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("stored reply");
    fireEvent.click(screen.getByText("发送测试消息"));
    h.disconnect();
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("连接未就绪"));
    h.reconnect();
    fireEvent.click(screen.getByText("发送不同消息"));
    expect(h.sent.filter((m) => m.type === "session_send")).toHaveLength(1);
    fireEvent.click(screen.getByText("发送测试消息"));
    expect(h.sent.filter((m) => m.type === "session_send")).toHaveLength(2);
    const sends = h.sent.filter((m) => m.type === "session_send");
    expect(sends[0].payload.idempotency_key).toBe(sends[1].payload.idempotency_key);
  });
  it("respond preserves the exact version and ack requires a click", async () => {
    const h = fixture();
    render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("stored reply");
    h.event(
      "needs_response",
      {
        request_id: "human",
        version: "1152921504606846975",
        request_kind: "approval",
        summary: "publish now",
      },
      8,
    );
    fireEvent.click(await screen.findByRole("button", { name: "批准" }));
    expect(h.sent.find((m) => m.type === "session_respond")?.payload).toMatchObject({
      request_id: "human",
      version: "1152921504606846975",
      decision: "approve",
    });
    h.event(
      "result_ready",
      { event_id: "result", mission_id: "m", status: "COMPLETED", acked: false },
      9,
    );
    expect(h.sent.some((m) => m.type === "session_ack")).toBe(false);
    fireEvent.click(await screen.findByRole("button", { name: "已收到" }));
    expect(h.sent.find((m) => m.type === "session_ack")?.payload).toEqual({ event_id: "result" });
  });
  it("replaying result_ready never revives an already acknowledged inbox item", async () => {
    const h = fixture([
      { event_id: "received", mission_id: "m", status: "COMPLETED", acked: true },
    ]);
    render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("stored reply");
    h.event(
      "result_ready",
      { event_id: "received", mission_id: "m", status: "COMPLETED", acked: false },
      20,
    );
    expect(screen.queryByRole("button", { name: "已收到" })).toBeNull();
  });
  it("historical human requests on a settled task are not shown as pending approvals", async () => {
    const h = fixture(
      [],
      [{ mission_id: "m", delegation_id: "d", status: "COMPLETED", detail: {} }],
    );
    render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("stored reply");
    h.event(
      "needs_response",
      { request_id: "old", version: "1", request_kind: "approval", summary: "old question" },
      21,
    );
    expect(screen.queryByRole("button", { name: "批准" })).toBeNull();
  });
});

describe("implementation review: actual projected request identifiers", () => {
  it("keeps a pending approval visible when the task snapshot uses request_id", async () => {
    const h = fixture(
      [],
      [
        {
          mission_id: "m",
          delegation_id: "d",
          status: "ACTIVE",
          detail: { approvals: [{ request_id: "human", state: "PENDING" }] },
        },
      ],
    );
    render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("stored reply");
    h.event(
      "needs_response",
      {
        request_id: "human",
        version: "1152921504606846975",
        request_kind: "approval",
        summary: "publish now",
      },
      8,
    );
    expect(await screen.findByRole("button", { name: "批准" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "批准" }));
    expect(h.sent.find((message) => message.type === "session_respond")?.payload).toEqual({
      request_id: "human",
      version: "1152921504606846975",
      decision: "approve",
    });
  });
  it("keeps a pending planning question visible with its projected decision_id", async () => {
    const h = fixture(
      [],
      [
        {
          mission_id: "m",
          delegation_id: "d",
          status: "ACTIVE",
          detail: {
            planning_questions: [{ decision_id: "question", version: 3, state: "PENDING" }],
          },
        },
      ],
    );
    render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("stored reply");
    h.event(
      "needs_response",
      { request_id: "question", version: "3", request_kind: "planning", question: "Which format?" },
      8,
    );
    fireEvent.change(await screen.findByLabelText("回答或拒绝理由"), {
      target: { value: "Markdown" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "提交回答" }));
    expect(h.sent.find((m) => m.type === "session_respond")?.payload).toMatchObject({
      request_id: "question",
      version: "3",
      decision: "answer",
      answer: "Markdown",
    });
  });
});

describe("implementation review: terminal failed send", () => {
  it("uses a new input key when the same text is sent again after a terminal failure", async () => {
    const h = fixture();
    render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("stored reply");
    fireEvent.click(screen.getByText("发送测试消息"));
    const first = h.sent.find((message) => message.type === "session_send")!;
    h.emit({
      type: "session_send_response",
      payload: {
        request_id: first.request_id,
        ok: true,
        data: { turn_id: "failed", state: "failed", text: "", error: { code: "chat_timeout" } },
      },
    });
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("可以发送消息"));
    fireEvent.click(screen.getByText("发送测试消息"));
    const sends = h.sent.filter((message) => message.type === "session_send");
    expect(sends).toHaveLength(2);
    expect(sends[1].payload.idempotency_key).not.toBe(first.payload.idempotency_key);
  });
  it("allows a fresh draft after a durable failed receipt", async () => {
    const h = fixture();
    render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("stored reply");
    fireEvent.click(screen.getByText("发送测试消息"));
    const send = h.sent.find((m) => m.type === "session_send")!;
    h.emit({
      type: "session_send_response",
      payload: {
        request_id: send.request_id,
        ok: true,
        data: {
          turn_id: "failed-turn",
          state: "failed",
          text: "",
          error: { code: "agent_turn_cancelled" },
        },
      },
    });
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("可以发送消息"));
    fireEvent.click(screen.getByText("发送不同消息"));
    expect(h.sent.filter((m) => m.type === "session_send")).toHaveLength(2);
  });
});

describe("loaded task approval state", () => {
  it("submits a rejection with the projected request id, exact version and reason", async () => {
    const h = fixture(
      [],
      [
        {
          mission_id: "m",
          delegation_id: "d",
          status: "ACTIVE",
          detail: { approvals: [{ request_id: "human", state: "PENDING" }] },
        },
      ],
    );
    render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("stored reply");
    h.event(
      "needs_response",
      {
        request_id: "human",
        version: "1152921504606846975",
        request_kind: "approval",
        summary: "publish now",
      },
      8,
    );
    fireEvent.change(await screen.findByLabelText("回答或拒绝理由"), {
      target: { value: "needs correction" },
    });
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    expect(h.sent.find((message) => message.type === "session_respond")?.payload).toEqual({
      request_id: "human",
      version: "1152921504606846975",
      decision: "reject",
      reason: "needs correction",
    });
  });
  it.each(["APPROVED", "REJECTED", "EXPIRED"])(
    "does not show a %s approval as pending",
    async (state) => {
      const h = fixture(
        [],
        [
          {
            mission_id: "m",
            delegation_id: "d",
            status: "ACTIVE",
            detail: { approvals: [{ request_id: "human", state }] },
          },
        ],
      );
      render(<PrimaryChatView channel={h.channel} />);
      await screen.findByText("stored reply");
      h.event(
        "needs_response",
        { request_id: "human", version: "1", request_kind: "approval", summary: "old" },
        8,
      );
      expect(screen.queryByRole("button", { name: "批准" })).toBeNull();
    },
  );
});
