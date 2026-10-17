// chat 车道画廊夹具：消息、工作流进度、执行步骤、产物、会话列表等固定数据（不连后端）。
// 画廊固定"现在" = 2026-10-01T08:00:00Z（NOW）。
import type { Message, WorkflowV5ProgressProjection } from "../../../src/stores/sessionTypes";
import type { CapabilityOperation } from "../../../src/types/capabilities";
import type { WorkflowTaskTrace } from "../../../src/components/AgentActivityMessage";
import type { ToolArtifact } from "../../../src/code-panel/artifactExtract";
import type { ChatStreamMessage } from "../../../src/components/messageStreamTypes";
import type { InboxItem } from "../../../src/stores/sessionSelectors";
import type {
  ProjectDescriptor,
  ProjectSessionDescriptor,
} from "../../../src/types/projectSessions";

export const NOW = 1790841600000;
const MIN = 60_000;

// ── Markdown 文本（表格/列表/代码块/链接/行内代码） ─────────────────
export const MARKDOWN_RICH = [
  "## 调研结论",
  "",
  "这是**加粗**与`行内代码`，还有 [官网链接](https://example.com/docs) 和 [本地报告](/Users/demo/report.pdf)。",
  "",
  "- 第一条要点",
  "- 第二条要点",
  "",
  "1. 有序一",
  "2. 有序二",
  "",
  "| 指标 | 数值 |",
  "| --- | --- |",
  "| 命中 | 12 |",
  "| 超时 | 1 |",
  "",
  "```python",
  "def hello(name):",
  '    return f"你好 {name}"',
  "```",
  "",
  "```",
  "无语言代码块",
  "```",
].join("\n");

// ── 产物 ───────────────────────────────────────────────────────────
export const ARTIFACTS: ToolArtifact[] = [
  {
    kind: "file",
    path: "/Users/demo/out/季度汇报.pptx",
    mime: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    title: "季度汇报.pptx",
    size_bytes: 2_456_789,
  },
  {
    kind: "file",
    path: "/Users/demo/out/明细.xlsx",
    mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    title: "明细.xlsx",
    size_bytes: 20_480,
    actions: [
      { id: "open", label: "打开" },
      { id: "copy_path" },
    ],
  },
  { kind: "file", path: "/Users/demo/out/说明.pdf", mime: "application/pdf", title: "说明.pdf", size_bytes: 512 },
  { kind: "file", path: "/Users/demo/out/合同.docx", mime: "application/msword", title: "合同.docx" },
  { kind: "file", path: "", mime: null, title: "未知文件" },
  { kind: "url", url: "https://example.com/report", title: "在线报告" },
  { kind: "url", url: "https://example.com/raw", title: "" },
  { kind: "text", title: "摘要.txt", preview: "第一行摘要\n第二行摘要，内容较长时会自动换行显示。" },
  { kind: "image", path: "/Users/demo/out/图表.png", mime: "image/png", title: "图表.png", size_bytes: 88_000 },
  { kind: "image", path: "/Users/demo/out/截图.jpg", title: "截图.jpg" },
  { kind: "table", title: "数据表", preview: "列A,列B,列C\n1,2,3\n4,5,6" },
  { kind: "table", title: "空表" },
  { kind: "presentation" as unknown as ToolArtifact["kind"], path: "/x/y.key", title: "未知种类" },
];

export const ARTIFACT_RESULT = JSON.stringify({ ok: true, artifacts: ARTIFACTS.slice(0, 3) });
export const HINT_RESULT = JSON.stringify({
  ok: false,
  error: "文件不存在：/tmp/missing.txt",
  hint: "先用 list_dir 确认路径，再重试。",
  examples: ["list_dir /tmp"],
});

// ── PPT 大纲 ───────────────────────────────────────────────────────
export const PPT_HISTORY = [
  { outline_id: "o-old-1", topic: "旧版大纲", created_at: "2026-09-30T08:00:00Z", sources_count: 4, status: "superseded" },
  { outline_id: "o-old-2", topic: "", created_at: "", sources_count: 0, status: "superseded" },
];
export const PPT_OUTLINE_MD = "# 年度回顾\n\n## 一、背景\n- 市场变化\n- 团队成长\n\n## 二、成果\n1. 收入增长\n2. 新产品上线";

// ── 会话消息（MessageBubble / ChatView） ────────────────────────────
export function bubbleMessages(sid: string): Message[] {
  return [
    { id: "b-user", role: "user", text: "帮我整理一份季度汇报\n第二行需求", ts: NOW - 30 * MIN },
    { id: "b-asst", role: "assistant", text: `<think>先确认数据来源</think>${MARKDOWN_RICH}`, ts: NOW - 29 * MIN },
    { id: "b-delta", role: "assistant_delta", text: "正在生成第二部分内容", ts: NOW - 28 * MIN },
    { id: "b-delta-think", role: "assistant_delta", text: "<think>还在思考结构", ts: NOW - 28 * MIN },
    { id: "b-reason", role: "reasoning_delta", text: "考虑按时间顺序组织", ts: NOW - 27 * MIN },
    {
      id: "b-plan",
      role: "plan",
      plan_rationale: "先收集数据再生成文件",
      plan_steps: [
        { title: "收集数据", detail: "读取销售表" },
        { title: "生成汇报", detail: "" },
      ],
      plan_target_directory: "/Users/demo/out",
      plan_action_categories: ["filesystem_read", "filesystem_write", "custom_action"],
      plan_awaiting_confirm: true,
      plan_sid: sid,
      ts: NOW - 26 * MIN,
    },
    { id: "b-plan-auto", role: "plan", plan_steps: [{ title: "自动步骤", detail: "无需确认" }], plan_auto_confirmed: true, ts: NOW - 26 * MIN },
    { id: "b-plan-manual", role: "plan", plan_steps: [], ts: NOW - 26 * MIN },
    {
      id: "b-ppt",
      role: "ppt_outline",
      outline_id: "o-bubble",
      topic: "年度回顾",
      outline_md: PPT_OUTLINE_MD,
      sources_count: 3,
      no_research: true,
      history: PPT_HISTORY,
      ppt_outline_awaiting: true,
      ts: NOW - 25 * MIN,
    },
    { id: "b-call", role: "tool_call", tool_name: "read_file", tool_args: { path: "/Users/demo/data.csv", _project_root: "/x", options: { encoding: "utf-8", long: "x".repeat(80) } }, ts: NOW - 24 * MIN },
    { id: "b-call-empty", role: "tool_call", tool_name: "list_dir", tool_args: {}, ts: NOW - 24 * MIN },
    { id: "b-result", role: "tool_result", tool_name: "read_file", tool_ok: true, tool_result: "a,b\n1,2\n3,4", ts: NOW - 23 * MIN },
    { id: "b-result-hint", role: "tool_result", tool_name: "read_file", tool_ok: false, tool_result: HINT_RESULT, ts: NOW - 23 * MIN },
    { id: "b-result-fail", role: "tool_result", tool_name: "shell", tool_ok: false, tool_result: "exit 1", ts: NOW - 23 * MIN },
    { id: "b-result-art", role: "tool_result", tool_name: "ppt_create", tool_ok: true, tool_result: ARTIFACT_RESULT, ts: NOW - 22 * MIN },
    { id: "b-slash", role: "slash_result", text: "/help\n  /goal  设定目标\n  /prefs 偏好", ts: NOW - 21 * MIN },
    { id: "b-error", role: "error", text: "模型调用失败：429", ts: NOW - 20 * MIN },
    { id: "b-summary", role: "reasoning_summary", text: "不渲染的角色", ts: NOW - 20 * MIN },
  ];
}

// ── 工作流进度 ─────────────────────────────────────────────────────
export function wfSummary(overrides: Partial<Message> = {}): Message {
  return {
    id: "workflow-run:run-group",
    role: "workflow_progress",
    ts: NOW - 5 * MIN,
    workflow_run_id: "run-group",
    workflow_name: "深度调研",
    workflow_version: "v2",
    workflow_status: "running",
    workflow_stage: "搜索资料",
    workflow_total: 13,
    workflow_completed_count: 4,
    workflow_elapsed_ms: 62000,
    workflow_seq: 9,
    ...overrides,
  };
}

export function wfStage(seq: number, stageId: string, overrides: Partial<Message> = {}): Message {
  return {
    id: `workflow-stage:event-${seq}`,
    role: "workflow_stage",
    text: `阶段 ${stageId} 已完成`,
    ts: NOW - (20 - seq) * MIN,
    workflow_run_id: "run-group",
    workflow_name: "深度调研",
    workflow_version: "v2",
    workflow_status: "completed",
    workflow_stage: stageId,
    workflow_stage_id: stageId,
    workflow_stage_instance_id: `${stageId}-${seq}`,
    workflow_transition: "completed",
    workflow_seq: seq,
    workflow_event_id: `event-${seq}`,
    workflow_total: 13,
    workflow_duration_ms: 1500,
    workflow_action: `执行 ${stageId}`,
    workflow_result: `完成 ${stageId}，保留有效结果`,
    ...overrides,
  };
}

export function v5Projection(
  overrides: Partial<WorkflowV5ProgressProjection> = {},
): WorkflowV5ProgressProjection {
  return {
    action: "evaluate_gaps",
    result: "completed",
    discarded: 2,
    remaining_gap: 1,
    next_step: "research_gap",
    dimension_counts: {
      total: 5,
      core_total: 3,
      covered: 3,
      partially_covered: 1,
      uncovered: 1,
      not_applicable: 0,
      core_covered: 2,
      core_partially_covered: 1,
      core_uncovered: 0,
    },
    dimension_status_changes: { improved: 1, regressed: 0, unchanged: 4 },
    source_counts: { valid: 8, first_party: 3 },
    active_gap: { status: "running", work_kind: "query", dimension_ordinal: 2 },
    elapsed_seconds: 92,
    soft_checkpoint: "reached",
    lease_reason: "lease_renewed_measurable_gain",
    quality_score: 87,
    hard_failures: [],
    predicted_delivery: "partial",
    token_budget_ratio: 42,
    control_action: "generate_now",
    control_status: "open",
    parent_operation: "none",
    failed_dimensions: [],
    rejection_reasons: [],
    ...overrides,
  } as WorkflowV5ProgressProjection;
}

export function capabilityOperation(): CapabilityOperation {
  return {
    operation_id: "operation-workflow",
    capability_id: "godot",
    capability_name: "Godot",
    kind: "build",
    phase: "verified",
    status: "running",
    authorization_mode: "auto",
    current_validation: "验证 Godot 能力包",
    latest_result: "清单哈希有效",
    available_actions: ["cancel"],
    artifacts: [],
    verification_receipts: [],
  } as CapabilityOperation;
}

export const V7_CHILDREN: NonNullable<Message["workflow_v7_children"]> = [
  { child_id: "dr-0", question: "运行时架构是什么？", status: "valid", attempt: 1, max_attempts: 2, n_sources: 2 },
  { child_id: "dr-1", question: "核心组件有哪些？", status: "queued", attempt: 0, max_attempts: 2, n_sources: 0 },
  { child_id: "dr-2", question: "适用场景是什么？", status: "insufficient", attempt: 2, max_attempts: 2, n_sources: 0 },
  { child_id: "dr-3", question: "常见陷阱有哪些？", status: "running", attempt: 1, max_attempts: 2, n_sources: 0 },
  { child_id: "dr-4", question: "替代方案对比？", status: "retrying", attempt: 2, max_attempts: 2, n_sources: 1 },
] as NonNullable<Message["workflow_v7_children"]>;

export function delivery(status: string) {
  return {
    schema_version: 1,
    run_id: "run-group",
    manifest_ref: "manifest-v6",
    status,
    required_total: 1,
    pending: 0,
    delivering: 0,
    delivered: 0,
    retrying: 1,
    fenced: 0,
    failed: 0,
    updated_at: 10,
  } as unknown as NonNullable<Message["workflow_delivery"]>;
}

/** 有详情的阶段集合：完成、降级、带指标/v5 五字段/下一步。 */
export function richStages(runId: string): Message[] {
  const base = { workflow_run_id: runId };
  return [
    wfStage(2, "search", {
      ...base,
      workflow_metrics: { providers: 3, candidates: 18, hits: 12, prompt: "不渲染" },
      workflow_next_stage: "fetch",
    }),
    wfStage(3, "fetch", {
      ...base,
      workflow_degraded: true,
      workflow_diagnostic_codes: ["timeout", "partial_results", "unknown_code"],
      workflow_error: "部分来源超时",
      workflow_duration_ms: 75_000,
    }),
    wfStage(4, "gap_evaluate", {
      ...base,
      workflow_version: "v5",
      workflow_v5: v5Projection({ control_action: "none" }),
      workflow_duration_ms: undefined,
    }),
    wfStage(5, "score", {
      ...base,
      workflow_v5: v5Projection({ discarded: "none" as never, remaining_gap: "none" as never, next_step: "none" as never }),
      workflow_result: undefined,
      text: "",
    }),
  ];
}

// ── 执行步骤（DurableTaskSteps） ───────────────────────────────────
export function taskTrace(runId: string, overrides: Partial<WorkflowTaskTrace> = {}): WorkflowTaskTrace {
  const tool = (id: string, extra: Record<string, unknown>) => ({
    id,
    callId: `call-${id}`,
    name: "read_file",
    action: "读取",
    target: "data.csv",
    status: "completed",
    detailsAvailable: true,
    resultAvailable: false,
    truncated: false,
    ts: NOW - 10 * MIN,
    ...extra,
  });
  return {
    runId,
    status: "running",
    startedAt: NOW - 12 * MIN,
    projectionComplete: true,
    steps: [
      { id: "s-plan", index: 0, title: "理解需求并制定计划", status: "completed", current: false, messages: ["已确认目标"], tools: [], substeps: [] },
      {
        id: "s-work",
        index: 1,
        title: "收集数据并生成报告（标题较长时省略显示）",
        status: "running",
        current: true,
        messages: ["正在读取销售数据", "第二条说明\n带换行"],
        substeps: [
          { id: "ss-1", label: "读取原始数据", status: "completed", current: false, index: 0 },
          { id: "ss-2", label: "清洗数据", status: "running", current: true, index: 1 },
          { id: "ss-3", label: "汇总统计", status: "pending", current: false, index: 2 },
          { id: "ss-4", label: "未知状态", status: "odd", current: false, index: 3 },
        ],
        tools: [
          tool("t-ok", { ok: true, durationMs: 420, resultAvailable: true }),
          tool("t-fail", { ok: false, status: "failed", durationMs: 2300, target: undefined }),
          tool("t-run", { status: "running", action: "", target: "" }),
          tool("t-cancel", { status: "cancelled", ok: false }),
          tool("t-reject", { status: "rejected" }),
        ],
      },
      { id: "s-empty", index: 2, title: "空步骤", status: "pending", current: true, messages: [], tools: [], substeps: [] },
      { id: "s-wait", index: 3, title: "等待确认", status: "waiting", current: false, messages: [], tools: [tool("t-w", {})], substeps: [] },
      { id: "s-fail", index: 4, title: "失败步骤", status: "failed", current: false, messages: [], tools: [], substeps: [] },
      { id: "s-cancel", index: 5, title: "已取消步骤", status: "cancelled", current: false, messages: [], tools: [], substeps: [] },
      { id: "s-rec", index: 6, title: "修复后完成", status: "completed_with_recovery", current: false, messages: [], tools: [], substeps: [] },
      { id: "s-odd", index: 7, title: "未知状态步骤", status: "mystery", current: false, messages: [], tools: [], substeps: [] },
    ],
    ...overrides,
  } as WorkflowTaskTrace;
}

// ── 消息流（MessageStreamPanel / ChatRow） ──────────────────────────
export function streamMessages(): ChatStreamMessage[] {
  return [
    { role: "user", text: "请帮我调研一下行业现状", ts: NOW - 40 * MIN, continuationStatus: "waiting" },
    { role: "user", text: "补充：重点看国内", ts: NOW - 39 * MIN, continuationStatus: "bound" },
    { role: "user", text: "再补充一条", ts: NOW - 38 * MIN, continuationStatus: "failed", continuationError: "任务已结束" },
    { role: "user", text: "", ts: NOW - 38 * MIN },
    { role: "assistant", text: MARKDOWN_RICH, ts: NOW - 37 * MIN },
    { role: "assistant", text: "", ts: NOW - 37 * MIN },
    { role: "tool", text: "🔧 调用 read_file", ts: NOW - 36 * MIN, toolName: "read_file", toolArgs: { path: "/a.csv" } },
    { role: "tool", text: "✅ ppt_create 完成", ts: NOW - 35 * MIN, toolName: "ppt_create", toolOk: true, toolResultRaw: ARTIFACT_RESULT },
    { role: "tool", text: "✅ deepresearch 完成", ts: NOW - 35 * MIN, toolName: "deepresearch", toolOk: true, toolResultRaw: JSON.stringify({ artifacts: [ARTIFACTS[2]] }) },
    { role: "tool", text: "✅ search 完成", ts: NOW - 34 * MIN, toolName: "search", toolOk: true, toolResultRaw: "共找到 3 条结果" },
    { role: "tool", text: "❌ read_file 完成", ts: NOW - 34 * MIN, toolName: "read_file", toolOk: false, toolResultRaw: HINT_RESULT },
    { role: "tool", text: "工具轨迹：正在执行 shell", ts: NOW - 33 * MIN, toolName: "shell" },
    { role: "tool", text: "", ts: NOW - 33 * MIN },
    { role: "tool", text: "✅ mission_start 完成", ts: NOW - 33 * MIN, toolName: "mission_start", toolOk: true, toolResultRaw: JSON.stringify({ mission_id: "m-gallery" }) },
    { role: "progress", text: "**观察**：已获取 3 个来源", ts: NOW - 32 * MIN, phase: "observation" },
    { role: "progress", text: "状态：正在整理", ts: NOW - 32 * MIN, phase: "status" },
    { role: "progress", text: "计划：先搜索后汇总", ts: NOW - 32 * MIN, phase: "planning" },
    { role: "progress", text: "准备使用 search 处理当前步骤。", ts: NOW - 31 * MIN },
    // 运行中的思考组（展开）
    { role: "progress", text: "正在规划步骤", ts: NOW - 30 * MIN, runId: "run-think", phase: "planning", status: "running" },
    { role: "tool", text: "🔧 调用 search", ts: NOW - 29 * MIN, runId: "run-think", toolName: "search", toolArgs: { q: "行业" } },
    { role: "tool", text: "✅ search 完成", ts: NOW - 28 * MIN, runId: "run-think", toolName: "search", toolOk: true, toolResultRaw: "结果" },
    // 已完成的思考组（收起）
    { role: "progress", text: "已完成规划", ts: NOW - 27 * MIN, runId: "run-done", status: "completed" },
    { role: "tool", text: "✅ fetch 完成", ts: NOW - 26 * MIN, runId: "run-done", toolName: "fetch", toolOk: true, toolResultRaw: "ok" },
    {
      role: "ppt_outline",
      session_id: "sid-stream",
      ts: NOW - 25 * MIN,
      message: {
        id: "ppt-stream",
        role: "ppt_outline",
        outline_id: "o-stream",
        topic: "行业报告",
        outline_md: PPT_OUTLINE_MD,
        sources_count: 5,
        ppt_outline_awaiting: false,
        ppt_outline_decision_status: "已确认生成",
        ts: NOW - 25 * MIN,
      },
    },
    {
      role: "workflow_progress",
      ts: NOW - 24 * MIN,
      message: wfSummary({ workflow_run_id: "run-stream", id: "wf-stream", workflow_status: "running" }),
    },
    {
      role: "workflow_stage",
      ts: NOW - 23 * MIN,
      message: wfStage(2, "search", { workflow_run_id: "run-stream" }),
    },
  ];
}

export function inboxItem(id: string, severity: "yellow" | "red", extra: Partial<InboxItem> = {}): InboxItem {
  return {
    alert_id: id,
    severity,
    action: "ask_user",
    diagnosis: "工具反复失败",
    user_message: severity === "yellow" ? "任务似乎卡住了，要继续吗？" : "",
    suggested_buttons: ["继续", "停止", "第三个不显示"],
    received_at: NOW - 3 * MIN,
    session_id: "sid-stream",
    project_name: "simple_harness",
    ...extra,
  };
}

// ── 会话列表 ───────────────────────────────────────────────────────
export const PROJECTS: ProjectDescriptor[] = [
  {
    project_id: "p1",
    display_name: "simple_harness",
    project_root: "/repo",
    root_kind: "git",
    project_revision: 2,
    availability: "missing",
    created_at: 1,
    updated_at: 2,
    last_opened_at: 3,
  },
  {
    project_id: "p2",
    display_name: "一个名字非常非常长的项目目录用于测试省略号显示效果",
    project_root: "/work/long",
    root_kind: "folder",
    project_revision: 1,
    availability: "available",
    created_at: 1,
    updated_at: 2,
    last_opened_at: 3,
  },
];

function session(id: string, extra: Partial<ProjectSessionDescriptor>): ProjectSessionDescriptor {
  return {
    session_id: id,
    project_id: null,
    session_kind: "projectless",
    execution_kind: null,
    execution_root: null,
    source_session_id: null,
    title: "",
    preview: "",
    turn_count: 3,
    activity_at: Math.floor(NOW / 1000) - 3600,
    created_at: Math.floor(NOW / 1000) - 7200,
    availability: "projectless",
    ...extra,
  };
}

export const PROJECTLESS_SESSIONS: ProjectSessionDescriptor[] = [
  session("s-active", { title: "当前选中的会话" }),
  session("s-auto", { title: "默认目录会话", workspace_kind: "automatic", execution_root: "/Users/demo/auto" }),
  session("s-selected", { title: "已选目录会话", workspace_kind: "selected", execution_root: "/Users/demo/pick" }),
  session("s-new", { title: "", preview: "刚刚新建的会话预览文字", turn_count: 0 }),
  session("s-zero", { title: "无活动时间", activity_at: 0 }),
];
