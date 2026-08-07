// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(async () => ""),
}));

import { useSessionsStore } from "../stores/sessionsStore";
import {
  __test_dispatch,
  __test_reset_companion_identity_status,
  controlWS,
} from "./controlWs";

function resetStore() {
  __test_reset_companion_identity_status();
  useSessionsStore.setState((s) => ({
    ...s,
    active_sid: "default",
    sessions: {
      default: {
        ...s.sessions.default,
        messages: [],
        status: "idle" as const,
        inflight: false,
        active_run_id: null,
        selected_run_id: null,
        run_projections: {},
        companion_events: [],
        companion_detail_cache: {},
      },
    },
    companion_owner: null,
    companion_provisional_streams: {},
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

  it("keeps live tool traces attached to their durable Run", () => {
    __test_dispatch({
      type: "tool_call",
      payload: {
        session_id: "default",
        run_id: "run-tool",
        task_scope_id: "scope-tool",
        request_id: "request-tool",
        invocation_id: "invocation-tool",
        stream_epoch: "epoch-tool",
        name: "shell_command",
        arguments: { command: "Get-Location" },
      },
    });
    __test_dispatch({
      type: "tool_result",
      payload: {
        session_id: "default",
        run_id: "run-tool",
        task_scope_id: "scope-tool",
        request_id: "request-tool",
        invocation_id: "invocation-tool",
        stream_epoch: "epoch-tool",
        tool: "shell_command",
        ok: true,
        result: "F:\\projects\\deskpet",
      },
    });

    const [call, result] =
      useSessionsStore.getState().sessions.default.messages;
    expect(call).toMatchObject({
      role: "tool_call",
      run_id: "run-tool",
      task_scope_id: "scope-tool",
      request_id: "request-tool",
      invocation_id: "invocation-tool",
      stream_epoch: "epoch-tool",
    });
    expect(result).toMatchObject({
      role: "tool_result",
      run_id: "run-tool",
      task_scope_id: "scope-tool",
      request_id: "request-tool",
      invocation_id: "invocation-tool",
      stream_epoch: "epoch-tool",
    });
  });

  it("selects a new Run when the previously selected Run is terminal", () => {
    useSessionsStore.getState().upsert_run_projection("default", "run-old", {
      task_scope_id: "scope-old",
      status: "completed",
      inflight: false,
      ui_state: "open",
    });
    useSessionsStore.getState().select_run_projection("default", "run-old");

    __test_dispatch({
      type: "chat_v2_run_started",
      payload: {
        session_id: "default",
        run_id: "run-new",
        task_scope_id: "scope-new",
        request_id: "request-new",
      },
    });

    const session = useSessionsStore.getState().sessions.default;
    expect(session.selected_run_id).toBe("run-new");
    expect(session.run_projections["run-new"].ui_state).toBe("open");
  });

  it("keeps a concurrently running selected Run while a background Run starts", () => {
    useSessionsStore.getState().upsert_run_projection("default", "run-active", {
      task_scope_id: "scope-active",
      status: "running",
      inflight: true,
      ui_state: "open",
    });
    useSessionsStore.getState().select_run_projection("default", "run-active");

    __test_dispatch({
      type: "chat_v2_run_started",
      payload: {
        session_id: "default",
        run_id: "run-background",
        task_scope_id: "scope-background",
        request_id: "request-background",
      },
    });

    const session = useSessionsStore.getState().sessions.default;
    expect(session.selected_run_id).toBe("run-active");
    expect(session.run_projections["run-background"].ui_state).toBe("background");
  });

  it("selects and binds a locally originated Run as soon as it is reserved", () => {
    useSessionsStore.getState().upsert_run_projection("default", "run-old", {
      task_scope_id: "scope-old",
      status: "running",
      inflight: true,
      ui_state: "open",
    });
    useSessionsStore.getState().select_run_projection("default", "run-old");
    useSessionsStore.getState().push_message("default", {
      role: "user",
      text: "帮我打开 Godot",
      request_id: "request-new",
      turn_id: "turn-new",
    });
    useSessionsStore.getState().upsert("default", {
      pending_root_request_id: "request-new",
      pending_root_turn_id: "turn-new",
    });

    __test_dispatch({
      type: "chat_v2_run_reserved",
      payload: {
        session_id: "default",
        run_id: "run-new",
        task_scope_id: "scope-new",
        request_id: "request-new",
        turn_id: "turn-new",
      },
    });

    const session = useSessionsStore.getState().sessions.default;
    expect(session.selected_run_id).toBe("run-new");
    expect(session.run_projections["run-new"]).toMatchObject({
      status: "starting",
      ui_state: "open",
    });
    expect(session.messages.at(-1)).toMatchObject({
      run_id: "run-new",
      task_scope_id: "scope-new",
    });
    expect(session.pending_root_request_id).toBeUndefined();
  });

  it("keeps durable reasoning summaries when the final response arrives", () => {
    __test_dispatch({
      type: "chat_response",
      payload: {
        session_id: "default",
        run_id: "run-reasoning",
        text: "准备检查项目并启动编辑器。",
      },
    });
    __test_dispatch({
      type: "chat_v2_reasoning_summary",
      payload: {
        session_id: "default",
        run_id: "run-reasoning",
        task_scope_id: "scope-reasoning",
        summary_id: "reasoning-summary:run-reasoning:1:planning",
        text: "准备检查项目并启动编辑器。",
        phase: "planning",
        status: "running",
      },
    });
    __test_dispatch({
      type: "chat_v2_final",
      payload: {
        session_id: "default",
        run_id: "run-reasoning",
        task_scope_id: "scope-reasoning",
        text: "Godot 已打开。",
      },
    });

    const messages = useSessionsStore.getState().sessions.default.messages;
    expect(messages).toEqual(expect.arrayContaining([
      expect.objectContaining({
        role: "reasoning_summary",
        text: "准备检查项目并启动编辑器。",
      }),
      expect.objectContaining({
        role: "assistant",
        text: "Godot 已打开。",
      }),
    ]));
    expect(
      messages.filter(
        (message) => message.text === "准备检查项目并启动编辑器。",
      ),
    ).toHaveLength(1);
    expect(messages.some((message) => message.role === "assistant_delta")).toBe(false);
  });

  it("tracks a queued continuation until the Driver binds it", () => {
    useSessionsStore.getState().push_message("default", {
      role: "user",
      text: "补充约束",
      request_id: "request-steer",
      run_id: "run-active",
      task_scope_id: "scope-active",
      continuation_status: "waiting",
    });

    __test_dispatch({
      type: "chat_v2_continuation_accepted",
      payload: {
        session_id: "default",
        request_id: "request-steer",
        run_id: "run-active",
        task_scope_id: "scope-active",
        conversation_boundary_ref: "boundary-active",
        conversation_boundary_version: 2,
        queued: true,
      },
    });

    expect(
      useSessionsStore.getState().sessions.default.messages.at(-1)
        ?.continuation_status,
    ).toBe("waiting");

    __test_dispatch({
      type: "chat_v2_continuation_status",
      payload: {
        session_id: "default",
        request_id: "request-steer",
        run_id: "run-active",
        task_scope_id: "scope-active",
        status: "bound",
      },
    });

    expect(
      useSessionsStore.getState().sessions.default.messages.at(-1)
        ?.continuation_status,
    ).toBe("bound");
  });

  it("marks an immediately bound continuation as read on acceptance", () => {
    useSessionsStore.getState().push_message("default", {
      role: "user",
      text: "继续",
      request_id: "request-bound",
      continuation_status: "waiting",
    });

    __test_dispatch({
      type: "chat_v2_continuation_accepted",
      payload: {
        session_id: "default",
        request_id: "request-bound",
        run_id: "run-active",
        task_scope_id: "scope-active",
        queued: false,
      },
    });

    expect(
      useSessionsStore.getState().sessions.default.messages.at(-1)
        ?.continuation_status,
    ).toBe("bound");
  });

  it("marks a rejected continuation without falsely failing the target Run", () => {
    useSessionsStore.getState().upsert_run_projection(
      "default",
      "run-active",
      {
        task_scope_id: "scope-active",
        status: "running",
        inflight: true,
        ui_state: "open",
      },
    );
    useSessionsStore.getState().push_message("default", {
      role: "user",
      text: "过期边界上的补充",
      request_id: "request-rejected",
      run_id: "run-active",
      continuation_status: "waiting",
    });

    __test_dispatch({
      type: "chat_v2_error",
      payload: {
        session_id: "default",
        request_id: "request-rejected",
        run_id: "run-active",
        task_scope_id: "scope-active",
        error: "continuation boundary is stale",
      },
    });

    const session = useSessionsStore.getState().sessions.default;
    expect(session.messages.find(
      (message) => message.request_id === "request-rejected",
    )?.continuation_status).toBe("failed");
    expect(session.run_projections["run-active"].status).toBe("running");
  });

  // r4 S18 回归：Run 被预约之前就夭折的 root turn（companion_identity_not_ready、
  // 只读会话等）不会产生 run_id，原实现只在 chat_v2_run_reserved/started 里清
  // pending_root_request_id → 该字段永远挂着 → InputBar 的 shouldDefer 恒真 →
  // 此后每条消息只 push 到本地流、根本不 send，永久停在「等待 Agent 读取…」。
  it("root turn 在 Run 预约前失败时解除会话挂起（不留永久「等待 Agent 读取」）", () => {
    useSessionsStore.getState().upsert("default", {
      pending_root_request_id: "request-root",
      pending_root_turn_id: "turn-root",
    });
    useSessionsStore.getState().push_message("default", {
      role: "user",
      text: "身份未就绪时发出的第一条",
      request_id: "request-root",
      continuation_status: "waiting",
      deferred_send: true,
    });

    __test_dispatch({
      type: "chat_v2_error",
      payload: {
        session_id: "default",
        request_id: "request-root",
        error: "companion_identity_not_ready",
        code: "companion_identity_not_ready",
      },
    });

    const session = useSessionsStore.getState().sessions.default;
    expect(session.pending_root_request_id).toBeUndefined();
    expect(session.pending_root_turn_id).toBeUndefined();
    const stuck = session.messages.find(
      (message) => message.request_id === "request-root",
    );
    expect(stuck?.continuation_status).toBe("failed");
    expect(stuck?.deferred_send).toBeFalsy();
  });

  // 后端老版本/未覆盖的拒绝分支可能不回传 request_id —— 仍必须按会话级兜底解挂，
  // 否则同样卡死。
  it("chat_v2_error 缺 request_id 时按会话兜底解除挂起", () => {
    useSessionsStore.getState().upsert("default", {
      pending_root_request_id: "request-root-2",
      pending_root_turn_id: "turn-root-2",
    });

    __test_dispatch({
      type: "chat_v2_error",
      payload: { session_id: "default", error: "companion_session_read_only" },
    });

    expect(
      useSessionsStore.getState().sessions.default.pending_root_request_id,
    ).toBeUndefined();
  });

  it("activates and creates the backend-selected session after a session switch", () => {
    __test_dispatch({
      type: "session_switched",
      payload: {
        old_sid: "default",
        new_sid: "task-default-1",
        reason: "explicit_new",
        provider_id: null,
        preferred_model: "kimi-k3",
        model_params: { thinking: false, fast: true },
      },
    });

    const store = useSessionsStore.getState();
    expect(store.active_sid).toBe("task-default-1");
    expect(store.sessions["task-default-1"]).toBeDefined();
    expect(store.sessions["task-default-1"].messages).toEqual([]);
    expect(store.sessions["task-default-1"].preferred_model).toBe("kimi-k3");
    expect(store.sessions["task-default-1"].model_params).toEqual({
      thinking: false,
      fast: true,
    });
  });

  it("restores a persisted model binding when a historical session hydrates", () => {
    useSessionsStore.getState().ensure("session-history");

    __test_dispatch({
      type: "session_provider_binding",
      payload: {
        session_id: "session-history",
        provider_id: null,
        preferred_model: "kimi-k3",
        model_params: { thinking: false, fast: false },
      },
    });

    const restored = useSessionsStore.getState().sessions["session-history"];
    expect(restored.preferred_model).toBe("kimi-k3");
    expect(restored.model_params).toEqual({ thinking: false, fast: false });
  });

  it("switches to the owner-fenced inbox from identity status", () => {
    __test_dispatch({
      type: "companion_identity_status",
      payload: {
        ready: true,
        profile_id: "profile-a",
        profile_generation: 3,
        session_id: "companion-inbox",
      },
    });

    expect(useSessionsStore.getState().companion_owner).toEqual({
      profile_id: "profile-a",
      profile_generation: 3,
    });
    expect(useSessionsStore.getState().active_sid).toBe("companion-inbox");
  });

  it("replays the latest companion identity status to a remounted panel", async () => {
    const identityStatus = {
      type: "companion_identity_status",
      payload: {
        ready: true,
        profile_id: "profile-a",
        profile_generation: 3,
        session_id: "companion-inbox",
      },
    };
    __test_dispatch(identityStatus);

    const listener = vi.fn();
    const unsubscribe = controlWS.on_message(listener);
    await Promise.resolve();

    expect(listener).toHaveBeenCalledOnce();
    expect(listener).toHaveBeenCalledWith(identityStatus);
    unsubscribe();
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

  it("finishes only the workflow projection identified as the task root", () => {
    useSessionsStore.getState().upsert_run_projection(
      "default",
      "run-active",
      {
        task_scope_id: "scope-active",
        version: 0,
        status: "running",
        inflight: true,
        ui_state: "open",
      },
    );
    useSessionsStore.getState().upsert_run_projection(
      "default",
      "run-other",
      {
        task_scope_id: "scope-other",
        version: 0,
        status: "running",
        inflight: true,
        ui_state: "background",
      },
    );

    __test_dispatch({
      type: "workflow_final",
      payload: {
        session_id: "default",
        event_id: "workflow-terminal-active",
        event_type: "workflow.final",
        run_id: "child-workflow",
        root_run_id: "run-active",
        seq: 5,
        payload: { workflow_name: "深度调研", status: "completed" },
      },
    });

    const session = useSessionsStore.getState().sessions.default;
    expect(session.inflight).toBe(true);
    expect(session.run_projections["run-active"].status).toBe("completed");
    expect(session.run_projections["run-other"].status).toBe("running");
  });

  it("keeps three interleaved run streams isolated when one final arrives", () => {
    for (let index = 1; index <= 3; index += 1) {
      __test_dispatch({
        type: "chat_v2_run_started",
        payload: {
          session_id: "default",
          run_id: `root-${index}`,
          task_scope_id: `scope-${index}`,
          request_id: `request-${index}`,
          conversation_boundary_ref: `boundary-${index}`,
          conversation_boundary_version: 1,
          projection_version: 0,
        },
      });
      __test_dispatch({
        type: "chat_v2_delta",
        payload: {
          session_id: "default",
          run_id: `root-${index}`,
          task_scope_id: `scope-${index}`,
          content: `partial-${index}`,
        },
      });
    }

    __test_dispatch({
      type: "chat_v2_final",
      payload: {
        session_id: "default",
        run_id: "root-2",
        task_scope_id: "scope-2",
        text: "done-2",
      },
    });

    const session = useSessionsStore.getState().sessions.default;
    expect(session.inflight).toBe(true);
    expect(session.run_projections["root-1"].inflight).toBe(true);
    expect(session.run_projections["root-2"].status).toBe("completed");
    expect(session.run_projections["root-3"].inflight).toBe(true);
    expect(
      session.messages.find(
        (message) =>
          message.role === "assistant_delta" &&
          message.run_id === "root-1",
      )?.text,
    ).toBe("partial-1");
    expect(
      session.messages.some(
        (message) =>
          message.role === "assistant_delta" &&
          message.run_id === "root-2",
      ),
    ).toBe(false);
    expect(session.messages.at(-1)).toMatchObject({
      role: "assistant",
      run_id: "root-2",
      text: "done-2",
    });
  });

  it("retracts only the matching provisional provider stream", () => {
    for (const invocationId of ["invocation-a", "invocation-b"]) {
      __test_dispatch({
        type: "chat_v2_delta",
        payload: {
          session_id: "default",
          run_id: "root-1",
          invocation_id: invocationId,
          stream_epoch: "epoch-1",
          provisional: true,
          content: invocationId,
        },
      });
    }

    __test_dispatch({
      type: "chat_v2_delta",
      payload: {
        session_id: "default",
        run_id: "root-1",
        invocation_id: "invocation-a",
        stream_epoch: "epoch-1",
        provisional: true,
        retract_provisional: true,
      },
    });

    const store = useSessionsStore.getState();
    expect(store.sessions.default.messages).toEqual([]);
    expect(Object.entries(store.companion_provisional_streams)).toEqual([
      ["root-1\u001finvocation-b\u001fepoch-1", "invocation-b"],
    ]);
  });

  it("drops malformed provisional deltas instead of persisting them as Message rows", () => {
    __test_dispatch({
      type: "chat_v2_delta",
      payload: {
        session_id: "default",
        run_id: "root-1",
        provisional: true,
        content: "must-not-persist",
      },
    });

    const store = useSessionsStore.getState();
    expect(store.sessions.default.messages).toEqual([]);
    expect(store.companion_provisional_streams).toEqual({});
  });

  it("switches the message page to the owner-fenced inbox on profile bind", () => {
    __test_dispatch({
      type: "companion_profile_bound",
      payload: {
        profile_id: "profile-a",
        profile_generation: 4,
        session_id: "owner-inbox",
      },
    });

    const store = useSessionsStore.getState();
    expect(store.active_sid).toBe("owner-inbox");
    expect(store.sessions["owner-inbox"]).toBeDefined();
    expect(store.companion_owner).toEqual({
      profile_id: "profile-a",
      profile_generation: 4,
    });
  });

  it("hydrates live/retracted Companion events only for the bound owner", () => {
    __test_dispatch({
      type: "companion_profile_bound",
      payload: { profile_id: "profile-a", profile_generation: 4 },
    });
    const live = {
      event_id: "companion-event-1",
      profile_id: "profile-a",
      profile_generation: 4,
      session_id: "default",
      seq: 1,
      notification: {
        notification_id: "notice-1",
        kind: "growth_notice",
        summary: "原始摘要",
        detail_ref: "detail-1",
        detail_version: "v1",
        available_actions: ["forget"],
      },
    };
    __test_dispatch({ type: "companion_event", payload: live });
    __test_dispatch({
      type: "companion_event",
      payload: { ...live, event_id: "old-owner", profile_generation: 3 },
    });
    __test_dispatch({
      type: "companion_projection_retracted",
      payload: {
        event_id: "companion-event-1",
        notification_id: "notice-1",
        profile_id: "profile-a",
        profile_generation: 4,
        session_id: "default",
        seq: 1,
        redaction_version: 1,
      },
    });

    const events =
      useSessionsStore.getState().sessions.default.companion_events ?? [];
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      event_id: "companion-event-1",
      tombstone: true,
      notification: { available_actions: [], detail_ref: "" },
    });
  });

  it("restores durable lifecycle status instead of treating terminal runs as waiting", () => {
    __test_dispatch({
      type: "task_projections_response",
      payload: {
        session_id: "default",
        projections: [
          {
            projection_id: "projection-running",
            run_id: "root-running",
            task_scope_id: "scope-running",
            ui_state: "open",
            version: 2,
            status: "running",
            started_at: 10,
            updated_at: 12,
          },
          {
            projection_id: "projection-done",
            run_id: "root-done",
            task_scope_id: "scope-done",
            ui_state: "background",
            version: 4,
            status: "completed",
            started_at: 5,
            updated_at: 9,
            ended_at: 9,
          },
        ],
      },
    });

    const projections =
      useSessionsStore.getState().sessions.default.run_projections;
    expect(projections["root-running"]).toMatchObject({
      status: "running",
      inflight: true,
      started_at: 10_000,
      last_activity: 12_000,
    });
    expect(projections["root-done"]).toMatchObject({
      status: "completed",
      inflight: false,
    });
  });

  it("keeps a durable terminal Run settled when an older permission event arrives late", () => {
    __test_dispatch({
      type: "permission_request",
      payload: {
        session_id: "default",
        run_id: "root-done",
        task_scope_id: "scope-done",
      },
    });

    __test_dispatch({
      type: "task_projections_response",
      payload: {
        session_id: "default",
        projections: [
          {
            projection_id: "projection-done",
            run_id: "root-done",
            task_scope_id: "scope-done",
            ui_state: "open",
            version: 10,
            status: "completed",
            started_at: 10,
            updated_at: 20,
            ended_at: 20,
          },
        ],
      },
    });

    // A queued event from the old stream may be delivered after rehydration.
    __test_dispatch({
      type: "permission_request",
      payload: {
        session_id: "default",
        run_id: "root-done",
        task_scope_id: "scope-done",
      },
    });

    const session = useSessionsStore.getState().sessions.default;
    expect(session.run_projections["root-done"]).toMatchObject({
      status: "completed",
      inflight: false,
    });
    expect(session).toMatchObject({
      status: "idle",
      inflight: false,
      active_run_id: null,
    });
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

  it("restores a durable artifact envelope as an ArtifactCard instead of raw assistant JSON", () => {
    __test_dispatch({
      type: "session_messages_response",
      payload: {
        session_id: "default",
        messages: [
          {
            id: "artifact-event-history",
            role: "assistant",
            text: JSON.stringify({
              tool: "artifact_create",
              ok: true,
              result: "# 调研结论\n\n- 2024年末全国人口：140828万人",
              artifacts: [
                {
                  kind: "text",
                  title: "research_report",
                  preview: "# 调研结论\n\n- 2024年末全国人口：140828万人",
                },
              ],
            }),
            ts: 2000,
          },
        ],
      },
    });

    const messages = useSessionsStore.getState().sessions.default.messages;
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({
      id: "workflow-artifact:artifact-event-history",
      role: "tool_result",
      tool_name: "artifact_create",
      tool_ok: true,
      workflow_event_id: "artifact-event-history",
      tool_result: expect.stringContaining("research_report"),
    });
    expect(messages.some((message) => message.role === "assistant")).toBe(false);
  });

  it("rebuilds one legacy text artifact from its nested workflow file without duplicating it", () => {
    __test_dispatch({
      type: "session_messages_response",
      payload: {
        session_id: "default",
        messages: [
          {
            id: "legacy-file-artifact",
            role: "assistant",
            text: JSON.stringify({
              tool: "artifact_create",
              ok: true,
              result: "legacy report preview",
              artifacts: [{ kind: "text", title: "research_report", preview: "legacy report preview" }],
            }),
            ts: 2000,
            workflow_event: {
              event_id: "legacy-file-artifact",
              event_type: "workflow.artifact_card",
              payload: {
                payload: {
                  artifact: {
                    kind: "file",
                    path: "C:\\DeepResearch\\report.md",
                    title: "report.md",
                    mime: "text/markdown",
                    size_bytes: 123,
                    sha256: "abc123",
                  },
                },
              },
            },
          },
        ],
      },
    });

    const messages = useSessionsStore.getState().sessions.default.messages;
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({
      id: "workflow-artifact:legacy-file-artifact",
      role: "tool_result",
      tool_name: "artifact_create",
      workflow_event_id: "legacy-file-artifact",
    });
    expect(JSON.parse(messages[0].tool_result || "{}")).toMatchObject({
      tool: "artifact_create",
      artifacts: [
        {
          kind: "file",
          path: "C:\\DeepResearch\\report.md",
          title: "report.md",
          mime: "text/markdown",
          size_bytes: 123,
          sha256: "abc123",
        },
      ],
    });
  });

  it("accepts the one-level legacy workflow artifact shape", () => {
    __test_dispatch({
      type: "session_messages_response",
      payload: {
        session_id: "default",
        messages: [
          {
            id: "one-level-file-artifact",
            role: "assistant",
            text: JSON.stringify({
              tool: "artifact_create",
              ok: true,
              artifacts: [{ kind: "text", title: "research_report", preview: "legacy" }],
            }),
            workflow_event: {
              event_type: "workflow.artifact_card",
              payload: {
                artifact: {
                  kind: "file",
                  path: "C:\\DeepResearch\\one-level.md",
                  title: "one-level.md",
                },
              },
            },
          },
        ],
      },
    });

    const messages = useSessionsStore.getState().sessions.default.messages;
    expect(messages).toHaveLength(1);
    expect(JSON.parse(messages[0].tool_result || "{}").artifacts).toEqual([
      expect.objectContaining({
        kind: "file",
        path: "C:\\DeepResearch\\one-level.md",
        title: "one-level.md",
      }),
    ]);
  });

  it("keeps the legacy text artifact when the workflow file path is invalid", () => {
    __test_dispatch({
      type: "session_messages_response",
      payload: {
        session_id: "default",
        messages: [
          {
            id: "invalid-file-artifact",
            role: "assistant",
            text: JSON.stringify({
              tool: "artifact_create",
              ok: true,
              artifacts: [{ kind: "text", title: "research_report", preview: "keep me" }],
            }),
            workflow_event: {
              event_type: "workflow.artifact_card",
              payload: {
                payload: {
                  artifact: { kind: "file", path: "   ", title: "missing.md" },
                },
              },
            },
          },
        ],
      },
    });

    const messages = useSessionsStore.getState().sessions.default.messages;
    expect(messages).toHaveLength(1);
    expect(JSON.parse(messages[0].tool_result || "{}").artifacts).toEqual([
      { kind: "text", title: "research_report", preview: "keep me" },
    ]);
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
