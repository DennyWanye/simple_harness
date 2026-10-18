// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// cap 车道的固定夹具数据与假通道应答（技能商店、能力中心、上下文、审批、伙伴成长）。
// 数据抄自各组件的 vitest 测试夹具；不连后端，回包都在微任务里发出。
import type { CapabilityDescriptor, CapabilityOperation } from "../../../src/types/capabilities";
import type { MarketplaceSkill, SkillMeta } from "../../../src/types/skillPlatform";
import type { ContextUsageSnapshot } from "../../../src/types/messages";
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


// ---------------- ContextTrace ----------------

const traceDecisions = [
  { timestamp: 1_700_000_120, classifier_path: "cloud", latency_ms: 2_300, total_tokens: 18_400, token_breakdown: { system: 2_000, history: 12_000, tools: 3_400, current: 1_000 }, reason: "需要工具与长历史（夹具）", session_id: "s-1" },
  { timestamp: 1_700_000_060, classifier_path: "local", latency_ms: 640, total_tokens: 4_200, token_breakdown: { system: 2_000, history: 1_800, current: 400 }, reason: "短问答（夹具）", session_id: "s-1" },
];

/** ContextTrace 的假通道：ok 回两条决定记录；empty 回空清单并带原因。 */
export function traceChannel(mode: "ok" | "empty") {
  return makeFakeChannel((msg: Wire) => {
    if (msg.type !== "decisions_list") return null;
    return {
      type: "decisions_list_response",
      payload: mode === "ok" ? { decisions: traceDecisions } : { decisions: [], reason: "上下文记录暂时读不到（夹具）" },
    };
  }).channel;
}
