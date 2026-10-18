// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// cap 车道的固定夹具数据与假通道应答（技能商店、能力中心、上下文、审批、伙伴成长）。
// 数据抄自各组件的 vitest 测试夹具；不连后端，回包都在微任务里发出。
import type { CapabilityDescriptor, CapabilityOperation } from "../../../src/types/capabilities";
import type { MarketplaceSkill, SkillMeta } from "../../../src/types/skillPlatform";
import type {
  CompanionDetailErrorResponse,
  CompanionDetailResponse,
  CompanionEvent,
  ContextUsageSnapshot,
} from "../../../src/types/messages";
import { makeFakeChannel, type Wire } from "./fakeChannel";

// ---------------- 技能商店 ----------------

export const installedSkills: SkillMeta[] = [
  {
    name: "plan-bs",
    scope: "global",
    capability_id: "skill:plan-bs",
    description: "头脑风暴式计划共创：多轮对话澄清需求，产出可执行计划。",
    source: "global",
    version: "1.2.0",
    path: "/skills/plan-bs/SKILL.md",
  },
  {
    name: "docx",
    scope: "builtin",
    description:
      "Word 文档读写与排版。Triggers include: Word doc / .docx / professional documents / 报告 / 合同 / 简历 等长描述，用来验证两行截断。",
    source: "builtin",
    path: "/builtin/docx/SKILL.md",
  },
  {
    name: "bare-skill",
    scope: "global",
    description: "",
    source: "global",
    path: "/skills/bare/SKILL.md",
  },
];

export const marketplaceSkills: MarketplaceSkill[] = [
  {
    name: "algorithmic-art",
    description: "生成算法艺术作品。",
    source_url: "github:anthropics/skills/tree/main/skills/algorithmic-art",
    author: "anthropics",
    permission_categories: ["read_file", "shell"],
  },
];

export const stagedPendingDangerous = {
  ok: true,
  intent_id: "intent-1",
  digest: "digest-1",
  decision_nonce: "nonce-1",
  decision_version: 2,
  repository_url: "https://github.com/o/r",
  resolved_commit: "a".repeat(40),
  expires_at: "2026-08-28T12:00:00Z",
  project: {
    project_id: "project-a",
    project_name: "Project A",
    project_revision: 3,
    project_identity: "identity-a",
  },
  members: [
    { name: "plan-bs", description: "Brainstorm", permission_categories: ["read_file"] },
    { name: "plan-test", description: "Execute", permission_categories: ["skill_install", "shell"] },
  ],
};

export const stagedPendingSafe = {
  ok: true,
  intent_id: "intent-2",
  digest: "digest-2",
  decision_nonce: "nonce-2",
  decision_version: 1,
  repository_url: "https://github.com/o/safe",
  resolved_commit: "",
  project: { project_id: "project-b", project_revision: 1 },
  members: [{ name: "plain-skill", permission_categories: [] }],
};

/** 技能商店的假通道：已安装列表按给定回包应答；extra 是同一轮额外推送的回包。 */
export function skillStoreChannel(skills: SkillMeta[] | null, extra: unknown[] = [], error?: string) {
  return makeFakeChannel((msg: Wire) => {
    if (msg.type !== "skill_list_installed" || skills === null) return null;
    return [{ type: "skill_list_installed_response", payload: { skills, ...(error ? { error } : {}) } }, ...extra];
  }).channel;
}

// ---------------- 能力中心 ----------------

export const godotCapability: CapabilityDescriptor = {
  capability_id: "godot",
  name: "Godot",
  description: "Godot 4 项目检测与 headless 检查",
  categories: ["instruction", "tool", "pack"],
  version: "1.0.0",
  source: { type: "builtin", label: "DeskPet 内置" },
  scope: "builtin",
  health: "healthy",
  installed: true,
  manifest: {
    schema_version: 1,
    manifest_hash: "a".repeat(64),
    compatibility: {
      deskpet: ">=0.6.0-beta.9",
      os: ["windows"],
      architectures: ["x86_64"],
      python: ">=3.11",
    },
    entries: {
      skills: [{ path: "skills/godot/SKILL.md" }],
      tools: [
        {
          id: "detect",
          provider_name: "godot__detect",
          runtime: "deskpet-json-tool-v1",
          execution_profile: "native-adapter",
          input_views: [],
          entry: "tools/godot/main.py",
          schema: "tools/godot/detect.schema.json",
          healthcheck: "healthcheck",
        },
      ],
      mcp_servers: [{ id: "godot-mcp", config_ref: "mcp/godot.json" }],
    },
    permissions: ["filesystem_read", "filesystem_write", "process_execute"],
    effects: ["read_only", "staged_file", "opaque_manual"],
    dependencies: {
      python: ["pyyaml>=6"],
      commands: [{ name: "godot", version: ">=4.0" }],
    },
    files: [{ path: "tools/godot/main.py", sha256: "b".repeat(64) }],
    uninstall: { stop_servers: true, remove_environment_when_unreferenced: true },
  },
  available_actions: ["activate", "repair", "uninstall"],
};

/** 清单各项都为空的变体：渲染"未声明 / 无"分支。 */
export const emptyManifestCapability: CapabilityDescriptor = {
  ...godotCapability,
  capability_id: "empty-pack",
  name: "空清单能力包",
  description: undefined,
  health: "validating",
  manifest: {
    ...godotCapability.manifest!,
    compatibility: { deskpet: ">=0.6", os: [], architectures: [], python: ">=3.11" },
    entries: { skills: [], tools: [], mcp_servers: [] },
    permissions: [],
    effects: [],
    dependencies: { python: [], commands: [] },
    files: [],
    uninstall: { stop_servers: false, remove_environment_when_unreferenced: false },
  },
  available_actions: ["install"],
};

export const otherCapabilities: CapabilityDescriptor[] = [
  {
    capability_id: "blender",
    name: "Blender",
    description: "3D 资产处理",
    categories: ["tool"],
    version: "0.2.0",
    source: { type: "marketplace", label: "官方市场" },
    scope: "user",
    health: "degraded",
    health_summary: "缺少可执行文件",
    installed: false,
    available_actions: ["install"],
  },
  {
    capability_id: "mcp-fs",
    name: "文件系统 MCP（名字很长很长用来验证省略号截断的能力条目）",
    description: "MCP 文件服务",
    categories: ["mcp"],
    version: "2.1.0",
    source: { type: "local", label: "本机" },
    scope: "project",
    health: "unavailable",
    installed: true,
  },
  {
    capability_id: "gen-tool",
    name: "生成的工具",
    categories: ["tool", "instruction"],
    version: "0.0.1",
    source: { type: "generated", label: "自动生成" },
    scope: "run",
    health: "unknown",
    installed: false,
  },
];

export function capOperation(overrides: Partial<CapabilityOperation> = {}): CapabilityOperation {
  return {
    operation_id: "operation-1",
    capability_id: "godot",
    capability_name: "Godot",
    kind: "install",
    phase: "candidate_ready",
    status: "running",
    authorization_mode: "auto",
    current_validation: "Godot 4 headless healthcheck",
    latest_result: "候选运行时健康",
    available_actions: ["cancel", "retry", "rollback", "uninstall"],
    artifacts: [
      { artifact_id: "artifact-1", name: "project report", kind: "json", ref: "artifact://godot-check" },
      { artifact_id: "artifact-2", name: "plain artifact" },
    ],
    verification_receipts: [
      { receipt_id: "receipt-1", status: "passed", summary: "manifest hash and healthcheck passed", ref: "receipt://v-1" },
      { receipt_id: "receipt-2", status: "failed", summary: "schema check failed" },
      { receipt_id: "receipt-3", status: "unknown", summary: "awaiting runtime" },
    ],
    event_seq: 2,
    ...overrides,
  };
}

export const capabilityOperations: CapabilityOperation[] = [
  capOperation(),
  capOperation({
    operation_id: "op-queued",
    kind: "update",
    phase: "planned",
    status: "queued",
    authorization_mode: "manual",
    current_validation: undefined,
    latest_result: undefined,
    available_actions: [],
    artifacts: [],
    verification_receipts: [],
  }),
  capOperation({
    operation_id: "op-waiting",
    kind: "build",
    phase: "publish_intent",
    status: "waiting_external",
    authorization_mode: "manual",
    available_actions: ["cancel"],
    artifacts: [],
    verification_receipts: [],
  }),
  capOperation({
    operation_id: "op-succeeded",
    kind: "activate",
    phase: "published",
    status: "succeeded",
    available_actions: ["uninstall"],
  }),
  capOperation({
    operation_id: "op-failed",
    kind: "repair",
    phase: "environment_ready",
    status: "failed",
    authorization_mode: "manual",
    latest_result: `诊断开始 ${"诊断内容".repeat(60)}`,
    error_code: "env_missing",
    error_message: "缺少 Python 3.11 运行环境",
    recovery_hint: "安装 Python 3.11 后重试",
    available_actions: ["retry", "rollback"],
  }),
  capOperation({
    operation_id: "op-failed-plain",
    kind: "rollback",
    phase: "catalog_swapped",
    status: "failed",
    error_message: "回滚失败",
    available_actions: [],
    artifacts: [],
    verification_receipts: [],
  }),
];

export const compactOperations: CapabilityOperation[] = [
  capOperation({ operation_id: "op-cancelled", kind: "uninstall", phase: "bound", status: "cancelled" }),
  capOperation({
    operation_id: "op-unknown",
    phase: "staged",
    status: "unknown",
    authorization_mode: "manual",
    progress_percent: 33,
    error_message: "状态未知",
  }),
];

/** 能力中心假通道：capabilities 为 null 时不回能力列表（保留 initialCapabilities）。 */
export function capabilityChannel(
  capabilities: CapabilityDescriptor[] | null,
  operations: CapabilityOperation[],
  autoMode: boolean,
) {
  return makeFakeChannel((msg: Wire) => {
    if (msg.type === "capability_list" && capabilities) {
      return { type: "capability_list_response", payload: { capabilities } };
    }
    if (msg.type === "capability_operations_list") {
      return { type: "capability_operations_response", payload: { operations } };
    }
    if (msg.type === "permission_auto_mode_get") {
      return { type: "permission_auto_mode_response", payload: { enabled: autoMode } };
    }
    return null;
  }).channel;
}

// ---------------- 上下文用量 ----------------

export const measuredSnapshot: ContextUsageSnapshot = {
  schema_version: 2,
  session_id: "session-1",
  source: "measured",
  sample_id: "sample-7",
  snapshot_id: "snapshot-7",
  snapshot_version: 3,
  version: 7,
  availability: "available",
  has_measurement: true,
  model: "sf-glm-5.2",
  prompt_tokens: 83_200,
  completion_tokens: 100,
  cached_tokens: 12_000,
  context_window: 128_000,
  effective_ceiling: 115_200,
  compact_at: 102_400,
  recall_sweet: 16_000,
  updated_at: 20,
};

export const bindingOnlySnapshot: ContextUsageSnapshot = {
  ...measuredSnapshot,
  source: "binding_only",
  has_measurement: false,
  prompt_tokens: 0,
};

export const legacySnapshot: ContextUsageSnapshot = {
  ...measuredSnapshot,
  legacy_incomplete: true,
};

const baseBreakdown = {
  session_id: "session-1",
  snapshot_id: "snapshot-7",
  sample_id: "sample-7",
  snapshot_version: 3,
  usage_version: 7,
  snapshot_fingerprint: "sha256:public-7",
  availability: "available",
  model: "sf-glm-5.2",
  project_name: "deskpet",
  total_estimated_tokens: 9_000,
  last_usage_prompt_tokens: 83_200,
  context_window: 128_000,
  effective_ceiling: 115_200,
  compact_at: 102_400,
  updated_at: 20,
  ts: 20,
};

export const fullBreakdown = {
  ...baseBreakdown,
  sections: [
    { kind: "system", label: "System", count: 1, tokens: 1_000, token_source: "estimated", public_preview: "1 条已脱敏系统指令", preview_truncated: false },
    { kind: "memory", label: "Memory", tokens: 2_400, token_source: "measured", public_preview: null, preview_truncated: false },
    { kind: "tools", label: "Tools", count: 12, tokens: 3_100, token_source: "estimated", public_preview: "tool schemas", preview_truncated: true },
    { kind: "history", label: "History", count: 40, tokens: 2_500, token_source: "estimated", public_preview: "最近对话" },
    { kind: "custom", label: "Custom", tokens: 0, token_source: "estimated", public_preview: "零 token 段" },
    { kind: "current", label: "Current", tokens: null, token_source: "unavailable", public_preview: null },
  ],
  history: [
    { sample_id: "h-1", session_id: "session-1", event_type: "provider_attempt", source: "measured", tokens_after: 20_000, prompt_tokens: 20_000, context_window: 128_000, effective_ceiling: 115_200, estimate_method: "usage", created_at: 1_700_000_000 },
    { sample_id: "h-2", session_id: "session-1", event_type: "provider_attempt", source: "measured", tokens_after: 90_000, prompt_tokens: 90_000, context_window: 128_000, effective_ceiling: 115_200, estimate_method: "usage", created_at: 1_700_000_060 },
    { sample_id: "h-3", session_id: "session-1", event_type: "compaction", source: "compacted", tokens_before: 104_000, tokens_after: 40_000, prompt_tokens: 40_000, context_window: 128_000, effective_ceiling: 115_200, estimate_method: "usage", created_at: 1_700_000_120 },
    { sample_id: "h-4", session_id: "session-1", event_type: "provider_attempt", source: "measured", tokens_after: 83_200, prompt_tokens: 83_200, context_window: 128_000, effective_ceiling: 115_200, estimate_method: "usage", created_at: 1_700_000_180 },
  ],
};

export const unavailableBreakdown = {
  ...baseBreakdown,
  availability: "unavailable",
  last_usage_prompt_tokens: null,
  sections: [],
  history: [],
};

/** 上下文构成弹窗的读取函数：给定构成就立即返回；payload 为 null 时一直不回（停在加载中）。 */
export function breakdownQuery(payload: Record<string, unknown> | null) {
  return () => (payload ? Promise.resolve(payload) : new Promise<unknown>(() => {}));
}

// ---------------- ContextTrace ----------------

export const traceRuns = [
  {
    run_id: "run-1",
    workflow_name: "deep-research",
    workflow_version: "1",
    status: "waiting" as const,
    created_at: 1,
    updated_at: 2,
    active_nodes: ["review"],
    run_version: 7,
    trace_id: "trace-1",
  },
  {
    run_id: "run-2",
    workflow_name: "code-review",
    workflow_version: "2",
    status: "completed" as const,
    created_at: 3,
    updated_at: 4,
    active_nodes: [],
    run_version: 2,
    trace_id: "trace-2",
  },
];

export const traceDetail = {
  run_id: "run-1",
  run_version: 7,
  nodes: [
    { id: "plan", label: "Plan", status: "completed", attempt: 1 },
    { id: "review", label: "Review", status: "waiting", attempt: 1 },
  ],
  edges: [{ source: "plan", target: "review" }],
  spans: [{ span_id: "span-1", name: "model call", kind: "model", status: "ok", redacted: true }],
  checkpoints: [
    {
      checkpoint_id: "cp-1",
      checkpoint_ns: "",
      node_id: "review",
      created_at: 1,
      status: "safe",
      can_fork: true,
      requires_effect_confirmation: true,
      effect_summary: "文件写入可能重新执行",
    },
  ],
  evaluations: [],
  decisions: [
    {
      decision_id: "decision-1",
      run_id: "run-1",
      kind: "approval",
      status: "open" as const,
      prompt: { title: "批准计划" },
      nonce: "nonce-1",
      version: 3,
      created_at: 1,
    },
  ],
  deliveries: [],
};

export function traceChannel(mode: "ok" | "error") {
  return makeFakeChannel((msg: Wire) => {
    if (mode === "error" && msg.type === "workflow_runs_list") {
      const error = { code: "storage_unavailable", message: "运行记录暂时读不到（夹具错误）", retryable: true };
      return { type: "workflow_ipc_error", request_type: msg.type, request_id: msg.request_id, ok: false, error, payload: { error } };
    }
    if (msg.type === "workflow_runs_list") {
      return { type: "workflow_runs_list_response", request_id: msg.request_id, ok: true, payload: { runs: traceRuns, next_cursor: null } };
    }
    if (msg.type === "workflow_run_detail") {
      return { type: "workflow_run_detail_response", request_id: msg.request_id, ok: true, payload: traceDetail };
    }
    return null;
  }).channel;
}

// ---------------- 审批 / 弹窗 ----------------

export const pendingPermissions = [
  { request_id: "p-1", category: "shell", summary: "执行 python -m pytest -q", params: {}, default_action: "prompt", dangerous: true, session_id: "s-1" },
  { request_id: "p-2", category: "write_file", summary: "写入 src/app.ts", params: {}, default_action: "prompt", dangerous: false, session_id: "s-1" },
  { request_id: "p-3", category: "custom_category", summary: "未知类别的请求，用来验证默认强调色和长文本换行（夹具）", params: {}, default_action: "prompt", dangerous: false, session_id: "s-1" },
];

export function approvalChannel() {
  return makeFakeChannel((msg: Wire) =>
    msg.type === "permissions_pending_list"
      ? { type: "permissions_pending_list_response", payload: { pending: pendingPermissions } }
      : null,
  ).channel;
}

// ---------------- 伙伴成长 ----------------

export function companionEvent(overrides: Partial<CompanionEvent> = {}): CompanionEvent {
  return {
    event_id: "event-1",
    profile_id: "profile-a",
    profile_generation: 1,
    session_id: "default",
    seq: 1,
    notification: {
      notification_id: "notice-1",
      kind: "activation",
      summary: "新增本地能力",
      detail_ref: "detail-1",
      detail_version: "detail-v1",
      available_actions: ["confirm", "rollback", "forget"],
    },
    ...overrides,
  };
}

export const evaluationDecision = {
  kind: "evaluation_authorization" as const,
  candidate_id: "candidate-1",
  candidate_revision: 2,
  candidate_package_hash: "package-hash",
  candidate_code_digest: "evaluation-code-digest",
  suite_hash: "suite-hash",
  runner_policy_hash: "runner-policy-hash",
  nonce: "evaluation-nonce",
  decision_version: 4,
  expires_at: "2026-07-26T00:00:00Z",
  status: "open" as const,
};

export const activationDecision = {
  kind: "activation" as const,
  candidate_id: "candidate-1",
  pack_id: "pack-1",
  candidate_version: "1.0.0",
  candidate_package_hash: "package-hash-exact",
  evaluation_report_hash: "report-hash",
  risk_assessment_hash: "risk-hash",
  scope: "user" as const,
  scope_key: "profile-a",
  owner_key: "companion:profile-a:1",
  expected_binding_generation: 8,
  nonce: "activation-nonce",
  decision_version: 2,
  expires_at: "2026-07-26T00:00:00Z",
  status: "open" as const,
};

export const actionConfirmationDecision = {
  kind: "action_confirmation" as const,
  decision_id: "decision-9",
  run_id: "run-9",
  call_id: "call-9",
  effect_id: "effect-9",
  tool_name: "send_email",
  redacted_target_summary: "收件人 a***@example.com",
  args_hash: "args-hash",
  capability_hash: "cap-hash",
  scope_hash: "scope-hash",
  expires_at: "2026-07-26T00:00:00Z",
  status: "open" as const,
};

type DetailReply = CompanionDetailResponse | CompanionDetailErrorResponse;

export function companionQuery(kind: "items" | "error" | "empty") {
  return async (request: { notification_id: string; section: CompanionDetailResponse["payload"]["section"] }): Promise<DetailReply> => {
    if (kind === "error") {
      return { type: "companion_detail_error", request_id: "request-err", payload: { code: "detail_changed" } };
    }
    return {
      type: "companion_detail_response",
      request_id: "request-1",
      payload: {
        notification_id: request.notification_id,
        section: request.section,
        detail_version: "detail-v7",
        items:
          kind === "empty"
            ? []
            : [
                { id: "row-1", title: "变更", summary: "已完成", status: "applied", hash: "hash-1", occurred_at: "2026-07-25T10:00:00Z", extra_field: "附加元数据" },
                { id: "row-2", title: "回执" },
              ],
        next_cursor: kind === "items" ? "opaque-cursor" : null,
      },
    };
  };
}
