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
      ...overrides,
    },
  };
}

function mk(over: Partial<SessionState>): SessionState {
  return {
    base_session_id: "sid",
    code_session_id: "code-x",
    project_root: "/tmp/x",
    project_name: "x",
    messages: [],
    todos: [],
    token_usage: { prompt: 0, completion: 0 },
    status: "running",
    last_activity: Date.now(),
    inflight: false,
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
            code_session_id: null,
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

  it("companion-mode sids without project_root or code_session_id are excluded", () => {
    const sids = {
      default: mk({
        base_session_id: "default",
        code_session_id: null,
        project_root: null,
        status: "error", // would score 60 if eligible
      }),
      "code-real": mk({
        base_session_id: "code-real",
        status: "running", // 10
      }),
    };
    expect(pet_focus_sid(sids, now)).toBe("code-real");
  });
});

describe("workflow progress reducer (AC-23)", () => {
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
    });
    expect(JSON.stringify(messages)).not.toContain("must-not-cross-the-boundary");
    expect(JSON.stringify(messages)).not.toContain("secret");
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
        }),
      ]),
    );
  });
});
