// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 设置车道（set）的固定夹具：控制通道回包、Tauri 命令返回值、状态库数据。全部是假数据，不含真实凭据。
import { useState } from "react";
import type { ControlChannel } from "../../../src/ws/ControlChannel";
import type { Provider } from "../../../src/components/providersStore";
import type { DataDirSetting } from "../../../src/components/settingsMessages";
import type { SkillCatalogueRow } from "../../../src/components/settingsOrchestration";
import type { CatalogModel } from "../../../src/code-panel/sessionModelsStore";
import type { ContextUsageSnapshot } from "../../../src/stores/sessionsStore";
import { makeFakeChannel, type Responder, type Wire } from "./fakeChannel";

/** 场景内稳定的 getChannel（每个组件实例一条假通道）；responder 为 null 时 getChannel 返回 null。 */
export function useFakeGetter(responder: Responder | null): () => ControlChannel | null {
  const [getter] = useState(() => {
    if (!responder) return () => null;
    const { channel } = makeFakeChannel(responder);
    return () => channel;
  });
  return getter;
}

/** 永不回包的应答器（组件停在加载中）。 */
export const silent: Responder = () => null;

const rid = (msg: Wire) => msg.request_id;

// ---------------- 服务商 ----------------
export const SET_PROVIDERS: Provider[] = [
  {
    id: "provider-relay-a",
    source: "user",
    name: "夹具中转 A（多模型）",
    base_url: "https://relay-a.example.test/v1",
    models: ["deepseek-v4-flash", "deepseek-v4-pro", "gpt-5.5"],
    default_model: "deepseek-v4-flash",
    api_key: "********",
    priority: 0,
    enabled: true,
  },
  {
    id: "provider-relay-b",
    source: "user",
    name: "夹具中转 B（已停用，名字很长很长很长很长很长很长很长很长）",
    base_url: "https://relay-b.example.test/v1",
    models: ["kimi-k3"],
    default_model: null,
    model: "kimi-k3",
    api_key: "********",
    priority: 1,
    enabled: false,
  },
  {
    id: "provider-relay-c",
    source: "user",
    name: "夹具中转 C（无模型）",
    base_url: "https://relay-c.example.test/v1",
    models: [],
    api_key: "********",
    priority: 2,
    enabled: true,
  },
];

// ---------------- 模型上下文 ----------------
const BUILTIN = {
  "deepseek-v4-flash": { context_window: 512000, effective_pct: 0.9, compact_at_pct: 0.75, recall_sweet_tokens: 128000 },
  "gpt-5.5": { context_window: 1000000, effective_pct: 0.9, compact_at_pct: 0.8, recall_sweet_tokens: 256000 },
  _default: { context_window: 32000, effective_pct: 0.9, compact_at_pct: 0.7, recall_sweet_tokens: 16000 },
};

export function modelContextResponder(opts: {
  source?: string;
  defaultModel?: string;
  window?: number;
  reason?: string;
  replyGet?: boolean;
}): Responder {
  return (msg) => {
    if (msg.type === "models_list") {
      return {
        type: "models_list_response",
        payload: {
          models: opts.defaultModel === "" ? [] : [{ id: "deepseek-v4-flash" }, { id: "gpt-5.5" }, { id: "custom-model-x" }],
          default_model: opts.defaultModel ?? "deepseek-v4-flash",
        },
      };
    }
    if (msg.type === "model_context_get") {
      if (opts.replyGet === false) return null;
      const model = String((msg.payload as { model?: string } | undefined)?.model ?? "");
      if (opts.reason) {
        return { type: "model_context_get_response", payload: { model, resolved: {}, builtin: BUILTIN, reason: opts.reason } };
      }
      const win = opts.window ?? 512000;
      return {
        type: "model_context_get_response",
        payload: {
          model,
          resolved: {
            context_window: win,
            effective_pct: 0.9,
            compact_at_pct: 0.75,
            recall_sweet_tokens: 128000,
            source: opts.source ?? "project",
          },
          builtin: BUILTIN,
        },
      };
    }
    if (msg.type === "context_compaction_get") {
      return {
        type: "context_compaction_get_response",
        payload: { model: "deepseek-v4-flash", default_model: "follow_session", available_models: ["deepseek-v4-flash", "gpt-5.5"] },
      };
    }
    return null;
  };
}

// ---------------- 编排相关设置 ----------------
export const STORAGE_MEASURED = {
  bytes: 6.4 * 1024 ** 3,
  measured_at_ms: 1759305600000,
  warn_bytes: 5 * 1024 ** 3,
  over_warn: true,
  measured: true,
  breakdown: [
    { key: "chat", label: "聊天记录", bytes: 3.2 * 1024 ** 3 },
    { key: "index", label: "检索索引", bytes: 2.1 * 1024 ** 3 },
    { key: "checks", label: "检查记录", bytes: 300 * 1024 ** 2 },
  ],
};
export const STORAGE_UNMEASURED = {
  bytes: 0,
  measured_at_ms: null,
  warn_bytes: 5 * 1024 ** 3,
  over_warn: false,
  measured: false,
  breakdown: [],
};

export function storageResponder(mode: "measured" | "unmeasured" | "error" | "measured-then-error"): Responder {
  return (msg) => {
    if (msg.type !== "orchestration_storage_get") return null;
    const ok = (data: unknown) => ({ type: "orchestration_storage_get_response", payload: { ok: true, request_id: rid(msg), data } });
    const bad = { type: "orchestration_storage_get_response", payload: { ok: false, request_id: rid(msg), error: "统计失败（夹具）" } };
    if (mode === "measured") return ok(STORAGE_MEASURED);
    if (mode === "unmeasured") return ok(STORAGE_UNMEASURED);
    if (mode === "error") return bad;
    return [ok({ ...STORAGE_MEASURED, over_warn: false }), bad];
  };
}

export function budgetResponder(mode: "opened" | "unopened" | "none" | "error"): Responder {
  return (msg) => {
    if (msg.type !== "orchestration_status") return null;
    if (mode === "error") {
      return { type: "orchestration_status_response", payload: { ok: false, request_id: rid(msg), error: "后台未就绪（夹具）" } };
    }
    const global_budget =
      mode === "none"
        ? null
        : {
            month: "2026-10",
            max_tokens: 200_000_000,
            opened: mode === "opened",
            used_tokens: mode === "opened" ? 35_000_000 : 0,
            reserved_tokens: mode === "opened" ? 12_000_000 : 0,
            remaining_tokens: mode === "opened" ? 153_000_000 : 200_000_000,
          };
    return { type: "orchestration_status_response", payload: { ok: true, request_id: rid(msg), data: { global_budget } } };
  };
}

export function publishDirResponder(mode: "enabled" | "invalid" | "unset" | "error" | "saved"): Responder {
  return (msg) => {
    if (msg.type !== "orchestration_publish_dir_get") return null;
    const type = mode === "saved" ? "orchestration_publish_dir_set_response" : "orchestration_publish_dir_get_response";
    if (mode === "error") return { type, payload: { ok: false, request_id: rid(msg), error: "读取发布目录失败（夹具）" } };
    const data =
      mode === "enabled" || mode === "saved"
        ? { configured: "/Users/fixture/Documents/发布", publish: { enabled: true, root: "/Users/fixture/Documents/发布" }, active_missions: 2 }
        : mode === "invalid"
          ? { configured: "/Volumes/离线盘/发布", publish: { enabled: false, reason: "目录不存在" }, active_missions: 0 }
          : { configured: "", publish: {}, active_missions: 0 };
    return { type, payload: { ok: true, request_id: rid(msg), data } };
  };
}

const pools = {
  "deepseek-flash-128k-v1": { state: "ADMITTED", usable: true, reasons: [] },
  "deepseek-flash-512k-thinking-v2": { state: "ADMITTED", usable: false, reasons: ["DEPENDENCY_UNRESOLVED"] },
  "custom-pool": { state: null, usable: false, reasons: ["NOT_MIRRORED"] },
};
const skillRow = (id: string, state: string, extra: Partial<SkillCatalogueRow> = {}): SkillCatalogueRow => ({
  skill_ref: { skill_id: id, version: 1 },
  skill_id: id,
  version: 1,
  name: `夹具技能 ${id}`,
  description: state === "RETIRED" ? "" : `夹具技能 ${id} 的说明文字。`,
  state,
  pools,
  ...extra,
});
export const SKILL_ROWS: SkillCatalogueRow[] = [
  skillRow("quarantined", "QUARANTINED", { pools: { "pool-a": { state: "QUARANTINED", usable: false, reasons: [] } } }),
  skillRow("trial-new", "TRIAL", { evaluation: null }),
  skillRow("trial-passed", "TRIAL", { evaluation: { mission_id: "m1", mission_status: "COMPLETED", passed: true, expired: false } }),
  skillRow("trial-expired", "TRIAL", { evaluation: { mission_id: "m2", mission_status: "RUNNING", passed: false, expired: true } }),
  skillRow("trial-failed", "TRIAL", { evaluation: { mission_id: "m3", mission_status: "FAILED", passed: false, expired: false } }),
  skillRow("trial-cancelled", "TRIAL", { evaluation: { mission_id: "m4", mission_status: "CANCELLED", passed: false, expired: false } }),
  skillRow("trial-running", "TRIAL", { evaluation: { mission_id: "m5", mission_status: "RUNNING", passed: false, expired: false } }),
  skillRow("admitted", "ADMITTED"),
  skillRow("suspended", "SUSPENDED"),
  skillRow("retired", "RETIRED"),
  skillRow("unknown", "WEIRD", { state: null }),
];

export function skillResponder(mode: "rows" | "empty" | "error" | "done" | "refused" | "few"): Responder {
  return (msg) => {
    if (msg.type !== "orchestration_skill_catalogue") return null;
    const catalogue = (skills: SkillCatalogueRow[]) => ({ owner: "fixture", members: [], skills });
    if (mode === "error") return { type: "orchestration_skill_catalogue_response", payload: { ok: false, request_id: rid(msg), error: "目录读取失败（夹具）" } };
    if (mode === "rows") return { type: "orchestration_skill_catalogue_response", payload: { ok: true, request_id: rid(msg), data: catalogue(SKILL_ROWS) } };
    if (mode === "few") return { type: "orchestration_skill_catalogue_response", payload: { ok: true, request_id: rid(msg), data: catalogue(SKILL_ROWS.slice(7, 9)) } };
    if (mode === "empty") return { type: "orchestration_skill_catalogue_response", payload: { ok: true, request_id: rid(msg), data: catalogue([]) } };
    if (mode === "done") {
      return { type: "orchestration_skill_admit_response", payload: { ok: true, request_id: rid(msg), data: { response: { error: null }, catalogue: catalogue(SKILL_ROWS.slice(7, 8)) } } };
    }
    return {
      type: "orchestration_skill_lifecycle_response",
      payload: { ok: true, request_id: rid(msg), data: { response: { error: { code: "SKILL_IN_USE" } }, catalogue: catalogue(SKILL_ROWS.slice(8, 9)) } },
    };
  };
}

// ---------------- 权限 / 执行预算 ----------------
export function autoModeResponder(mode: "auto" | "manual"): Responder {
  return (msg) =>
    msg.type === "permission_auto_mode_get"
      ? { type: "permission_auto_mode_response", request_id: rid(msg), payload: { mode, generation: mode === "auto" ? 7 : 2, provenance: mode === "auto" ? "user" : "default" } }
      : null;
}
export function timeoutResponder(minutes: number): Responder {
  return (msg) => (msg.type === "chat_turn_timeout_get" ? { type: "chat_turn_timeout_response", request_id: rid(msg), payload: { minutes } } : null);
}

/** 把若干应答器合成一个（第一个有回包的生效）。 */
export function combine(...responders: Responder[]): Responder {
  return (msg) => {
    for (const r of responders) {
      const out = r(msg);
      if (out != null) return out;
    }
    return null;
  };
}

/** 设置页"全都有数据"的应答器。 */
export const settingsFullResponder: Responder = combine(
  (msg) =>
    msg.type === "settings_providers_list_request" ? { type: "settings_providers_list_response", payload: { providers: SET_PROVIDERS } } : null,
  modelContextResponder({ source: "project" }),
  autoModeResponder("auto"),
  timeoutResponder(15),
  storageResponder("measured"),
  budgetResponder("opened"),
  publishDirResponder("enabled"),
  skillResponder("few"),
);

/** 设置页"空/出错/未授权"的应答器。 */
export const settingsSparseResponder: Responder = combine(
  (msg) =>
    msg.type === "settings_providers_list_request"
      ? { type: "settings_providers_error", payload: { detail: "服务商注册表读取失败（夹具）" } }
      : null,
  modelContextResponder({ reason: "resolve_error: 夹具解析失败" }),
  autoModeResponder("manual"),
  timeoutResponder(42),
  storageResponder("unmeasured"),
  budgetResponder("unopened"),
  publishDirResponder("unset"),
  skillResponder("empty"),
);

// ---------------- 数据目录 ----------------
export const DATA_DIR_PLAIN: DataDirSetting = {
  effective: "/Users/fixture/Library/Application Support/deskpet",
  default: "/Users/fixture/Library/Application Support/deskpet",
  env_override: null,
  preference: null,
  externally_pinned: false,
  effective_exists: true,
  effective_size_bytes: 734 * 1024 * 1024,
};
export const DATA_DIR_PINNED: DataDirSetting = {
  effective: "/Volumes/大盘/deskpet-data",
  default: null,
  env_override: "/Volumes/大盘/deskpet-data",
  preference: "/Volumes/大盘/deskpet-pref",
  externally_pinned: true,
  effective_exists: true,
  effective_size_bytes: 12 * 1024 * 1024,
};

/** 按调用次序依次返回的 Tauri 应答（第 n 次调用取第 n 项，超出取最后一项）；项为函数时调用它。 */
export function sequenceAnswer(items: unknown[]) {
  let n = 0;
  return () => {
    const item = items[Math.min(n, items.length - 1)];
    n += 1;
    return typeof item === "function" ? (item as () => unknown)() : item;
  };
}
export const never = () => new Promise(() => undefined);
export const fail = (message: string) => () => {
  throw new Error(message);
};

// ---------------- 改模型弹窗 ----------------
export const SET_CATALOG: CatalogModel[] = [
  {
    id: "kimi-k3",
    label: "Kimi K3",
    caps: { thinking: true, fast: true, context: true, effort: false },
    context_window: 400_000,
    supported_windows: [128_000, 256_000, 512_000, 1_000_000],
  },
  {
    id: "gpt-5.5",
    label: "GPT 5.5",
    caps: { thinking: false, fast: false, context: true, effort: true },
    context_window: 1_000_000,
    supported_windows: [1_000_000],
  },
  { id: "embed-x", label: "Embed X", caps: { thinking: false, fast: false, context: false, effort: false } },
  { id: "think-only", label: "Think Only", caps: { thinking: true, fast: false, context: true, effort: false } },
];

// ---------------- 上下文环 ----------------
export function snap(overrides: Partial<ContextUsageSnapshot> = {}): ContextUsageSnapshot {
  return {
    schema_version: 2,
    session_id: "default",
    source: "measured",
    sample_id: "sample-1",
    snapshot_id: "snapshot-1",
    version: 1,
    availability: "available",
    has_measurement: true,
    model: "deepseek-v4-pro",
    prompt_tokens: 0,
    completion_tokens: 0,
    cached_tokens: 0,
    context_window: 1_000_000,
    effective_ceiling: 950_000,
    compact_at: 750_000,
    recall_sweet: 384_000,
    updated_at: 0,
    ...overrides,
  } as ContextUsageSnapshot;
}

