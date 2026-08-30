// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InputBar } from "./InputBar";
import { useSessionsStore } from "../stores/sessionsStore";
import { controlWS } from "./controlWs";

vi.mock("./controlWs", () => ({
  controlWS: {
    send: vi.fn(() => true),
    on_message: vi.fn(() => () => {}),
    state: vi.fn(() => "connected"),
  },
}));

function resetStore() {
  useSessionsStore.setState({
    active_sid: "default",
    sessions: {
      default: {
        base_session_id: "default",
        project_root: null,
        project_name: "(untitled)",
        messages: [],
        todos: [],
        token_usage: { prompt: 0, completion: 0 },
        context_usage: null,
        status: "idle",
        last_activity: Date.now(),
        inflight: false,
        active_run_id: null,
        selected_run_id: null,
        run_projections: {},
        current_iteration: 0,
        max_iterations: 50,
        tool_signature_repeat: 0,
        supervisor_severity: "green",
        supervisor_alert: null,
        supervisor_inbox: [],
        auto_resume_attempts: 0,
        provider_id: null,
        preferred_model: null,
        model_params: null,
      },
    },
    inflight_count: 0,
    inflight_max: 2,
  });
}

describe("InputBar chat send", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.mocked(controlWS.send).mockClear();
    vi.mocked(controlWS.on_message).mockClear();
    vi.mocked(controlWS.send).mockReturnValue(true);
    resetStore();
  });

  it("sends chat_v2 immediately when Enter is pressed", () => {
    render(<InputBar sessionId="default" placeholder="chat" />);

    const input = screen.getByPlaceholderText("chat");
    fireEvent.change(input, { target: { value: "你好啊" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });

    expect(controlWS.send).toHaveBeenCalledWith({
      type: "chat_v2",
      payload: expect.objectContaining({
        text: "你好啊",
        session_id: "default",
        request_id: expect.any(String),
        turn_id: expect.any(String),
      }),
    });
    expect(useSessionsStore.getState().sessions.default.messages.at(-1)?.text).toBe(
      "你好啊",
    );
  });

  it("sends selected text files as input_text attachments", async () => {
    render(<InputBar sessionId="default" placeholder="chat" />);

    const file = new File(["FIRST LINE\nbody-canary"], "fixture.txt", {
      type: "text/plain",
    });
    fireEvent.change(screen.getByTestId("text-attachment-input"), {
      target: { files: [file] },
    });
    expect(await screen.findByText(/fixture\.txt/)).toBeTruthy();

    const input = screen.getByPlaceholderText("chat");
    fireEvent.change(input, { target: { value: "附件第一行是什么？" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });

    await waitFor(() =>
      expect(controlWS.send).toHaveBeenCalledWith({
        type: "chat_v2",
        payload: expect.objectContaining({
          text: "附件第一行是什么？",
          session_id: "default",
          attachments: [
            expect.objectContaining({
              type: "input_text",
              name: "fixture.txt",
              media_type: "text/plain",
              data: "FIRST LINE\nbody-canary",
            }),
          ],
        }),
      }),
    );
    expect(screen.queryByTestId("attachment-list")).toBeNull();
  });

  it("rejects a text attachment above the 8 MiB block limit", async () => {
    render(<InputBar sessionId="default" placeholder="chat" />);

    const file = new File(["small"], "oversized.txt", { type: "text/plain" });
    Object.defineProperty(file, "size", { value: 8 * 1024 * 1024 + 1 });
    fireEvent.change(screen.getByTestId("text-attachment-input"), {
      target: { files: [file] },
    });

    expect((await screen.findByRole("alert")).textContent).toContain(
      "超过单文件 8 MiB 限制",
    );
    expect(screen.queryByTestId("attachment-list")).toBeNull();
  });

  it("does not send when Enter confirms an active IME composition", () => {
    render(<InputBar sessionId="default" placeholder="chat" />);

    const input = screen.getByPlaceholderText("chat");
    fireEvent.change(input, { target: { value: "阿斯顿" } });
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      isComposing: true,
    });

    expect(controlWS.send).not.toHaveBeenCalled();
    expect((input as HTMLTextAreaElement).value).toBe("阿斯顿");
  });

  it("does not send for macOS WebKit IME keyCode 229", () => {
    render(<InputBar sessionId="default" placeholder="chat" />);

    const input = screen.getByPlaceholderText("chat");
    fireEvent.change(input, { target: { value: "阿斯顿" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      keyCode: 229,
      shiftKey: false,
      isComposing: false,
    });

    expect(controlWS.send).not.toHaveBeenCalled();
    expect((input as HTMLTextAreaElement).value).toBe("阿斯顿");
  });

  it("swallows WebKit's Enter after compositionend, then allows a later Enter", () => {
    render(<InputBar sessionId="default" placeholder="chat" />);

    const input = screen.getByPlaceholderText("chat");
    fireEvent.change(input, { target: { value: "阿斯顿" } });
    fireEvent.compositionStart(input);
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, {
      key: "Enter",
      keyCode: 13,
      shiftKey: false,
      isComposing: false,
    });

    expect(controlWS.send).not.toHaveBeenCalled();
    expect((input as HTMLTextAreaElement).value).toBe("阿斯顿");

    fireEvent.keyDown(input, {
      key: "Enter",
      keyCode: 13,
      shiftKey: false,
      isComposing: false,
    });
    expect(controlWS.send).toHaveBeenCalledWith({
      type: "chat_v2",
      payload: expect.objectContaining({
        text: "阿斯顿",
        session_id: "default",
      }),
    });
  });

  // 2026-08-09 保留会话移除后的空态入口：没有任何会话时在输入框直接发消息，
  // 必须让后端**新建**一个会话再投递，而不是复活固定 sid。
  it("空态发送走 new_session，不复活任何固定 sid", () => {
    useSessionsStore.setState({ active_sid: "", sessions: {} });
    render(<InputBar placeholder="chat" />);

    const input = screen.getByPlaceholderText("chat");
    fireEvent.change(input, { target: { value: "第一句话" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });

    expect(controlWS.send).toHaveBeenCalledWith({
      type: "chat_v2",
      payload: expect.objectContaining({
        text: "第一句话",
        session_id: "",
        new_session: true,
        request_id: expect.any(String),
        turn_id: expect.any(String),
      }),
    });
    // 没有 sid 可写：本地不做乐观 push（用户气泡由后端 chat_v2_user_echo
    // 带着新 sid 回推），store 里也不能凭空长出会话。
    expect(useSessionsStore.getState().sessions).toEqual({});
    expect((input as HTMLTextAreaElement).value).toBe("");
  });

  it("空态可发送全局 Skill 安装请求并保留 new_session 语义", () => {
    useSessionsStore.setState({ active_sid: "", sessions: {} });
    render(<InputBar placeholder="chat" />);

    const request =
      "安装这个skill https://github.com/DennyWanye/plan-test-skill";
    const input = screen.getByPlaceholderText("chat");
    fireEvent.change(input, { target: { value: request } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });

    expect(controlWS.send).toHaveBeenCalledWith({
      type: "chat_v2",
      payload: expect.objectContaining({
        text: request,
        session_id: "",
        new_session: true,
      }),
    });
  });

  it("空态发送失败时保留输入内容", () => {
    vi.mocked(controlWS.send).mockReturnValue(false);
    useSessionsStore.setState({ active_sid: "", sessions: {} });
    render(<InputBar placeholder="chat" />);

    const input = screen.getByPlaceholderText("chat") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "别弄丢我" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });

    // 空态没有会话可承载错误气泡，只能靠"不清空输入框"保证消息不丢。
    expect(input.value).toBe("别弄丢我");
    expect(useSessionsStore.getState().sessions).toEqual({});
  });

  it("clears thinking when the control channel rejects the send", () => {
    vi.mocked(controlWS.send).mockReturnValue(false);
    render(<InputBar sessionId="default" placeholder="chat" />);

    const input = screen.getByPlaceholderText("chat");
    fireEvent.change(input, { target: { value: "hello" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });

    const session = useSessionsStore.getState().sessions.default;
    expect(session.inflight).toBe(false);
    expect(session.status).toBe("error");
    expect(session.messages.at(-1)?.role).toBe("error");
  });

  it("allows a second task while another run is inflight", () => {
    useSessionsStore.getState().upsert("default", {
      status: "running",
      inflight: true,
      active_run_id: "run-current",
    });
    render(<InputBar sessionId="default" placeholder="chat" />);

    const input = screen.getByPlaceholderText("chat") as HTMLTextAreaElement;
    expect(input.disabled).toBe(false);
    fireEvent.change(input, { target: { value: "run in parallel" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });

    expect(controlWS.send).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "chat_v2",
        payload: expect.objectContaining({ text: "run in parallel" }),
      }),
    );
    expect(useSessionsStore.getState().sessions.default.messages).toHaveLength(1);
  });

  it("renders status from the selected task instead of stale session activity", () => {
    useSessionsStore.getState().upsert_run_projection(
      "default",
      "run-completed",
      {
        task_scope_id: "scope-completed",
        version: 1,
        status: "completed",
        inflight: false,
        ui_state: "open",
      },
    );
    useSessionsStore.getState().upsert("default", {
      status: "running",
      selected_run_id: "run-completed",
    });

    render(<InputBar sessionId="default" placeholder="chat" />);

    expect(screen.getByText("✓ 空闲")).toBeTruthy();
    expect(screen.queryByText("🔧 工具执行中")).toBeNull();
  });

  it("returns to idle after the selected task reaches a failed terminal state", () => {
    useSessionsStore.getState().upsert_run_projection(
      "default",
      "run-failed",
      {
        task_scope_id: "scope-failed",
        version: 1,
        status: "failed",
        inflight: false,
        ui_state: "open",
      },
    );
    useSessionsStore.getState().upsert("default", {
      status: "error",
      selected_run_id: "run-failed",
      inflight: false,
    });

    render(<InputBar sessionId="default" placeholder="chat" />);

    expect(screen.getByText(/空闲/)).toBeTruthy();
    expect(screen.queryByText(/错误/)).toBeNull();
  });

  it("blocks chat while companion identity is not ready", () => {
    render(<InputBar sessionId="default" placeholder="正在恢复身份…" disabled />);

    const input = screen.getByPlaceholderText("正在恢复身份…") as HTMLTextAreaElement;
    expect(input.disabled).toBe(true);

    fireEvent.change(input, { target: { value: "不应发送" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });

    expect(controlWS.send).not.toHaveBeenCalled();
    expect(useSessionsStore.getState().sessions.default.messages).toHaveLength(0);
  });

  it("keeps the compact composer to one dynamic action button", () => {
    render(<InputBar sessionId="default" placeholder="chat" />);

    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "发送" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "新话题" })).toBeNull();
  });

  it("targets a selected waiting task with a fenced continuation", () => {
    useSessionsStore.getState().upsert_run_projection(
      "default",
      "root-waiting",
      {
        task_scope_id: "scope-waiting",
        version: 2,
        conversation_boundary_ref: "boundary-waiting",
        conversation_boundary_version: 4,
        status: "waiting",
        inflight: false,
        ui_state: "open",
      },
    );
    render(<InputBar sessionId="default" placeholder="chat" />);

    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "继续这个任务" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });

    expect(controlWS.send).toHaveBeenCalledWith({
      type: "chat_v2",
      payload: expect.objectContaining({
        session_id: "default",
        text: "继续这个任务",
        target_root_run_id: "root-waiting",
        task_scope_id: "scope-waiting",
        conversation_boundary_version: 4,
      }),
    });
    expect(
      useSessionsStore.getState().sessions.default.messages.at(-1),
    ).toMatchObject({
      run_id: "root-waiting",
      task_scope_id: "scope-waiting",
      continuation_status: "waiting",
    });
    expect(screen.getByTestId("continuation-target").textContent).toContain(
      "将在安全边界读取",
    );
  });

  it("queues steering on the selected running task instead of starting a root", () => {
    useSessionsStore.getState().upsert_run_projection(
      "default",
      "root-running",
      {
        task_scope_id: "scope-running",
        version: 3,
        conversation_boundary_ref: "boundary-running",
        conversation_boundary_version: 7,
        status: "running",
        inflight: true,
        ui_state: "open",
      },
    );
    render(<InputBar sessionId="default" placeholder="chat" />);

    const input = screen.getByRole("textbox");
    fireEvent.change(input, {
      target: { value: "steer the running task" },
    });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });

    expect(controlWS.send).toHaveBeenCalledWith({
      type: "chat_v2",
      payload: expect.objectContaining({
        session_id: "default",
        text: "steer the running task",
        target_root_run_id: "root-running",
        task_scope_id: "scope-running",
        conversation_boundary_version: 7,
      }),
    });
    expect(
      useSessionsStore.getState().sessions.default.messages.at(-1),
    ).toMatchObject({
      run_id: "root-running",
      task_scope_id: "scope-running",
      continuation_status: "waiting",
    });
  });

  it("defers a second input until the newly sent root Run is reserved", () => {
    render(<InputBar sessionId="default" placeholder="chat" />);
    const input = screen.getByRole("textbox");

    fireEvent.change(input, { target: { value: "019fa8bd" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });
    const pendingRequestId =
      useSessionsStore.getState().sessions.default.pending_root_request_id;
    expect(pendingRequestId).toEqual(expect.any(String));
    expect(controlWS.send).toHaveBeenCalledTimes(1);

    fireEvent.change(input, { target: { value: "帮我打开 Godot" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });

    expect(controlWS.send).toHaveBeenCalledTimes(1);
    expect(
      useSessionsStore.getState().sessions.default.messages.at(-1),
    ).toMatchObject({
      text: "帮我打开 Godot",
      deferred_send: true,
      deferred_parent_request_id: pendingRequestId,
      continuation_status: "waiting",
    });
  });

  it("defers steering while the selected Run is starting without a boundary", () => {
    useSessionsStore.getState().upsert_run_projection(
      "default",
      "root-starting",
      {
        task_scope_id: "scope-starting",
        status: "starting",
        inflight: true,
        ui_state: "open",
      },
    );
    render(<InputBar sessionId="default" placeholder="chat" />);
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "再补充一条" } });
    fireEvent.keyDown(input, {
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: false },
    });

    expect(controlWS.send).not.toHaveBeenCalled();
    expect(
      useSessionsStore.getState().sessions.default.messages.at(-1),
    ).toMatchObject({
      run_id: "root-starting",
      task_scope_id: "scope-starting",
      deferred_send: true,
      continuation_status: "waiting",
    });
  });

  it("stops the selected run without cancelling another inflight run", () => {
    const store = useSessionsStore.getState();
    store.upsert_run_projection("default", "root-a", {
      task_scope_id: "scope-a",
      version: 0,
      status: "running",
      inflight: true,
      ui_state: "open",
    });
    store.upsert_run_projection("default", "root-b", {
      task_scope_id: "scope-b",
      version: 0,
      status: "running",
      inflight: true,
      ui_state: "background",
    });
    store.select_run_projection("default", "root-b");
    render(<InputBar sessionId="default" placeholder="chat" />);

    fireEvent.click(screen.getByRole("button", { name: "■ 停止" }));

    expect(controlWS.send).toHaveBeenCalledWith({
      type: "chat_v2_interrupt",
      payload: {
        session_id: "default",
        run_id: "root-b",
      },
    });
    expect(
      useSessionsStore.getState().sessions.default.run_projections["root-a"]
        .inflight,
    ).toBe(true);
  });

  it("keeps stop available for a rehydrated waiting Run", () => {
    const store = useSessionsStore.getState();
    store.upsert_run_projection("default", "root-waiting", {
      task_scope_id: "scope-waiting",
      version: 1,
      status: "waiting",
      inflight: false,
      ui_state: "open",
    });
    store.select_run_projection("default", "root-waiting");
    store.upsert("default", { inflight: false, status: "idle" });
    render(<InputBar sessionId="default" placeholder="chat" />);

    fireEvent.click(screen.getByRole("button", { name: "■ 停止" }));

    expect(controlWS.send).toHaveBeenCalledWith({
      type: "chat_v2_interrupt",
      payload: {
        session_id: "default",
        run_id: "root-waiting",
      },
    });
  });
});
