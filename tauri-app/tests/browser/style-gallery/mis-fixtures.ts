// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 任务编排车道（mis）的夹具数据与假通道。只给 scenes-mis*.tsx 用。
import type { OrchestrationStatus } from "../../../src/stores/missionsStore";
import type { MissionEvent } from "../../../src/stores/missionsStore";
import { makeFakeChannel, type Wire } from "./fakeChannel";

type Json = Record<string, unknown>;

/** 控制通道 mission_* 风格的成功回包。 */
export function misOk(msg: Wire, data: unknown) {
  return { type: `${String(msg.type)}_response`, payload: { request_id: msg.request_id, ok: true, data } };
}

/** 控制通道 mission_* 风格的失败回包。 */
export function misFail(msg: Wire, error: string, errorCode?: string) {
  return {
    type: `${String(msg.type)}_response`,
    payload: { request_id: msg.request_id, ok: false, error, ...(errorCode ? { error_code: errorCode } : {}) },
  };
}

function missionIdOf(msg: Wire): string {
  const payload = (msg.payload ?? {}) as Json;
  return String(payload.mission_id ?? "");
}

const HASH = (c: string) => c.repeat(64);

// ---------- 状态 ----------

export const MIS_STATUS: OrchestrationStatus = {
  available: true,
  state: "available",
  reason: null,
  orchestrator_version: "0.9.0",
  sdk_version: "0.12.2",
  active_missions: 2,
  assurance_available: true,
  diagnostics_available: true,
  mission_budget_defaults: { max_tokens: 2000000, max_attempts: 6 },
  default_context_profile_id: "ctx-512k",
  context_profiles: [
    {
      profile_id: "ctx-512k",
      max_input_tokens: 393216,
      max_total_tokens: 524288,
      default_max_output_tokens: 32768,
      max_output_tokens_ceiling: 65536,
      mission_max_tokens: 10000000,
    },
    {
      profile_id: "ctx-256k",
      max_input_tokens: 262144,
      default_max_output_tokens: 32768,
      max_output_tokens_ceiling: 32768,
      mission_max_tokens: 5000000,
    },
  ],
  native_plane: {
    profiles: [
      {
        profile_id: "p1",
        background: [
          { loop: "index", consecutive_failures: 1 },
          { loop: "recall", consecutive_failures: 5, last_error_code: "store_busy" },
        ],
      },
    ],
  },
  publish: { enabled: true, root: "/Users/me/publish" },
  deployment_manifest: { features: { domains: { atomic_source_create: true } } },
};

export const MIS_STATUS_PLAIN: OrchestrationStatus = {
  available: true,
  state: "available",
  reason: null,
  publish: { enabled: false, reason: "目录不存在" },
};

export const MIS_STATUS_DEGRADED: OrchestrationStatus = {
  available: true,
  state: "degraded",
  reason: "数据库连续写入失败",
};

// ---------- 列表 ----------

export const MIS_ROWS = [
  {
    id: "mission-a",
    goal: "整理季度报告并发布 REPORT.md",
    status: "ACTIVE",
    ui_state: "running",
    stop_reason: null,
    created_at: 1789000000,
    pending_approvals: 3,
    task_counts: { completed: 2, total: 5 },
  },
  {
    id: "mission-b",
    goal: "根据会议纪要写一份行动清单",
    status: "PLANNING",
    ui_state: "waiting_person",
    stop_reason: null,
    created_at: 1789000100,
    pending_approvals: 0,
    blocked: true,
  },
  {
    id: "mission-c",
    goal: "核对发布目录里的文件",
    status: "ACTIVE",
    stop_reason: null,
    created_at: 1789000200,
    pending_approvals: 0,
    recovery_isolated: { tables: ["tasks", "attempts"] },
  },
  {
    id: "mission-d",
    goal: "写 NOTES.md",
    status: "COMPLETED",
    ui_state: "delivered",
    stop_reason: "verification_passed",
    created_at: 1789000300,
    pending_approvals: 0,
    task_counts: { completed: 3, total: 3 },
  },
  {
    id: "mission-e",
    goal: "测试失败的任务",
    status: "FAILED",
    ui_state: "verifying",
    stop_reason: "budget_exhausted",
    created_at: 1789000400,
    pending_approvals: 0,
    recovery_isolated: { tables: [] },
  },
  {
    id: "mission-f",
    goal: "被取消的任务",
    status: "CANCELLED",
    stop_reason: "user_cancelled",
    created_at: 1789000500,
    pending_approvals: 0,
    blocked: true,
    ui_state: "unknown",
  },
  {
    id: "mission-g",
    goal: "刚创建的任务",
    status: "CREATED",
    stop_reason: null,
    created_at: 1789000600,
    pending_approvals: 0,
  },
];

export const MIS_POLICY = {
  active_version_id: "policy-v3",
  drift: [
    { name: "max_attempts", config: 6, active: 4 },
    { name: "allowed_tools", config: ["read", "write"], active: ["read"] },
  ],
};

// ---------- 完成要求与操作 ----------

const requirementsRef = (revision: number) => ({
  kind: "requirements",
  id: `requirements-${revision}`,
  revision,
  content_hash: HASH("a"),
});

const milestoneHash = {
  id: "FILE_PUBLISHED",
  label: "文件已发布，并已读取核对内容哈希",
  milestone_policy_ref: { id: "milestone-policy", revision: 1, content_hash: HASH("b") },
  evidence_policy_ref: { id: "evidence-policy", revision: 1, content_hash: HASH("c") },
};
const milestoneSeen = {
  id: "FILE_SEEN",
  label: "文件已出现在目录里",
  milestone_policy_ref: { id: "milestone-policy-2", revision: 1, content_hash: HASH("b") },
  evidence_policy_ref: { id: "evidence-policy-2", revision: 1, content_hash: HASH("c") },
};

const opCriteria = [
  { id: "c-report", statement: "生成季度报告", required: true },
  { id: "c-chart", statement: "附上三张图表", required: false },
  { id: "c-publish", statement: "action:file_publish.publish:out/REPORT.md", required: true },
];

/** 待确认、可编辑：两个目标、两个完成标准（不会自动选上）。 */
export function misWorkspaceConfirm(missionId: string, revision = 2): Json {
  return {
    mission_id: missionId,
    state: "CONFIRMATION_REQUIRED",
    editable: true,
    requirements_ref: requirementsRef(revision),
    criteria: opCriteria,
    obligations: [
      { id: "ob-1", label: "交付报告" },
      { id: "ob-2", label: "" },
    ],
    milestones: [milestoneHash, milestoneSeen],
    spec: null,
    spec_hash: null,
    intents: [],
  };
}

/** 待确认、可编辑：一个目标、一个完成标准（自动选上）。 */
export function misWorkspaceConfirmSingle(missionId: string): Json {
  return {
    ...misWorkspaceConfirm(missionId),
    requirements_ref: { ...requirementsRef(3), content_hash: HASH("9") },
    obligations: [{ id: "ob-1", label: "交付报告" }],
    milestones: [milestoneHash],
  };
}

/** 待确认但不可编辑、没有完成标准（只能确认内容类）。 */
export function misWorkspaceReadonly(missionId: string): Json {
  return {
    ...misWorkspaceConfirm(missionId),
    requirements_ref: { ...requirementsRef(4), content_hash: HASH("8") },
    editable: false,
    milestones: [],
    criteria: [opCriteria[0]],
  };
}

export function misWorkspaceWaiting(missionId: string): Json {
  return {
    mission_id: missionId,
    state: "WAITING_REQUIREMENTS",
    editable: false,
    requirements_ref: { ...requirementsRef(1), content_hash: HASH("7") },
    criteria: [],
    obligations: [],
    milestones: [],
  };
}

export function misWorkspaceApprovedContent(missionId: string): Json {
  return {
    ...misWorkspaceConfirm(missionId),
    requirements_ref: { ...requirementsRef(5), content_hash: HASH("6") },
    state: "APPROVED",
    editable: false,
    spec_hash: HASH("d"),
    spec: { mode: "CONTENT_ONLY", effects: [] },
    criteria: opCriteria.slice(0, 2),
  };
}

export function misWorkspaceApprovedEffects(missionId: string): Json {
  return {
    ...misWorkspaceConfirm(missionId),
    requirements_ref: { ...requirementsRef(6), content_hash: HASH("5") },
    state: "APPROVED",
    editable: false,
    spec_hash: HASH("d"),
    spec: {
      mode: "REQUIRED_EFFECTS",
      effects: [
        { effect_key: "eff-publish", criterion_ids: ["c-publish"], required_milestone: "FILE_PUBLISHED" },
        { effect_key: "eff-other", criterion_ids: ["c-report", "c-chart"], required_milestone: "FILE_UNKNOWN" },
      ],
    },
    intents: [
      { intent_id: "i-0", effect_key: "eff-publish", spec_hash: HASH("d"), state: "AWAITING_REVIEW", current: true },
      { intent_id: "i-1", effect_key: "eff-publish", spec_hash: HASH("d"), state: "AWAITING_APPROVAL", current: true },
      { intent_id: "i-old", effect_key: "eff-other", spec_hash: HASH("e"), state: "FAILED", current: true },
    ],
  };
}

// ---------- 任务详情 ----------

function budgetRows(): Json[] {
  const steps = Array.from({ length: 10 }, (_, i) => ({
    task_id: `step-${i + 1}`,
    label: i === 3 ? "" : `第 ${i + 1} 步`,
    attempts: i === 9 ? 0 : i + 1,
    settled_tokens: i === 9 ? 0 : 1000 * (i + 1),
    failed_attempts: i % 3,
    unknown_usage_attempts: i === 2 ? 1 : 0,
    branches: i === 1 ? 3 : 1,
  }));
  const rows: Json[] = [
    { obligation_id: "ob-root", label: "交付季度报告", depth: 0, attempts: 9, settled_tokens: 54000, failed_attempts: 2, steps },
    { obligation_id: "ob-idle", label: "没花过的", depth: 1, attempts: 0, settled_tokens: 0 },
  ];
  for (let i = 0; i < 9; i += 1)
    rows.push({
      obligation_id: `ob-${i}`,
      label: i === 4 ? "" : `子目标 ${i + 1}`,
      depth: (i % 3) + 1,
      attempts: i + 1,
      settled_tokens: 500 * (i + 1),
      failed_attempts: i % 2,
      unknown_usage_attempts: i === 0 ? 2 : 0,
    });
  return rows;
}

export function misEvents(count: number): MissionEvent[] {
  const out: MissionEvent[] = [];
  for (let seq = 1; seq <= count; seq += 1)
    out.push(
      seq % 7 === 0
        ? { seq, type: "HumanCommentAdded", created_at: 1789000000 + seq, summary: `第 ${seq} 条评论` }
        : { seq, type: seq % 2 ? "TaskCommitted" : "AttemptStarted", created_at: 1789000000 + seq },
    );
  return out;
}

const SOURCES = [
  { path: "sources/纪要.md", revision: 2, version_hash: "abcdef0123456789abcdef", kind: "markdown" },
  { path: "sources/旧需求.md", revision: 1, version_hash: "1234567890abcdef1234", superseded_by: "v2" },
  { path: "sources/作废.txt", revision: 3, version_hash: "fedcba9876543210fedc", revoked: true },
];

const ARTIFACTS_FULL = [
  { id: "art-1", path: "REPORT.md", size_bytes: 2048, verification_status: "VERIFIED", content_hash: "sha256:0123456789abcdef0123" },
  { id: "art-2", path: "notes.txt", size_bytes: 300, verification_status: "UNVERIFIED", content_hash: "fedcba987654" },
  { id: "art-3", path: "data/big.bin", size_bytes: 5000000, verification_status: "", content_hash: "" },
  { id: "art-4", path: "out/very/long/path/with/many/segments/that-should-wrap-anywhere-because-it-is-long.md", size_bytes: null, verification_status: "REJECTED" },
];
const ARTIFACTS_INTERNAL = [
  { id: "art-i1", path: ".assurance/review-1.json", size_bytes: 512, verification_status: "VERIFIED", content_hash: "sha256:aaaa" },
  { id: "art-i2", path: ".assurance/check-2.json", size_bytes: 1536, verification_status: "" },
];

export const MIS_DETAIL_A: Json = {
  mission: {
    id: "mission-a",
    goal: "整理季度报告并发布 REPORT.md",
    status: "ACTIVE",
    ui_state: "running",
    stop_reason: "timeout",
    budget: { max_tokens: 2000000, max_attempts: 6 },
  },
  mission_policy: { version_id: "policy-v3" },
  usage: { settled_tokens: 123456, reserved_tokens: 20000, overrun_calls: 2, overrun_tokens: 3400 },
  runtime_context: { max_input_tokens: 393216, max_total_tokens: 524288 },
  budget_by_duty: budgetRows(),
  steps_no_longer_counting: [
    { label: "写摘要", requirements_revision: 2 },
    { label: "画图", requirements_revision: 3 },
  ],
  unrefined_goals: [{ label: "补充图表" }, { label: "校对全文" }],
  waiting_on: [
    "approval-x",
    { kind: "review", since: 1789000000, request_id: "w2" },
    { kind: "mystery", request_id: "w3" },
    { kind: "reconciliation", needs_human: true, action_key: "act-1", state: "UNKNOWN", request_id: "w4" },
    { kind: "reconciliation", needs_human: true, action_key: "act-2", state: "FAILED", request_id: "w5" },
    { kind: "reconciliation", needs_human: false, action_key: "act-3", request_id: "w6" },
  ],
  planning_authorization_requests: [{ request_id: "auth-1", mission_id: "mission-a" }],
  blocked: [{ task_id: "task-blocked" }, { task_id: "task-blocked-2" }],
  operation_workspace: misWorkspaceApprovedEffects("mission-a"),
  planning_questions: [
    {
      decision_id: "q-1",
      state: "PENDING",
      version: 1,
      question: "报告用哪种语言？",
      options: [
        { key: "zh", label: "中文" },
        { key: "en", label: "英文" },
      ],
    },
    { decision_id: "q-2", state: "ANSWERED", version: 2, question: "需要图表吗？", answer: "需要三张" },
  ],
  approvals: [
    {
      request_id: "ap-action",
      kind: "action",
      state: "PENDING",
      summary: {
        connector: "file_publish",
        operation: "publish",
        target: "REPORT.md",
        params: { artifact_path: "out/report.md" },
        reason: { text: "交付季度报告", source: "system" },
        previous_attempt: { outcome: "service_refused", reason: "目录只读" },
      },
      action: { state: "UNKNOWN", published_path: "/srv/publish/REPORT.md", published_hash: "sha256:abcdef1234567890" },
      comments: [
        { principal_id: "user-1", text: "看过了，可以发" },
        { text: "没有署名的评论" },
      ],
    },
    {
      request_id: "ap-legacy",
      kind: "action",
      state: "PENDING",
      summary: { text: "把 feature_flags.new_ui 设为 on", source: "model" },
      action: { reason: { text: "请批准", source: "model" }, state: "SUCCEEDED", published_path: "/srv/flags.json" },
    },
    {
      request_id: "ap-source",
      kind: "source_change",
      state: "PENDING",
      summary: { text: "替换纪要为新版本", source: "model" },
      source_change: { operation: "supersede", path: "sources/纪要.md", expected_version_hash: "abcdef" },
    },
    { request_id: "ap-review", kind: "review", state: "PENDING", summary: { text: "请复核报告结论", source: "model" } },
    { request_id: "ap-review-2", kind: "review", state: "PENDING", summary: "第二份复核" },
    {
      request_id: "ap-arb",
      kind: "arbitration",
      state: "PENDING",
      summary: { text: "代码测试结论有争议", source: "system" },
      options: ["met", "unmet", "other"],
    },
    { request_id: "ap-mystery", kind: "mystery", state: "PENDING", summary: "未知类型" },
    { request_id: "ap-empty", kind: "", state: "PENDING", summary: "没写类型" },
    { request_id: "ap-done", kind: "action", state: "APPROVED", summary: "已经批过的" },
  ],
  tasks: [
    { id: "task-1", goal: { text: "写初稿", source: "model" }, status: "RUNNING" },
    { id: "task-2", goal: "校对", status: "DONE" },
    { id: "task-blocked", goal: "汇总", status: "ACTIVE" },
    { id: "task-x", goal: "奇怪状态", status: "WEIRD" },
  ],
  results: [
    {
      result_id: "res-1",
      attempt_id: "att-1",
      verdict: "FAIL",
      verification_state: "DONE",
      final_rejection: {
        reason: "stale_source",
        source_issues: [
          { path: "sources/纪要.md", reason: "superseded", version: "v2" },
          { reason: "", code: "X_CODE" },
          { path: "sources/x.md", reason: "odd_reason" },
        ],
      },
      summary: { text: "报告写好了，但引用了旧版纪要", source: "model" },
      verification_layers: [
        { layer: "format_check", status: "PASS", summary: { text: "格式正确", source: "system" } },
        { layer: "code_test", status: "FAIL", summary: { text: "两个测试失败", source: "model" } },
        { layer: "review", status: "ERROR" },
        { layer: "legacy", status: "NOT_REQUIRED", summary: "旧的纯字符串" },
        { layer: "human", status: "NEEDS_HUMAN" },
      ],
    },
    {
      attempt_id: "att-2",
      verdict: "PASS",
      verification_state: "DONE",
      summary: "第二次通过",
      verification_layers: [{ layer: "format_check", status: "PASS" }],
    },
    { attempt_id: "att-3", verdict: "", verification_state: "RUNNING", summary: "还在验证", verification_layers: [] },
    { attempt_id: "att-4", summary: "没有判定", verification_layers: [{ layer: "x", status: "SKIPPED" }] },
  ],
  artifacts: [...ARTIFACTS_FULL, ...ARTIFACTS_INTERNAL],
  sources: SOURCES,
};

export const MIS_ARTIFACT_TEXT: Json = {
  artifact_id: "art-1",
  path: "REPORT.md",
  content_hash: "sha256:0123456789abcdef0123456789abcdef",
  size_bytes: 2048,
  encoding: "utf-8",
  content: "# 季度报告\n\n- 收入增长 12%\n- 成本下降 3%\n\n" + "长行内容".repeat(40),
  truncated: true,
};
export const MIS_ARTIFACT_UNVERIFIED: Json = { ...MIS_ARTIFACT_TEXT, artifact_id: "art-2", path: "notes.txt", truncated: false };
export const MIS_ARTIFACT_BINARY: Json = { artifact_id: "art-3", path: "data/big.bin", content_hash: "sha256:ffff", size_bytes: 5000000, encoding: "binary" };
export const MIS_ARTIFACT_EMPTY: Json = { artifact_id: "art-x", path: "missing.md", size_bytes: 0, encoding: "utf-8" };

export const MIS_DETAIL_B: Json = {
  mission: {
    id: "mission-b",
    goal: "根据会议纪要写一份行动清单",
    status: "PLANNING",
    ui_state: "waiting_person",
    stop_reason: null,
    budget: {},
  },
  mission_policy: {},
  usage: {},
  runtime_context: { max_input_tokens: 262144 },
  operation_workspace: misWorkspaceConfirm("mission-b"),
  planning_authorization_requests: [{ request_id: "auth-b", mission_id: "mission-b" }],
  planning_questions: [
    { decision_id: "q-b1", state: "PENDING", version: 1, question: "行动清单要写负责人吗？", options: [] },
    { decision_id: "q-b2", state: "SUPERSEDED", version: 1, question: "旧问题" },
  ],
  approvals: [],
  tasks: [],
  results: [],
  artifacts: [ARTIFACTS_FULL[0], ...ARTIFACTS_INTERNAL],
  sources: [],
};

export const MIS_DETAIL_DONE: Json = {
  mission: { id: "mission-d", goal: "写 NOTES.md", status: "COMPLETED", ui_state: "delivered", stop_reason: "verification_passed", budget: { max_tokens: 100000 } },
  mission_policy: { version_id: "policy-v2" },
  usage: { settled_tokens: 5000, reserved_tokens: 0 },
  tasks: [{ id: "task-d1", goal: "写 NOTES.md", status: "COMPLETED" }],
  results: [],
  artifacts: [ARTIFACTS_FULL[0]],
  sources: [SOURCES[0]],
  planning_questions: [{ decision_id: "q-d", state: "PENDING", version: 1, question: "已结束任务留下的问题", options: [] }],
};

export const MIS_DETAIL_FAILED: Json = {
  mission: { id: "mission-e", goal: "人工接管后停止的任务", status: "FAILED", stop_reason: "HUMAN_OVERRIDE", budget: {} },
  usage: {},
  tasks: [{ id: "task-e1", goal: "做点事", status: "FAILED" }],
  results: [],
  artifacts: [],
};

export const MIS_DETAIL_FAILED_PLAIN: Json = {
  mission: { id: "mission-e2", goal: "预算用完的任务", status: "FAILED", stop_reason: "budget_exhausted" },
  tasks: [],
};

export const MIS_DETAIL_CANCELLED: Json = {
  mission: { id: "mission-f", goal: "被取消的任务", status: "CANCELLED", stop_reason: "something_new" },
};

export const MIS_DETAIL_ISOLATED: Json = {
  mission: { id: "mission-c", goal: "核对发布目录里的文件", status: "ACTIVE" },
  recovery_isolated: { tables: ["tasks"] },
  tasks: [{ id: "task-c1", goal: "核对", status: "PENDING" }],
};

export const MIS_DETAIL_APPROVALS_ONLY: Json = {
  mission: { id: "mission-h", goal: "只有审批等待的任务", status: "ACTIVE" },
  approvals: [{ request_id: "ap-h", kind: "review", state: "PENDING", summary: "复核一下" }],
};

export const MIS_DETAIL_RUNNING: Json = {
  mission: { id: "mission-i", goal: "自动进行中的任务", status: "ACTIVE", ui_state: "queued" },
};

// ---------- 改计划进度 ----------

export const MIS_CONVERGENCE = {
  jobs: [
    { job_id: "job-ready", state: "READY", row_version: 3 },
    { job_id: "job-fenced", state: "FENCED", row_version: 1 },
    { job_id: "job-done", state: "DONE", row_version: 9 },
  ],
  blocked_notifications: [
    { message_id: "msg-1", row_version: 2, subject_key: "subject-1", error_code: "delivery_failed", attempts: 4 },
  ],
};

// ---------- 对话卡片与通知 ----------

export const MIS_CHAT_DETAILS: Record<string, Json> = {
  "chat-1": {
    mission: { id: "chat-1", goal: "把周报整理好发布到 REPORT.md", status: "CREATED" },
    tasks: [
      { id: "desktop-root-1", status: "ACTIVE" },
      { id: "t1", status: "COMPLETED" },
      { id: "t2", status: "RUNNING", kind: "work" },
      { id: "t3", status: "SUCCEEDED", kind: null },
      { id: "t4", status: "RUNNING", kind: "review" },
    ],
    operation_workspace: misWorkspaceConfirmSingle("chat-1"),
    planning_questions: [
      { decision_id: "cq-1", state: "PENDING", version: 1, question: "周报要包含哪几周？", options: [] },
      { decision_id: "cq-2", state: "ANSWERED", version: 1, question: "已答的问题", answer: "x" },
    ],
    approvals: [
      {
        request_id: "cap-1",
        kind: "action",
        state: "PENDING",
        summary: { connector: "file_publish", operation: "publish", target: "REPORT.md", reason: "交付周报", reason_source: "model" },
        action: {},
      },
      { request_id: "cap-2", kind: "source_change", state: "PENDING", source_change: { operation: "revoke", path: "sources/旧.md" } },
      { request_id: "cap-3", kind: "source_change", state: "PENDING", source_change: { operation: "supersede", path: "sources/纪要.md" } },
      { request_id: "cap-4", kind: "review", state: "PENDING" },
      { request_id: "cap-5", kind: "", state: "PENDING" },
    ],
    actions: [
      { action_key: "k1", state: "SUCCEEDED", target: "REPORT.md", published_path: "/srv/publish/REPORT.md" },
      { action_key: "k2", state: "UNKNOWN", target: "X.md", published_path: "/srv/x" },
    ],
  },
  "chat-2": {
    mission: { id: "chat-2", goal: "失败的后台任务", status: "FAILED", stop_reason: "budget_exhausted" },
    tasks: [{ id: "t1", status: "FAILED" }],
    operation_workspace: { ...misWorkspaceApprovedContent("chat-2"), requirements_ref: { revision: 1 } },
  },
  "chat-3": {
    mission: { id: "chat-3", goal: "已完成的后台任务", status: "COMPLETED" },
  },
  "chat-5": {
    mission: { id: "chat-5", goal: "状态词没有中文的任务", status: "WEIRD_STATE" },
  },
};

export const MIS_NOTICES = [
  { notice_id: "n1", mission_id: "m1", status: "COMPLETED", status_zh: "已完成", goal: "最早的一条" },
  { notice_id: "n2", mission_id: "m2", status: "CANCELLED", status_zh: "", goal: "" },
  {
    notice_id: "n3",
    mission_id: "m3",
    status: "COMPLETED",
    goal: "发布周报：" + "很长的目标文字".repeat(20),
    action_settled: { operation: "file_publish.publish", target: "REPORT.md", applied: true },
  },
  {
    notice_id: "n4",
    mission_id: "m4",
    status: "FAILED",
    status_zh: "未完成",
    goal: "失败的任务",
    stop_reason: "预算用完",
    unresolved_actions: 2,
  },
  {
    notice_id: "n5",
    mission_id: "m5",
    status: "COMPLETED",
    goal: "",
    action_settled: { operation: "file_publish.publish", target: "X.md", applied: false },
  },
  { notice_id: "", mission_id: "bad" },
];

// ---------- 假通道 ----------

/** 任务编排页用的假通道：按请求类型回固定夹具；任务过程（MissionStory）一律回"夹具不提供"。 */
export function misChannel(details: Record<string, Json> = {}) {
  return makeFakeChannel((msg) => {
    const type = String(msg.type);
    const missionId = missionIdOf(msg);
    switch (type) {
      case "taskgraph.convergence":
        return misOk(msg, missionId === "mission-a" ? MIS_CONVERGENCE : { jobs: [], blocked_notifications: [] });
      case "taskgraph.execution_snapshot":
        return misFail(msg, "画廊夹具不提供任务过程");
      case "orchestration_policy_status":
        return misOk(msg, MIS_POLICY);
      case "mission_get":
        return details[missionId] ? misOk(msg, details[missionId]) : misFail(msg, "找不到这个任务", "not_found");
      case "mission_events":
        return misOk(msg, { mission_id: missionId, events: [], has_more: false });
      default:
        return null;
    }
  });
}

/** 改计划进度"已过期"形态：第一次读成功并推一条 mission_changed，第二次读失败。 */
export function misStaleConvergenceChannel(missionId: string, errorCode?: string) {
  let reads = 0;
  return makeFakeChannel((msg) => {
    if (msg.type !== "taskgraph.convergence") return null;
    reads += 1;
    if (reads === 1) return [misOk(msg, MIS_CONVERGENCE), { type: "mission_changed", payload: { mission_id: missionId } }];
    return misFail(msg, "读不到", errorCode);
  });
}

/** 对话卡片用：mission_get 按 MIS_CHAT_DETAILS 回；chat-4 回失败。 */
export function misChatChannel() {
  return makeFakeChannel((msg) => {
    if (msg.type !== "mission_get") return null;
    const missionId = missionIdOf(msg);
    if (missionId === "chat-4") return misFail(msg, "读取任务失败：后台不可用");
    return MIS_CHAT_DETAILS[missionId] ? misOk(msg, MIS_CHAT_DETAILS[missionId]) : null;
  });
}

export function misNoticesChannel(mode: "list" | "protocol" | "plain-error") {
  return makeFakeChannel((msg) => {
    if (msg.type !== "mission_notices") return null;
    if (mode === "list") return misOk(msg, MIS_NOTICES);
    if (mode === "protocol") return misFail(msg, "收到的通知格式不对", "protocol_error");
    return misFail(msg, "普通失败不显示");
  });
}
