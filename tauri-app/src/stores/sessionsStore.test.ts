// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P5-S1 D — severity_score formula + pet_focus_sid selector tests.
 */
import { describe, expect, it } from "vitest";

import {
  type WorkflowEventEnvelope,
  type SessionState,
  type SupervisorAlertEntry,
  collect_inbox,
  count_unhandled_by_severity,
  pet_focus_sid,
  severity_score,
  severity_score_breakdown,
  useSessionsStore,
} from "./sessionsStore";

const PRODUCTION_V3_DIAGNOSTICS = [
  "deadline_exhausted", "search_port_unavailable", "provider_failure",
  "direct_failure", "fetch_failure", "blob_unavailable", "low_quality_source",
  "low_quality_content", "search_degraded", "support_rate_below_threshold",
  "published_factual_below_threshold", "citation_count_below_threshold",
  "domain_count_below_threshold", "body_bytes_below_threshold",
];

function workflowEvent(
  seq: number,
  event_type: string,
  payload: Record<string, unknown>,
  run_id = "run-1",
): WorkflowEventEnvelope {
  return {
    event_id: `${run_id}-event-${seq}`,
    run_id,
    seq,
    event_type,
    payload,
    created_at: seq,
  };
}

function deepResearchStage(
  seq: number,
  stageId = "search",
  runId = "research-run",
  overrides: Record<string, unknown> = {},
): WorkflowEventEnvelope {
  return {
    event_id: `${runId}-${stageId}-${seq}`,
    run_id: runId,
    seq,
    event_type: "workflow.progress",
    created_at: seq,
    payload: {
      schema_version: 2,
      kind: "stage",
      workflow_name: "deep_research",
      workflow_version: "v2",
      workflow_label: "深度调研",
      stage_id: stageId,
      stage_instance_id: `${stageId}-instance-${seq}`,
      stage: "搜索资料",
      ordinal: 4,
      total: 13,
      status: "completed",
      summary: "已保留 11 条候选",
      text: "已保留 11 条候选",
      completed_count: 4,
      metrics: { providers: 3, candidates: 18, kept: 11 },
      duration_ms: 1240,
      degraded: false,
      next_stage: "direct",
      action: "搜索可用资料来源",
      result: "尝试 3 个来源，找到 18 条候选，保留 11 条",
      result_code: "stage_ok",
      diagnostic_codes: [],
      ...overrides,
    },
  };
}

function deepResearchV5Stage(
  seq: number,
  runId = "research-v5-run",
  overrides: Record<string, unknown> = {},
): WorkflowEventEnvelope {
  return workflowEvent(seq, "workflow.progress", {
    schema_version: 5,
    capability: "deep_research_progress_v5",
    kind: "stage",
    workflow_name: "deep_research",
    workflow_version: "v5",
    workflow_label: "深度调研",
    stage_id: "gap_evaluate",
    public_stage_id: "gap",
    stage_instance_id: "0123456789abcdef01234567",
    stage: "检查缺口",
    ordinal: 4,
    total: 9,
    status: "completed",
    summary: "检查缺口已完成",
    text: "检查缺口已完成",
    visibility: "visible",
    action: "evaluate_gaps",
    result: "completed",
    discarded: 1,
    remaining_gap: 2,
    next_step: "research_gap",
    dimension_counts: {
      total: 5, core_total: 3, covered: 2, partially_covered: 2,
      uncovered: 1, not_applicable: 0, core_covered: 2,
      core_partially_covered: 1, core_uncovered: 0,
    },
    dimension_status_changes: { improved: 1, regressed: 0, unchanged: 4 },
    source_counts: { valid: 7, first_party: 2 },
    active_gap: { status: "running", work_kind: "query", dimension_ordinal: 2 },
    elapsed_seconds: 321,
    soft_checkpoint: "reached",
    lease_reason: "lease_renewed_measurable_gain",
    quality_score: 82,
    hard_failures: [],
    predicted_delivery: "partial",
    token_budget_ratio: 40,
    control_action: "generate_now",
    control_status: "open",
    parent_operation: "none",
    failed_dimensions: [],
    rejection_reasons: [],
    ...overrides,
  }, runId);
}

describe("legacy skill candidate removal", () => {
  it("does not preserve a legacy bare-candidate confirmation across history reload", () => {
    useSessionsStore.setState({
      active_sid: "default",
      sessions: {},
      inflight_count: 0,
      inflight_max: 2,
    });
    const store = useSessionsStore.getState();
    store.ensure("legacy-candidate");
    store.push_message("legacy-candidate", {
      id: "legacy-card",
      role: "skill_candidate",
      skill_candidate_id: 7,
      skill_candidate_awaiting: true,
    } as any);

    store.set_messages("legacy-candidate", []);

    expect(
      useSessionsStore.getState().sessions["legacy-candidate"].messages,
    ).toEqual([]);
  });
});

function deepResearchV7Children(
  seq: number,
  statuses: Array<"queued" | "running" | "retrying" | "valid" | "insufficient">,
  runId = "research-v7-run",
): WorkflowEventEnvelope {
  return workflowEvent(seq, "workflow.progress", {
    schema_version: 7,
    kind: "research_children",
    workflow_name: "deep_research",
    workflow_version: "v7",
    workflow_label: "深度调研",
    stage_id: "search",
    stage: "子代理调研与补救",
    ordinal: 3,
    total: 6,
    status: "started",
    text: `主 Agent 已拆分 ${statuses.length} 个调研子方向`,
    children: statuses.map((status, index) => ({
      child_id: `dr-${index}`,
      question: `子方向 ${index + 1} 是什么？`,
      status,
      attempt: status === "queued" ? 0 : status === "retrying" ? 2 : 1,
      max_attempts: 2,
      n_sources: status === "valid" ? 2 : 0,
      reason_code: status === "insufficient" ? "no_citations" : "",
    })),
  }, runId);
}

function mk(over: Partial<SessionState>): SessionState {
  return {
    base_session_id: "sid",
    project_root: "/tmp/x",
    project_name: "x",
    messages: [],
    todos: [],
    token_usage: { prompt: 0, completion: 0 },
    status: "running",
    last_activity: Date.now(),
    inflight: false,
    run_projections: {},
    current_iteration: 0,
    max_iterations: 50,
    tool_signature_repeat: 0,
    supervisor_severity: "green",
    supervisor_alert: null,
    ...over,
  };
}

describe("severity_score_breakdown", () => {
  it("idle session with fresh activity scores 0", () => {
    const s = mk({ status: "idle" });
    const b = severity_score_breakdown(s, Date.now());
    expect(b.total).toBe(0);
  });

  it("running session with no other signals scores 10 (base)", () => {
    const s = mk({ status: "running" });
    const b = severity_score_breakdown(s, Date.now());
    expect(b.base).toBe(10);
    expect(b.total).toBe(10);
  });

  it("permission status scores higher than running", () => {
    const s = mk({ status: "permission" });
    const b = severity_score_breakdown(s, Date.now());
    expect(b.base).toBe(25);
  });

  it("error status scores 60", () => {
    const s = mk({ status: "error" });
    const b = severity_score_breakdown(s, Date.now());
    expect(b.base).toBe(60);
  });

  it("yellow supervisor adds 20 boost", () => {
    const s = mk({ supervisor_severity: "yellow" });
    const b = severity_score_breakdown(s);
    expect(b.supervisor).toBe(20);
  });

  it("red supervisor adds 50 boost", () => {
    const s = mk({ supervisor_severity: "red" });
    const b = severity_score_breakdown(s);
    expect(b.supervisor).toBe(50);
  });

  it("repeat penalty caps at 40", () => {
    const s = mk({ tool_signature_repeat: 100 });
    const b = severity_score_breakdown(s);
    expect(b.repeat).toBe(40);
  });

  it("repeat penalty: 4 repeats = 40 points", () => {
    const s = mk({ tool_signature_repeat: 4 });
    const b = severity_score_breakdown(s);
    expect(b.repeat).toBe(40);
  });

  it("age penalty grows logarithmically with inactivity", () => {
    const now = Date.now();
    const fresh = severity_score_breakdown(mk({ last_activity: now }), now);
    const oneMin = severity_score_breakdown(
      mk({ last_activity: now - 60_000 }),
      now,
    );
    const sixteenMin = severity_score_breakdown(
      mk({ last_activity: now - 16 * 60_000 }),
      now,
    );
    expect(fresh.age).toBe(0);
    expect(oneMin.age).toBe(0); // log2(1) * 6 = 0
    expect(sixteenMin.age).toBeCloseTo(24, 0); // log2(16) * 6 = 24
  });

  it("age penalty caps at 30", () => {
    const now = Date.now();
    const veryOld = severity_score_breakdown(
      mk({ last_activity: now - 1000 * 60 * 1000 }),
      now,
    );
    expect(veryOld.age).toBe(30);
  });

  it("iteration pressure: 25/50 iters = 5 points", () => {
    const s = mk({ current_iteration: 25, max_iterations: 50 });
    const b = severity_score_breakdown(s);
    expect(b.iteration).toBe(5);
  });

  it("worst-case (error + repeat 8 + red + iter 50/50) sums above 160", () => {
    const s = mk({
      status: "error",
      tool_signature_repeat: 8,
      supervisor_severity: "red",
      current_iteration: 50,
      max_iterations: 50,
    });
    const total = severity_score(s);
    // 60 + 0 + 40 + 50 + 10 = 160 (age 0 since fresh)
    expect(total).toBe(160);
  });
});

describe("pet_focus_sid", () => {
  const now = Date.now();

  it("returns null when no eligible sessions", () => {
    expect(pet_focus_sid({}, now)).toBeNull();
    // Companion default has no project_root, not eligible
    expect(
      pet_focus_sid(
        {
          default: mk({
            base_session_id: "default",
            project_root: null,
          }),
        },
        now,
      ),
    ).toBeNull();
  });

  it("picks the highest-scoring sid", () => {
    const sids = {
      a: mk({ base_session_id: "a", status: "idle", supervisor_severity: "green" }),
      b: mk({ base_session_id: "b", status: "running", supervisor_severity: "yellow" }),
      c: mk({ base_session_id: "c", status: "running", tool_signature_repeat: 5 }),
    };
    expect(pet_focus_sid(sids, now)).toBe("c"); // 10 + 40 = 50, beats b's 30
  });

  it("inbox: count_unhandled_by_severity tallies across sessions and dedups severities", () => {
    const mkAlert = (id: string, sev: "yellow" | "red"): SupervisorAlertEntry => ({
      alert_id: id,
      severity: sev,
      action: "nudge",
      diagnosis: id,
      user_message: id,
      suggested_buttons: [],
      received_at: now,
    });
    const sids = {
      a: mk({
        base_session_id: "a",
        supervisor_inbox: [mkAlert("a1", "yellow"), mkAlert("a2", "red")],
      }),
      b: mk({
        base_session_id: "b",
        supervisor_inbox: [mkAlert("b1", "yellow"), mkAlert("b2", "yellow")],
      }),
      c: mk({ base_session_id: "c" }),
    };
    expect(count_unhandled_by_severity(sids, "yellow")).toBe(3);
    expect(count_unhandled_by_severity(sids, "red")).toBe(1);
    const reds = collect_inbox(sids, "red");
    expect(reds).toHaveLength(1);
    expect(reds[0].session_id).toBe("a");
    expect(reds[0].alert_id).toBe("a2");
  });

  it("inbox: apply_supervisor_alert dedups by alert_id (no double-counting on ws replay)", () => {
    useSessionsStore.setState({
      active_sid: "default",
      sessions: {},
      inflight_count: 0,
      inflight_max: 2,
    });
    const store = useSessionsStore.getState();
    store.ensure("s1", { project_name: "S1" });
    const alert: SupervisorAlertEntry = {
      alert_id: "alert-X",
      severity: "yellow",
      action: "nudge",
      diagnosis: "d",
      user_message: "m",
      suggested_buttons: ["A", "B"],
      received_at: now,
    };
    store.apply_supervisor_alert("s1", alert);
    store.apply_supervisor_alert("s1", alert); // duplicate landing
    expect(useSessionsStore.getState().sessions.s1.supervisor_inbox).toHaveLength(1);
    store.dismiss_alert("s1", "alert-X");
    expect(useSessionsStore.getState().sessions.s1.supervisor_inbox).toHaveLength(0);
    expect(useSessionsStore.getState().sessions.s1.supervisor_alert).toBeNull();
    expect(useSessionsStore.getState().sessions.s1.supervisor_severity).toBe("green");
  });

  it("inbox: dismiss_all_alerts clears one severity but keeps the other", () => {
    useSessionsStore.setState({
      active_sid: "default",
      sessions: {},
      inflight_count: 0,
      inflight_max: 2,
    });
    const store = useSessionsStore.getState();
    store.ensure("s1");
    const mkA = (id: string, sev: "yellow" | "red"): SupervisorAlertEntry => ({
      alert_id: id,
      severity: sev,
      action: "nudge",
      diagnosis: id,
      user_message: id,
      suggested_buttons: [],
      received_at: now,
    });
    store.apply_supervisor_alert("s1", mkA("y1", "yellow"));
    store.apply_supervisor_alert("s1", mkA("r1", "red"));
    store.dismiss_all_alerts("yellow");
    const inbox = useSessionsStore.getState().sessions.s1.supervisor_inbox ?? [];
    expect(inbox.map((a) => a.alert_id)).toEqual(["r1"]);
  });

  it("sessions without task metadata, active run, or alerts are excluded", () => {
    const sids = {
      default: mk({
        base_session_id: "default",
        project_root: null,
        status: "error", // would score 60 if eligible
      }),
      "task-real": mk({
        base_session_id: "task-real",
        status: "running", // 10
      }),
    };
    expect(pet_focus_sid(sids, now)).toBe("task-real");
  });
});

describe("workflow progress reducer (AC-23)", () => {
  it("projects required receipt aggregate and accepts a newer same-event CAS view", () => {
    const sid = "workflow-v6-delivery";
    const runId = "workflow-v6-delivery-run";
    const terminal = workflowEvent(9, "workflow.final", {
      kind: "final", status: "completed", workflow_version: "v6",
    }, runId);
    terminal.delivery_aggregate = {
      schema_version: 1, run_id: runId, manifest_ref: "manifest-v6", status: "queued",
      required_total: 1, pending: 1, delivering: 0, delivered: 0,
      retrying: 0, fenced: 0, failed: 0, updated_at: 10,
    };
    const store = useSessionsStore.getState();
    store.reduce_workflow_event(sid, terminal);
    let summary = useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_progress",
    );
    expect(summary?.workflow_delivery?.status).toBe("queued");

    store.reduce_workflow_event(sid, {
      ...terminal,
      delivery_aggregate: {
        schema_version: 1, run_id: runId, manifest_ref: "manifest-v6", status: "delivered",
        required_total: 1, pending: 0, delivering: 0, delivered: 1,
        retrying: 0, fenced: 0, failed: 0, updated_at: 11,
      },
    });
    summary = useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_progress",
    );
    expect(summary?.workflow_delivery?.status).toBe("delivered");

    // Invalid aggregate cardinality cannot overwrite the honest derived view.
    store.reduce_workflow_event(sid, {
      ...terminal,
      delivery_aggregate: {
        schema_version: 1, run_id: runId, manifest_ref: "manifest-v6", status: "failed",
        required_total: 1, pending: 0, delivering: 0, delivered: 1,
        retrying: 0, fenced: 0, failed: 1, updated_at: 12,
      },
    });
    summary = useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_progress",
    );
    expect(summary?.workflow_delivery?.status).toBe("delivered");
  });

  it("keeps server elapsed and run-start anchor monotonic across stage refreshes", () => {
    const sid = "workflow-v5-elapsed";
    const store = useSessionsStore.getState();
    store.reduce_workflow_event(
      sid,
      deepResearchV5Stage(10, "elapsed-run", { elapsed_seconds: 69 }),
    );
    let summary = useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_progress",
    );
    const startedAt = summary?.workflow_started_at;
    expect(summary?.workflow_elapsed_ms).toBe(69_000);

    store.reduce_workflow_event(
      sid,
      deepResearchV5Stage(11, "elapsed-run", { elapsed_seconds: 0, stage_id: "rerank" }),
    );
    summary = useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_progress",
    );
    expect(summary?.workflow_elapsed_ms).toBe(69_000);
    expect(summary?.workflow_started_at).toBe(startedAt);
  });

  it("accepts only safe v5 projections and applies terminal action matrices", () => {
    const sid = "workflow-v5";
    const store = useSessionsStore.getState();
    store.reduce_workflow_event(sid, deepResearchV5Stage(2));
    let summary = useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_progress",
    );
    expect(summary?.workflow_v5).toMatchObject({
      source_counts: { valid: 7, first_party: 2 },
      control_action: "generate_now",
      control_status: "open",
    });
    store.reduce_workflow_event(sid, workflowEvent(3, "workflow.final", {
      kind: "final", status: "completed", workflow_version: "v5",
      delivery_status: "partial",
      action_matrix: [{ action_id: "continue_research", enabled: true }],
    }, "research-v5-run"));
    summary = useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_progress",
    );
    expect(summary?.workflow_v5?.control_action).toBe("continue_research");
    expect(summary?.workflow_v5?.predicted_delivery).toBe("partial");

    const marker = "raw-sensitive-query";
    store.reduce_workflow_event(sid, deepResearchV5Stage(4, "unsafe-v5", { query: marker }));
    expect(JSON.stringify(useSessionsStore.getState().sessions[sid].messages)).not.toContain(marker);
  });

  it("keeps an open v5 control across the immediately following hidden stage start", () => {
    const sid = "workflow-v5-control-handoff";
    const runId = "control-handoff-run";
    const store = useSessionsStore.getState();
    store.reduce_workflow_event(sid, deepResearchV5Stage(20, runId, {
      stage_id: "rerank",
      public_stage_id: "rerank",
      control_action: "generate_now",
      control_status: "open",
    }));
    store.reduce_workflow_event(sid, deepResearchV5Stage(21, runId, {
      kind: "progress",
      status: "started",
      visibility: "hidden",
      stage_id: "synth",
      public_stage_id: "synth",
      action: "synthesize_report",
      result: "started",
      next_step: "audit_quality",
      control_action: "none",
      control_status: "none",
    }));

    const summary = useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_progress",
    );
    expect(summary?.workflow_v5).toMatchObject({
      action: "synthesize_report",
      control_action: "generate_now",
      control_status: "open",
    });
  });

  it("updates one card by run and keeps loop percentage monotonic", () => {
    let messages: SessionState["messages"] = [];
    const apply = (event: WorkflowEventEnvelope) => {
      useSessionsStore.setState({ sessions: { default: mk({ messages }) } });
      useSessionsStore.getState().reduce_workflow_event("default", event);
      messages = useSessionsStore.getState().sessions.default.messages;
    };

    apply(workflowEvent(1, "workflow.accepted", { workflow_name: "PPT" }));
    apply(workflowEvent(2, "workflow.progress", {
      workflow_name: "PPT", stage: "检查页面质量", ordinal: 11, total: 12, status: "started",
    }));
    apply(workflowEvent(3, "workflow.progress", {
      workflow_name: "PPT", stage: "重新生成页面", ordinal: 8, total: 12, status: "started",
    }));

    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({
      role: "workflow_progress",
      workflow_stage: "重新生成页面",
      workflow_ordinal: 8,
      workflow_display_ordinal: 11,
      workflow_seq: 3,
      workflow_status: "running",
    });
  });

  it("allows attempt recovery, rejects old seq, and locks terminal final", () => {
    const store = useSessionsStore.getState();
    store.ensure("workflow-test");
    store.reduce_workflow_event("workflow-test", workflowEvent(2, "workflow.progress", {
      workflow_name: "代码任务", stage: "运行测试", ordinal: 7, total: 9, status: "failed",
    }));
    store.reduce_workflow_event("workflow-test", workflowEvent(3, "workflow.progress", {
      workflow_name: "代码任务", stage: "运行测试", ordinal: 7, total: 9, status: "started",
    }));
    store.reduce_workflow_event("workflow-test", workflowEvent(1, "workflow.progress", {
      workflow_name: "代码任务", stage: "旧状态", ordinal: 1, total: 9, status: "waiting",
    }));
    store.reduce_workflow_event("workflow-test", workflowEvent(4, "workflow.final", {
      workflow_name: "代码任务", status: "completed",
    }));
    store.reduce_workflow_event("workflow-test", workflowEvent(5, "workflow.progress", {
      workflow_name: "代码任务", stage: "不应回退", ordinal: 1, total: 9, status: "started",
    }));

    expect(useSessionsStore.getState().sessions["workflow-test"].messages[0]).toMatchObject({
      workflow_status: "completed",
      workflow_terminal: true,
      workflow_stage: "运行测试",
      workflow_seq: 4,
    });
  });

  it("keeps concurrent runs separate and late history cannot replace live state", () => {
    const store = useSessionsStore.getState();
    store.ensure("race");
    store.reduce_workflow_event("race", workflowEvent(5, "workflow.progress", {
      workflow_name: "PPT", stage: "发布", ordinal: 12, total: 12, status: "started",
    }, "run-a"));
    store.reduce_workflow_event("race", workflowEvent(1, "workflow.accepted", {
      workflow_name: "调研",
    }, "run-b"));
    store.push_message("race", { id: "live-tool", role: "tool_result", tool_name: "x", ts: 5000 });
    store.push_message("race", { id: "run-a-event-1", role: "assistant", text: "旧进度文本", ts: 900 });

    store.merge_history_messages(
      "race",
      [{ id: "old-user", role: "user", text: "start", ts: 1000 }],
      [workflowEvent(1, "workflow.accepted", { workflow_name: "PPT" }, "run-a")],
    );

    const messages = useSessionsStore.getState().sessions.race.messages;
    expect(messages.filter((message) => message.role === "workflow_progress")).toHaveLength(2);
    expect(messages.find((message) => message.workflow_run_id === "run-a")).toMatchObject({
      workflow_seq: 5,
      workflow_stage: "发布",
    });
    expect(messages.some((message) => message.id === "live-tool")).toBe(true);
    expect(messages.some((message) => message.id === "old-user")).toBe(true);
    expect(messages.some((message) => message.id === "run-a-event-1")).toBe(false);
  });

  // r5 S05 真机回归：后端把 user 行持久化成回合作用域（turn-*），本地乐观副本
  // 带的是 Run 投影的任务作用域（task-*）。原实现拿这两个 id 做相等比较 → 同一条
  // 消息被判成"不同作用域"、拒绝对账 → 切走再切回后用户气泡渲染两次（DB 只有一行）。
  // 两边都有 run_id 时，Run 才是权威判据。
  it("同一 Run 内 turn-/task- 作用域不一致也应对账掉本地副本", () => {
    const sid = "history-scope-namespace";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.push_message(sid, {
      role: "user",
      text: "真机验证：请只回复四个字",
      run_id: "run-77c7",
      task_scope_id: "task-9782ba82",
    });

    store.merge_history_messages(
      sid,
      [
        {
          id: "34",
          role: "user",
          text: "真机验证：请只回复四个字",
          run_id: "run-77c7",
          task_scope_id: "turn-be06a2ed",
          ts: 1000,
        },
        {
          id: "35",
          role: "assistant",
          text: "收到确认",
          run_id: "run-77c7",
          task_scope_id: "task-9782ba82",
          ts: 1001,
        },
      ] as never,
      [],
    );

    const messages = useSessionsStore.getState().sessions[sid].messages;
    expect(messages.filter((message) => message.role === "user")).toHaveLength(1);
    expect(messages.find((message) => message.role === "user")?.id).toBe("34");
  });

  // 反向保护：run_id 不同 = 真的是两条不同消息，不能因为文本相同就合并。
  it("run_id 不同的同文本消息保持两条", () => {
    const sid = "history-scope-distinct-runs";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.push_message(sid, {
      role: "user",
      text: "重复文本",
      run_id: "run-a",
      task_scope_id: "task-a",
    });

    store.merge_history_messages(
      sid,
      [
        {
          id: "90",
          role: "user",
          text: "重复文本",
          run_id: "run-b",
          task_scope_id: "turn-b",
          ts: 2000,
        },
      ] as never,
      [],
    );

    expect(
      useSessionsStore.getState().sessions[sid].messages.filter(
        (message) => message.role === "user",
      ),
    ).toHaveLength(2);
  });

  it("replaces temporary live chat and tool rows with durable history one-for-one", () => {
    const sid = "history-live-reconcile";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.push_message(sid, { id: "live-user", role: "user", text: "检查 README" });
    store.push_message(sid, {
      id: "live-call",
      role: "tool_call",
      tool_name: "file_read",
      tool_args: { path: "README.md", line_end: 20 },
      run_id: "run-reconcile",
    });
    store.push_message(sid, {
      id: "live-result",
      role: "tool_result",
      tool_name: "file_read",
      tool_result: '{"ok":true,"lines":20}',
      run_id: "run-reconcile",
    });
    store.push_message(sid, {
      id: "live-final",
      role: "assistant",
      text: "检查完成",
      run_id: "run-reconcile",
    });

    store.merge_history_messages(sid, [
      { id: "db-user", role: "user", text: "检查 README", run_id: "run-reconcile", ts: 10 },
      {
        id: "db-call",
        role: "tool_call",
        tool_name: "file_read",
        tool_args: { line_end: 20, path: "README.md" },
        run_id: "run-reconcile",
        ts: 20,
      },
      {
        id: "db-result",
        role: "tool_result",
        tool_name: "file_read",
        tool_result: '{"lines":20,"ok":true}',
        run_id: "run-reconcile",
        ts: 30,
      },
      { id: "db-final", role: "assistant", text: "检查完成", run_id: "run-reconcile", ts: 40 },
    ], []);

    const messages = useSessionsStore.getState().sessions[sid].messages;
    expect(messages.map((message) => message.id)).toEqual([
      "db-user",
      "db-call",
      "db-result",
      "db-final",
    ]);
  });

  it("preserves real repeated messages and does not merge across distinct runs", () => {
    const sid = "history-multiset-reconcile";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.push_message(sid, { id: "live-1", role: "assistant", text: "仍在检查", run_id: "run-a" });
    store.push_message(sid, { id: "live-2", role: "assistant", text: "仍在检查", run_id: "run-a" });
    store.push_message(sid, { id: "other-run", role: "assistant", text: "仍在检查", run_id: "run-b" });

    store.merge_history_messages(sid, [
      { id: "db-1", role: "assistant", text: "仍在检查", run_id: "run-a", ts: 10 },
      { id: "db-2", role: "assistant", text: "仍在检查", run_id: "run-a", ts: 20 },
    ], []);

    const messages = useSessionsStore.getState().sessions[sid].messages;
    expect(messages.filter((message) => message.run_id === "run-a")).toHaveLength(2);
    expect(messages.filter((message) => message.run_id === "run-b")).toHaveLength(1);
    expect(messages.some((message) => message.id === "live-1")).toBe(false);
    expect(messages.some((message) => message.id === "live-2")).toBe(false);
    expect(messages.some((message) => message.id === "other-run")).toBe(true);
  });

  it("persists completed deep_research/v2 stages with only allowlisted metadata", () => {
    const sid = "research-stage";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    const event = deepResearchStage(2, "search", "research-stage-run", {
      metrics: {
        providers: 3,
        candidates: 18,
        kept: 11,
        prompt: "must-not-cross-the-boundary",
        cookie: "secret",
      },
    });
    store.reduce_workflow_event(sid, event);

    const messages = useSessionsStore.getState().sessions[sid].messages;
    expect(messages.filter((message) => message.role === "workflow_progress")).toHaveLength(1);
    expect(messages.filter((message) => message.role === "workflow_stage")).toHaveLength(1);
    expect(messages.find((message) => message.role === "workflow_stage")).toMatchObject({
      id: `workflow-stage:${event.event_id}`,
      workflow_run_id: "research-stage-run",
      workflow_version: "v2",
      workflow_stage_id: "search",
      workflow_seq: 2,
      workflow_metrics: { providers: 3, candidates: 18, kept: 11 },
      workflow_duration_ms: 1240,
      workflow_next_stage: "direct",
      workflow_action: "搜索可用资料来源",
      workflow_result: "尝试 3 个来源，找到 18 条候选，保留 11 条",
      workflow_result_code: "stage_ok",
    });
    expect(JSON.stringify(messages)).not.toContain("must-not-cross-the-boundary");
    expect(JSON.stringify(messages)).not.toContain("secret");
  });

  it("accepts v3 children by deep-research schema and stage allowlist", () => {
    const sid = "research-v3-stage";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.reduce_workflow_event(sid, deepResearchStage(4, "cite", "research-v3-run", {
      workflow_version: "v3",
      stage: "核验论断与引用",
      action: "核验论断与引用",
      result: "发布 9 条，丢弃 3 条，修复 2 条，支持率 75%",
      result_code: "stage_degraded",
      diagnostic_codes: ["claim_pruned", "provider_degraded", "secret_query"],
      published: 9,
      discarded: 3,
      repaired: 2,
      degraded: true,
      metrics: {
        published: 9,
        discarded: 3,
        repaired: 2,
        support_rate: 0.75,
        prompt: "must-not-cross-the-boundary",
      },
    }));

    const child = useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_stage",
    );
    expect(child).toMatchObject({
      workflow_version: "v3",
      workflow_stage_id: "cite",
      workflow_result_code: "stage_degraded",
      workflow_diagnostic_codes: ["claim_pruned", "provider_degraded"],
      workflow_published: 9,
      workflow_discarded: 3,
      workflow_repaired: 2,
      workflow_metrics: { published: 9, discarded: 3, repaired: 2, support_rate: 0.75 },
    });
    expect(JSON.stringify(child)).not.toContain("secret_query");
    expect(JSON.stringify(child)).not.toContain("must-not-cross-the-boundary");
  });

  it("keeps production v3 branch and publish-gate diagnostics while dropping unknown codes", () => {
    const sid = "research-v3-diagnostics";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.reduce_workflow_event(sid, deepResearchStage(8, "cite", "research-v3-diag-run", {
      workflow_version: "v3",
      result_code: "insufficient_evidence",
      diagnostic_codes: [...PRODUCTION_V3_DIAGNOSTICS, "secret_query"],
      degraded: true,
    }));

    const child = useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_stage",
    );
    expect(child?.workflow_diagnostic_codes).toEqual([...PRODUCTION_V3_DIAGNOSTICS].sort());
    expect(JSON.stringify(child)).not.toContain("secret_query");
  });

  it("projects v3 started summary fields for the current timeline row", () => {
    const sid = "research-v3-current";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.reduce_workflow_event(sid, workflowEvent(3, "workflow.progress", {
      schema_version: 2,
      kind: "progress",
      workflow_name: "deep_research",
      workflow_version: "v3",
      workflow_label: "深度调研",
      stage_id: "fetch",
      stage: "抓取正文",
      ordinal: 6,
      total: 13,
      status: "started",
      action: "抓取并提取来源正文",
      result: "深度调研进度：抓取正文（6/13）",
      result_code: "started",
    }, "research-v3-current-run"));

    expect(useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_progress",
    )).toMatchObject({
      workflow_version: "v3",
      workflow_stage_id: "fetch",
      workflow_action: "抓取并提取来源正文",
      workflow_result_code: "started",
    });
  });

  it("projects only allowlisted v4 terminal coverage, skipped stages, and retry action", () => {
    const sid = "research-v4-failed";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.reduce_workflow_event(sid, workflowEvent(9, "workflow.final", {
      workflow_name: "deep_research",
      workflow_version: "v4",
      status: "failed",
      terminal_error: { code: "insufficient_evidence", message: "coverage too low" },
      metrics: {
        actual_requests: 7,
        hits: 2,
        empty: 3,
        timeouts: 1,
        cooldown_skips: 4,
        probes: 2,
        prompt: "must-not-cross-the-boundary",
      },
      skipped_stage_ids: ["fetch", "score", "score", "unknown", 9],
      retry_action_id: "retry_from_start",
    }, "research-v4-failed-run"));

    const summary = useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_progress",
    );
    expect(summary).toMatchObject({
      workflow_version: "v4",
      workflow_status: "failed",
      workflow_error: "coverage too low",
      workflow_metrics: {
        actual_requests: 7,
        hits: 2,
        empty: 3,
        timeouts: 1,
        cooldown_skips: 4,
        probes: 2,
      },
      workflow_skipped_stage_ids: ["fetch", "score"],
      workflow_retry_action_id: "retry_from_start",
    });
    expect(JSON.stringify(summary)).not.toContain("must-not-cross-the-boundary");
    expect(JSON.stringify(summary)).not.toContain("unknown");
  });

  it("never promotes legacy recovery text or an unknown action into a retry action", () => {
    const store = useSessionsStore.getState();
    for (const [sid, version, retryAction] of [
      ["research-v3-recovery", "v3", "retry_from_start"],
      ["research-v4-unknown-retry", "v4", "retry_with_query"],
    ] as const) {
      store.ensure(sid);
      store.reduce_workflow_event(sid, workflowEvent(2, "workflow.final", {
        workflow_name: "deep_research",
        workflow_version: version,
        status: "failed",
        recovery_action: "retry_from_start",
        retry_action_id: retryAction,
      }, `${sid}-run`));
      const summary = useSessionsStore.getState().sessions[sid].messages.find(
        (message) => message.role === "workflow_progress",
      );
      expect(summary?.workflow_retry_action_id).toBeUndefined();
    }
  });

  it("keeps a late stage child after terminal summary and dedupes by event id or seq", () => {
    const sid = "research-terminal";
    const runId = "research-terminal-run";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.reduce_workflow_event(sid, workflowEvent(
      10,
      "workflow.final",
      { workflow_name: "深度调研", status: "completed" },
      runId,
    ));
    const late = deepResearchStage(7, "gap", runId, {
      stage: "补充证据",
      metrics: { iteration: 1, followup_count: 2, new_evidence: 4 },
    });
    store.reduce_workflow_event(sid, late);
    store.reduce_workflow_event(sid, { ...late, seq: 8 });
    store.reduce_workflow_event(sid, {
      ...late,
      event_id: `${runId}-different-event-same-seq`,
    });

    const messages = useSessionsStore.getState().sessions[sid].messages;
    expect(messages.filter((message) => message.role === "workflow_stage")).toHaveLength(1);
    expect(messages.find((message) => message.role === "workflow_progress")).toMatchObject({
      workflow_status: "completed",
      workflow_terminal: true,
      workflow_seq: 10,
    });
  });

  it("preserves a stage that arrives before its lower-sequence summary event", () => {
    const sid = "research-stage-first";
    const runId = "research-stage-first-run";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.reduce_workflow_event(sid, deepResearchStage(2, "normalize", runId, {
      stage: "理解问题",
      completed_count: 1,
      metrics: { mode: "deep" },
    }));
    store.reduce_workflow_event(sid, workflowEvent(
      1,
      "workflow.accepted",
      { workflow_name: "深度调研" },
      runId,
    ));

    const messages = useSessionsStore.getState().sessions[sid].messages;
    expect(messages.filter((message) => message.role === "workflow_stage")).toHaveLength(1);
    expect(messages.filter((message) => message.role === "workflow_progress")).toHaveLength(1);
  });

  it("keeps v1 workflow progress summary-only", () => {
    const sid = "research-v1";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.reduce_workflow_event(sid, workflowEvent(2, "workflow.progress", {
      schema_version: 1,
      kind: "stage",
      workflow_name: "deep_research",
      workflow_version: "v1",
      stage: "搜索资料",
      status: "completed",
    }, "research-v1-run"));
    const messages = useSessionsStore.getState().sessions[sid].messages;
    expect(messages.filter((message) => message.role === "workflow_progress")).toHaveLength(1);
    expect(messages.some((message) => message.role === "workflow_stage")).toBe(false);
  });

  it("keeps v7 directions independent from out-of-order stage and final events", () => {
    const sid = "research-v7-out-of-order";
    const runId = "research-v7-out-of-order-run";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.reduce_workflow_event(sid, deepResearchV7Children(10, ["running", "running"], runId));
    store.reduce_workflow_event(sid, workflowEvent(12, "workflow.progress", {
      schema_version: 7,
      kind: "progress",
      workflow_name: "deep_research",
      workflow_version: "v7",
      workflow_label: "深度调研",
      stage_id: "synth",
      stage: "综合调研结果",
      ordinal: 4,
      total: 6,
      status: "started",
      text: "正在综合",
    }, runId));
    store.reduce_workflow_event(sid, deepResearchV7Children(11, ["valid", "valid"], runId));

    let summary = useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_progress",
    );
    expect(summary).toMatchObject({
      workflow_seq: 12,
      workflow_v7_children_seq: 11,
      workflow_terminal: false,
    });
    expect(summary?.workflow_v7_children?.map((item) => item.status)).toEqual(["valid", "valid"]);

    store.reduce_workflow_event(sid, workflowEvent(20, "workflow.final", {
      workflow_name: "deep_research",
      workflow_version: "v7",
      status: "completed",
    }, runId));
    store.reduce_workflow_event(sid, deepResearchV7Children(19, ["valid", "insufficient"], runId));
    store.reduce_workflow_event(sid, deepResearchV7Children(9, ["running", "running"], runId));

    summary = useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_progress",
    );
    expect(summary).toMatchObject({
      workflow_seq: 20,
      workflow_v7_children_seq: 19,
      workflow_status: "completed",
      workflow_terminal: true,
    });
    expect(summary?.workflow_v7_children?.map((item) => item.status)).toEqual(["valid", "insufficient"]);
  });

  it("rehydrates durable stage children from history idempotently", () => {
    const sid = "research-history";
    const runId = "research-history-run";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    const child = deepResearchStage(2, "search", runId);
    const final = workflowEvent(
      3,
      "workflow.final",
      { workflow_name: "深度调研", status: "completed" },
      runId,
    );
    store.merge_history_messages(sid, [], [child, final]);
    store.merge_history_messages(sid, [], [child, final]);

    const messages = useSessionsStore.getState().sessions[sid].messages;
    expect(messages.filter((message) => message.role === "workflow_stage")).toHaveLength(1);
    expect(messages.filter((message) => message.role === "workflow_progress")).toHaveLength(1);
    expect(messages.find((message) => message.role === "workflow_progress")).toMatchObject({
      workflow_status: "completed",
      workflow_terminal: true,
      workflow_seq: 3,
    });
  });
});

describe("durable workflow decisions", () => {
  it("rehydrates an actionable PPT outline card from a decision event", () => {
    const store = useSessionsStore.getState();
    store.ensure("decision-session");
    const handled = store.reduce_workflow_event(
      "decision-session",
      workflowEvent(7, "workflow.decision", {
        status: "open",
        decision_id: "decision-1",
        decision_kind: "ppt_outline",
        nonce: "nonce-1",
        version: 4,
        prompt: {
          kind: "ppt_outline",
          outline_id: "workflow:run-1:0",
          topic: "Native workflow",
          outline_markdown: "# Slide 1",
          sources_count: 2,
          no_research: false,
        },
      }),
    );

    expect(handled).toBe(true);
    expect(useSessionsStore.getState().sessions["decision-session"].messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          role: "ppt_outline",
          outline_id: "workflow:run-1:0",
          outline_md: "# Slide 1",
          ppt_outline_awaiting: true,
          ppt_run_id: "run-1",
          ppt_decision_id: "decision-1",
          ppt_nonce: "nonce-1",
          ppt_version: 4,
        }),
      ]),
    );
  });

  it("renders stable provider balance failures as a user-readable message", () => {
    const sid = "durable-task-provider-balance";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.reduce_workflow_event(sid, workflowEvent(6, "workflow.final", {
      workflow_name: "durable_task",
      workflow_version: "v1",
      status: "failed",
      terminal_error: {
        code: "permanent",
        message_ref: "provider:insufficient_balance",
      },
    }, "durable-task-provider-balance-run"));

    const summary = useSessionsStore.getState().sessions[sid].messages.find(
      (message) => message.role === "workflow_progress",
    );
    expect(summary?.workflow_error).toBe(
      "模型服务余额不足，请充值或切换模型后重试。",
    );
  });

  it("binds workflow summaries and stages to their owning root Run", () => {
    const sid = "workflow-root-binding";
    const event = {
      ...deepResearchStage(2, "search", "child-workflow"),
      root_run_id: "root-owner",
      task_scope_id: "task-owner",
    };

    useSessionsStore.getState().reduce_workflow_event(sid, event);

    const workflowMessages =
      useSessionsStore.getState().sessions[sid].messages.filter(
        (message) =>
          message.role === "workflow_progress" ||
          message.role === "workflow_stage",
      );
    expect(workflowMessages).toHaveLength(2);
    expect(workflowMessages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          run_id: "root-owner",
          task_scope_id: "task-owner",
          workflow_run_id: "child-workflow",
        }),
      ]),
    );
    expect(workflowMessages.every(
      (message) =>
        message.run_id === "root-owner" &&
        message.task_scope_id === "task-owner",
    )).toBe(true);
  });
});

describe("single-session task run projections", () => {
  it("settles one of three interleaved runs without clearing the others", () => {
    const sid = "parallel-main-session";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    for (let index = 1; index <= 3; index += 1) {
      store.upsert_run_projection(sid, `run-${index}`, {
        task_scope_id: `scope-${index}`,
        version: 0,
        status: "running",
        inflight: true,
        ui_state: index === 1 ? "open" : "background",
        started_at: index,
      });
    }

    store.upsert_run_projection(sid, "run-2", {
      status: "completed",
      inflight: false,
    });

    const session = useSessionsStore.getState().sessions[sid];
    expect(session.inflight).toBe(true);
    expect(session.run_projections["run-1"].inflight).toBe(true);
    expect(session.run_projections["run-2"].status).toBe("completed");
    expect(session.run_projections["run-3"].inflight).toBe(true);
  });

  it("switching and closing a projection never cancels its run", () => {
    const sid = "projection-only-session";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.upsert_run_projection(sid, "run-a", {
      task_scope_id: "scope-a",
      version: 0,
      status: "running",
      inflight: true,
      ui_state: "open",
    });
    store.upsert_run_projection(sid, "run-b", {
      task_scope_id: "scope-b",
      version: 0,
      status: "waiting",
      inflight: false,
      ui_state: "background",
    });

    store.select_run_projection(sid, "run-b");
    store.close_run_projection(sid, "run-b");

    const session = useSessionsStore.getState().sessions[sid];
    expect(session.run_projections["run-a"].status).toBe("running");
    expect(session.run_projections["run-a"].inflight).toBe(true);
    expect(session.run_projections["run-b"].status).toBe("waiting");
    expect(session.run_projections["run-b"].ui_state).toBe("closed");
    expect(session.inflight).toBe(true);
  });

  it("binds a pending local message to the authoritative started run", () => {
    const sid = "request-binding-session";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.push_message(sid, {
      role: "user",
      text: "prepare Godot",
      request_id: "request-1",
    });

    store.upsert_run_projection(sid, "root-1", {
      task_scope_id: "scope-1",
      request_id: "request-1",
      version: 0,
      status: "running",
      inflight: true,
      ui_state: "open",
    });

    expect(
      useSessionsStore.getState().sessions[sid].messages.at(-1),
    ).toMatchObject({
      request_id: "request-1",
      run_id: "root-1",
      task_scope_id: "scope-1",
    });
  });
});
