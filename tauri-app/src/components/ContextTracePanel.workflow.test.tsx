import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { IncomingMessage } from "../types/messages";
import type { ControlChannel } from "../ws/ControlChannel";
import { ContextTracePanel } from "./ContextTracePanel";

afterEach(cleanup);

function channelHarness() {
  let listener: ((message: IncomingMessage) => void) | null = null;
  const channel = {
    send: vi.fn(),
    onMessage: vi.fn((next: (message: IncomingMessage) => void) => {
      listener = next;
      return () => { listener = null; };
    }),
  };
  return {
    channel: channel as unknown as ControlChannel,
    send: channel.send,
    emit(message: IncomingMessage) {
      act(() => listener?.(message));
    },
  };
}

const run = {
  run_id: "run-1",
  workflow_name: "deep-research",
  workflow_version: "1",
  status: "waiting" as const,
  created_at: 1,
  updated_at: 2,
  active_nodes: ["review"],
  run_version: 7,
  trace_id: "trace-1",
};

function detail(overrides: Record<string, unknown> = {}) {
  return {
    type: "workflow_run_detail_response" as const,
    request_id: "filled-by-load-panel",
    ok: true as const,
    payload: {
      run_id: run.run_id,
      run_version: 7,
      nodes: [{ id: "review", label: "Review", status: "waiting", attempt: 1 }],
      edges: [],
      spans: [{ span_id: "span-1", name: "model call", kind: "model", status: "ok", redacted: true }],
      checkpoints: [{ checkpoint_id: "cp-1", checkpoint_ns: "", node_id: "review", created_at: 1, status: "safe", can_fork: true, requires_effect_confirmation: true, effect_summary: "文件写入可能重新执行" }],
      evaluations: [],
      decisions: [{ decision_id: "decision-1", run_id: run.run_id, kind: "approval", status: "open" as const, prompt: { title: "批准计划" }, nonce: "nonce-1", version: 3, created_at: 1 }],
      deliveries: [],
      ...overrides,
    },
  };
}

async function loadPanel(harness: ReturnType<typeof channelHarness>, overrides: Record<string, unknown> = {}) {
  const listRequest = harness.send.mock.calls.find(([message]) => message.type === "workflow_runs_list")?.[0];
  harness.emit({ type: "workflow_runs_list_response", request_id: listRequest.request_id, ok: true, payload: { runs: [run], next_cursor: null } });
  await waitFor(() => expect(harness.send.mock.calls.some(([message]) => message.type === "workflow_run_detail")).toBe(true));
  const detailRequest = [...harness.send.mock.calls].reverse().find(([message]) => message.type === "workflow_run_detail")?.[0];
  harness.emit({ ...detail(overrides), request_id: detailRequest.request_id });
  await waitFor(() => expect(screen.getAllByText("Review").length).toBeGreaterThan(0));
}

describe("ContextTracePanel workflow integration", () => {
  it("loads workflow views and guards stale decision double-submit", async () => {
    const harness = channelHarness();
    render(<ContextTracePanel open onClose={vi.fn()} getChannel={() => harness.channel} />);
    expect(harness.send.mock.calls[0][0].type).toBe("workflow_runs_list");
    await loadPanel(harness);

    fireEvent.click(screen.getByRole("tab", { name: "Trace" }));
    expect(screen.getByRole("tree").textContent).toContain("model call");
    expect(screen.getByText("敏感详情已脱敏")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Runs" }));

    const approve = screen.getByRole("button", { name: "同意" });
    fireEvent.click(approve);
    fireEvent.click(approve);
    const decisions = harness.send.mock.calls.map(([message]) => message).filter((message) => message.type === "workflow_decision_resolve");
    expect(decisions).toHaveLength(1);
    expect(decisions[0].payload).toMatchObject({ decision_id: "decision-1", nonce: "nonce-1", expected_version: 3, response: { approved: true } });

    harness.emit({ type: "workflow_ipc_error", request_type: "workflow_decision_resolve", request_id: decisions[0].request_id, ok: false, error: { code: "stale_decision", message: "stale decision", retryable: false }, payload: { error: { code: "stale_decision", message: "stale decision", retryable: false } } });
    expect(screen.getByRole("alert").textContent).toContain("状态已更新");
    expect(harness.send.mock.calls.filter(([message]) => message.type === "workflow_run_detail").length).toBeGreaterThan(1);
  });

  it("requires dangerous-effect confirmation before forking", async () => {
    const harness = channelHarness();
    render(<ContextTracePanel open onClose={vi.fn()} getChannel={() => harness.channel} />);
    await loadPanel(harness);
    fireEvent.click(screen.getByRole("tab", { name: "Checkpoints" }));
    fireEvent.click(screen.getByRole("button", { name: "从这里创建分支" }));
    const create = screen.getByRole("button", { name: "创建分支" });
    fireEvent.click(create);
    expect(screen.getByRole("alert").textContent).toContain("确认可能重新执行的副作用");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(create);
    fireEvent.click(create);
    const forks = harness.send.mock.calls.map(([message]) => message).filter((message) => message.type === "workflow_checkpoint_fork");
    expect(forks).toHaveLength(1);
    expect(forks[0].payload).toMatchObject({ run_id: "run-1", checkpoint_id: "cp-1", expected_version: 7, confirm_dangerous_effects: true });
    expect(forks[0].payload).not.toHaveProperty("fork_key");
    expect(forks[0].request_id).toEqual(expect.any(String));
  });

  it("keeps fork errors visible and unlocks the dialog for retry", async () => {
    const harness = channelHarness();
    render(<ContextTracePanel open onClose={vi.fn()} getChannel={() => harness.channel} />);
    await loadPanel(harness);
    fireEvent.click(screen.getByRole("tab", { name: "Checkpoints" }));
    const checkpointFork = document.querySelector("ol button");
    expect(checkpointFork).not.toBeNull();
    fireEvent.click(checkpointFork!);
    fireEvent.click(screen.getByRole("checkbox"));
    const dialogs = screen.getAllByRole("dialog");
    const dialogButtons = dialogs[dialogs.length - 1].querySelectorAll("button");
    const create = dialogButtons[dialogButtons.length - 1];
    fireEvent.click(create);
    const first = harness.send.mock.calls.map(([message]) => message).find((message) => message.type === "workflow_checkpoint_fork");
    harness.emit({
      type: "workflow_ipc_error",
      request_type: "workflow_checkpoint_fork",
      request_id: first.request_id,
      ok: false,
      error: { code: "fork_confirmation_required", message: "confirm effects", retryable: false },
      payload: { error: { code: "fork_confirmation_required", message: "confirm effects", retryable: false } },
    });

    expect(screen.getAllByRole("alert").some((item) => item.textContent?.includes("confirm effects"))).toBe(true);
    fireEvent.click(create);
    const forks = harness.send.mock.calls.map(([message]) => message).filter((message) => message.type === "workflow_checkpoint_fork");
    expect(forks).toHaveLength(2);
  });

  it("submits one human evaluation with labels and comment", async () => {
    const harness = channelHarness();
    render(<ContextTracePanel open onClose={vi.fn()} getChannel={() => harness.channel} />);
    await loadPanel(harness, { decisions: [] });
    fireEvent.click(screen.getByRole("tab", { name: "Evaluations" }));
    fireEvent.change(screen.getByLabelText("评分"), { target: { value: "0.9" } });
    fireEvent.change(screen.getByLabelText("评分标签"), { target: { value: "准确, 清晰" } });
    fireEvent.change(screen.getByLabelText("评分说明"), { target: { value: "证据完整" } });
    const submit = screen.getByRole("button", { name: "提交评分" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    const evaluations = harness.send.mock.calls.map(([message]) => message).filter((message) => message.type === "workflow_evaluation_submit");
    expect(evaluations).toHaveLength(1);
    expect(evaluations[0].payload).toMatchObject({ trace_id: "trace-1", run_id: "run-1", evaluator_name: "human", evaluator_type: "human", verdict: "pass", score: 0.9, labels: ["准确", "清晰"], explanation: "证据完整" });
  });

  it("keeps the legacy Context decision timeline available", async () => {
    const harness = channelHarness();
    render(<ContextTracePanel open onClose={vi.fn()} getChannel={() => harness.channel} />);
    fireEvent.click(screen.getByRole("tab", { name: "Context" }));
    await waitFor(() => expect(harness.send.mock.calls.some(([message]) => message.type === "decisions_list")).toBe(true));
    harness.emit({
      type: "decisions_list_response",
      payload: {
        decisions: [{ timestamp: 1, classifier_path: "cloud", reason: "legacy", latency_ms: 20, total_tokens: 120, token_breakdown: { system: 20, history: 100 } }],
      },
    });
    expect(screen.getByTestId("trace-decision-0").textContent).toContain("legacy");
    expect(screen.getByTestId("trace-breakdown")).toBeTruthy();
  });

  it("shows prepared provider facts without rendering request bodies", async () => {
    const harness = channelHarness();
    render(<ContextTracePanel open onClose={vi.fn()} getChannel={() => harness.channel} />);
    fireEvent.click(screen.getByRole("tab", { name: "Context" }));
    await waitFor(() => expect(harness.send.mock.calls.some(([message]) => message.type === "decisions_list")).toBe(true));
    harness.emit({
      type: "decisions_list_response",
      payload: {
        decisions: [],
        attempts: [{
          session_id: "s1", request_id: "r1", attempt_id: "a1", purpose: "agent_response", state: "succeeded",
          provider_id: "relay", model_id: "gpt-test", adapter_id: "openai-compatible", adapter_version: "v1",
          message_hash: "messagehash", logical_tool_hash: "logicalhash", wire_tool_hash: "wirehash",
          schema_fingerprint: "schemahash", policy_fingerprint: "policyhash", registry_revision: 2, tool_scope_revision: 3,
          direct_tool_count: 2, activated_tool_count: 1, deferred_tool_count: 4,
          schema_tokens_by_name: [["read", 12]], selection_reasons: ["read:direct"],
          fragments: [{ fragment_id: "memory.l3:42", action: "trimmed", reason: "budget", estimated_tokens: 12, cache_scope: "", cache_hash: "" }],
          coverage_entries: [{ kind: "summary", message_ids: [1, 2], segment_id: "seg-1", source_hash: "sourcehash" }],
          coverage_valid: false, coverage_gaps: [3], coverage_overlaps: [], coverage_stale_segment_ids: ["seg-old"],
          coverage_broken_causal_groups: [], coverage_page_in_refs: 1,
          tool_tokens: 12, message_tokens: 100, attachment_tokens: 0, reserve_tokens: 64, effective_input_budget: 512,
          context_window: 1024, planned_tokens: 112, estimate_method: "canonical_json_conservative_v1",
          cache_boundary: 0, cache_fingerprint: "cachehash", actual_input_tokens: 101, actual_output_tokens: 9,
          actual_cache_read_tokens: 20, actual_cache_write_tokens: 0, transport_retry_count: 1,
          requested_compression_model: "follow_session", resolved_compression_model: "summary-model", actual_compression_model: "summary-model",
          compression_provider: "relay", compression_source: "follow_session", compression_failure: "", reasons: [], created_at: 1, updated_at: 2,
        }],
      },
    });

    const row = screen.getByTestId("context-attempt-a1");
    expect(row.textContent).toContain("agent_response · succeeded");
    expect(row.textContent).toContain("follow_session → summary-model → summary-model");
    expect(screen.getByTestId("context-attempt-fragments").textContent).toContain("trimmed · memory.l3:42");
    expect(screen.getByTestId("context-attempt-coverage").textContent).toContain("coverage invalid · page-in refs 1 · gaps 1");
    expect(screen.getByTestId("context-attempt-coverage").textContent).toContain("summary · ids 1,2 · segment seg-1");
    expect(row.textContent).not.toContain("TOP-SECRET-BODY");
  });
});
