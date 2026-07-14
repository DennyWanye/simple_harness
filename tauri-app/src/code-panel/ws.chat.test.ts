// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(async () => ""),
}));

import { useSessionsStore } from "../stores/sessionsStore";
import { __test_dispatch } from "./ws";

function resetStore() {
  useSessionsStore.setState((s) => ({
    ...s,
    active_sid: "default",
    sessions: {
      default: {
        ...s.sessions.default,
        messages: [],
        status: "idle" as const,
        inflight: false,
      },
    },
  }));
}

describe("ws.dispatch chat final dedupe", () => {
  beforeEach(resetStore);

  it("does not append a duplicate assistant bubble when chat_response already showed the final text", () => {
    __test_dispatch({
      type: "chat_v2_user_echo",
      payload: { session_id: "default", text: "please use a tool" },
    });
    __test_dispatch({
      type: "chat_response",
      payload: { session_id: "default", text: "same assistant answer" },
    });
    __test_dispatch({
      type: "tool_call",
      payload: {
        session_id: "default",
        name: "example_tool",
        arguments: { ok: true },
      },
    });

    __test_dispatch({
      type: "chat_v2_final",
      payload: { session_id: "default", text: "same assistant answer" },
    });

    const messages = useSessionsStore.getState().sessions.default.messages;
    expect(messages.filter((m) => m.role === "assistant")).toHaveLength(1);
    expect(messages.some((m) => m.role === "tool_call")).toBe(true);
    expect(messages[messages.length - 1]).toMatchObject({
      role: "assistant",
      text: "same assistant answer",
    });
  });

  it("activates and creates the backend-selected session after a session switch", () => {
    __test_dispatch({
      type: "session_switched",
      payload: {
        old_sid: "default",
        new_sid: "task-default-1",
        reason: "explicit_new",
      },
    });

    const store = useSessionsStore.getState();
    expect(store.active_sid).toBe("task-default-1");
    expect(store.sessions["task-default-1"]).toBeDefined();
    expect(store.sessions["task-default-1"].messages).toEqual([]);
  });

  it("deduplicates workflow finals without finishing an ordinary inflight turn", () => {
    useSessionsStore.getState().upsert("default", { status: "running", inflight: true });
    const event = {
      type: "workflow_final",
      payload: {
        session_id: "default",
        event_id: "workflow-event-1",
        event_type: "workflow.final_assistant",
        run_id: "run-1",
        seq: 4,
        payload: { payload: { text: "调研报告已完成" } },
      },
    };
    __test_dispatch(event);
    __test_dispatch(event);

    const session = useSessionsStore.getState().sessions.default;
    expect(session.messages.filter((message) => message.id === "workflow-event-1")).toHaveLength(1);
    expect(session.messages.at(-1)?.text).toBe("调研报告已完成");
    expect(session.status).toBe("running");
    expect(session.inflight).toBe(true);
  });

  it("hydrates workflow history into one card before live replay", () => {
    __test_dispatch({
      type: "session_messages_response",
      payload: {
        session_id: "default",
        messages: [
          {
            id: "workflow-progress-1",
            role: "assistant",
            text: "PPT进度：生成完整页面（8/12）",
            ts: 1000,
            workflow_event: {
              event_id: "workflow-progress-1",
              event_type: "workflow.progress",
              run_id: "run-1",
              seq: 8,
              created_at: 1,
              payload: {
                workflow_name: "PPT",
                stage: "生成完整页面",
                ordinal: 8,
                total: 12,
                status: "started",
              },
            },
          },
        ],
      },
    });

    __test_dispatch({
      type: "workflow_event",
      payload: {
        session_id: "default",
        event_id: "workflow-progress-1",
        event_type: "workflow.progress",
        run_id: "run-1",
        seq: 8,
        payload: {
          workflow_name: "PPT", stage: "生成完整页面", ordinal: 8, total: 12,
          status: "started", text: "PPT进度：生成完整页面（8/12）",
        },
      },
    });

    const messages = useSessionsStore.getState().sessions.default.messages;
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({
      id: "workflow-run:run-1",
      role: "workflow_progress",
      workflow_seq: 8,
    });
  });

  it("reduces accepted progress and final while keeping final assistant separate", () => {
    const dispatchWorkflow = (event_type: string, seq: number, payload: Record<string, unknown>) =>
      __test_dispatch({
        type: event_type === "workflow.final" ? "workflow_final" : "workflow_event",
        payload: { session_id: "default", event_id: `e-${seq}`, event_type, run_id: "run-x", seq, payload },
      });
    dispatchWorkflow("workflow.accepted", 1, { workflow_name: "PPT", status: "running" });
    dispatchWorkflow("workflow.progress", 2, {
      workflow_name: "PPT", stage: "等待确认大纲", ordinal: 6, total: 12, status: "waiting",
    });
    dispatchWorkflow("workflow.progress", 3, {
      workflow_name: "PPT", stage: "生成完整页面", ordinal: 8, total: 12, status: "started",
    });
    dispatchWorkflow("workflow.final_assistant", 4, { payload: { text: "PPT 做好了" } });
    dispatchWorkflow("workflow.final", 5, { workflow_name: "PPT", status: "completed" });

    const messages = useSessionsStore.getState().sessions.default.messages;
    expect(messages.filter((message) => message.role === "workflow_progress")).toHaveLength(1);
    expect(messages.find((message) => message.role === "workflow_progress")).toMatchObject({
      workflow_status: "completed",
      workflow_terminal: true,
      workflow_seq: 5,
    });
    expect(messages.filter((message) => message.role === "assistant")).toEqual([
      expect.objectContaining({ text: "PPT 做好了" }),
    ]);
  });

  it("keeps workflow-shaped legacy messages without run metadata as text", () => {
    __test_dispatch({
      type: "workflow_event",
      payload: {
        session_id: "default",
        event_id: "legacy-progress",
        event_type: "workflow.progress",
        payload: { text: "旧版进度文本" },
      },
    });
    expect(useSessionsStore.getState().sessions.default.messages).toEqual([
      expect.objectContaining({ id: "legacy-progress", role: "assistant", text: "旧版进度文本" }),
    ]);
  });

  it("restores async PPT artifact result and completion text from session history", () => {
    __test_dispatch({
      type: "session_messages_response",
      payload: {
        session_id: "task-default-5",
        messages: [
          {
            id: "192",
            role: "user",
            text: "帮我做一个有关俄乌战争局势的惊艳PPT",
            ts: 1000,
          },
          {
            id: "201",
            role: "tool",
            text: JSON.stringify({
              tool: "ppt_pro",
              ok: true,
              result: "PPT 已生成：deck.pptx",
              artifacts: [{ kind: "file", path: "deck.pptx" }],
            }),
            ts: 2000,
          },
          {
            id: "202",
            role: "assistant",
            text: "✨ PPT 做好啦，已自动打开：deck.pptx",
            ts: 3000,
          },
        ],
      },
    });

    const messages = useSessionsStore.getState().sessions["task-default-5"].messages;
    expect(messages).toHaveLength(3);
    expect(messages[1]).toMatchObject({
      role: "tool_result",
      tool_name: "ppt_pro",
      tool_result: expect.stringContaining("deck.pptx"),
    });
    expect(messages[2]).toMatchObject({
      role: "assistant",
      text: "✨ PPT 做好啦，已自动打开：deck.pptx",
    });
  });

  it("renders a live workflow artifact once even if delivery is retried", () => {
    const event = {
      type: "tool_result",
      payload: {
        session_id: "default",
        workflow_event_id: "artifact-event-live",
        tool: "artifact_create",
        ok: true,
        artifacts: [{ kind: "file", path: "report.md", title: "report.md" }],
      },
    };

    __test_dispatch(event);
    __test_dispatch(event);

    const artifacts = useSessionsStore.getState().sessions.default.messages.filter(
      (message) => message.id === "workflow-artifact:artifact-event-live",
    );
    expect(artifacts).toHaveLength(1);
    expect(artifacts[0]).toMatchObject({
      role: "tool_result",
      tool_name: "artifact_create",
      workflow_event_id: "artifact-event-live",
      tool_result: expect.stringContaining("report.md"),
    });
  });
});
