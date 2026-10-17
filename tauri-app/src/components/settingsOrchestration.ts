// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// ---------------------------------------------------------------------------
// 任务与会话数据（2026-09-25 主流程优化条目 7）
//
// 用户决定：任务里 Agent 的会话数据（聊天记录、检索索引、检查记录）永久保留供审计，
// 不做自动回收、不做清理按钮。这里只显示占用，超过阈值（默认 5 GB）给一条提醒，
// 引导用户去「数据目录」迁移到更大的磁盘。数字由后台统计（orchestration_storage_get）。
// ---------------------------------------------------------------------------

export interface StorageUsageData {
  bytes: number;
  measured_at_ms: number | null;
  warn_bytes: number;
  over_warn: boolean;
  measured: boolean;
  breakdown: { key: string; label: string; bytes: number }[];
}

// ---------------------------------------------------------------------------
// 本月全局预算（原计划 §18.2 Global Budget）：这台机器所有任务
// 每个自然月合计的 token 上限（月配额）。只读说明：数值在 config.toml 的 [orchestration]
// global_monthly_max_tokens 里改，重启后生效。

export type GlobalBudgetData = {
  month: string;
  max_tokens: number;
  opened: boolean;
  used_tokens: number;
  reserved_tokens: number;
  remaining_tokens: number;
};

// ---------------------------------------------------------------------------
// 任务发布目录（NEXT-TG-1.0 §9）：带"发布"要求的任务只能把验收过的文件发布到这里。
// 后台校验目录存在、能硬链接、不与应用自己的数据目录重叠；保存后编排服务会重启一次
// （进行中的任务暂停几秒后自动接着做）。清空 = 撤销授权，已经发布的文件不动。
// ---------------------------------------------------------------------------

export type PublishDirData = {
  configured: string;
  publish: { enabled?: boolean; root?: string; reason?: string };
  active_missions: number;
};

export function buildPublishDirMessage(requestId: string, path?: string) {
  return path === undefined
    ? { type: "orchestration_publish_dir_get", request_id: requestId, payload: {} }
    : { type: "orchestration_publish_dir_set", request_id: requestId, payload: { path } };
}

// ================ 任务技能目录 (NEXT-TG-1.0 §11) ================
// 所有任务执行档位共用一个技能目录：在这里安装一次，各档位看到同一版本；暂停/退役一次，
// 各档位下一次使用就生效。准入要先经过评估：「开始评估」建一个试用任务，由执行者真实调用
// 这个技能，独立审阅通过后点「准入」（系统核对审阅证书，不自签通过）。

export type SkillPoolView = { state: string | null; usable: boolean; reasons: string[] };

export type SkillEvaluation = {
  mission_id: string | null;
  mission_status: string | null;
  passed: boolean;
  expired: boolean;
};

export type SkillCatalogueRow = {
  skill_ref: Record<string, unknown>;
  skill_id: string;
  version: number;
  name: string;
  description: string;
  state: string | null;
  pools: Record<string, SkillPoolView>;
  evaluation?: SkillEvaluation | null;
};

export type SkillCatalogueData = {
  owner: string | null;
  members: string[];
  skills: SkillCatalogueRow[];
};

export const SKILL_STATE: Record<string, string> = {
  QUARANTINED: "待评估",
  TRIAL: "评估中",
  ADMITTED: "可用",
  SUSPENDED: "已暂停",
  RETIRED: "已退役",
};

export function poolLabel(profileId: string): string {
  const m = /-(\d+)k-(thinking-)?v\d+$/.exec(profileId);
  return m ? `${m[1]}K${m[2] ? " 思考" : ""}` : profileId;
}

export function poolStatus(view: SkillPoolView): string {
  if (view.usable) return "可用";
  if (view.reasons.includes("DEPENDENCY_UNRESOLVED")) return "不可用（这个档位缺少所需工具）";
  if (view.reasons.includes("NOT_MIRRORED")) return "不可用（尚未同步）";
  return `不可用（${SKILL_STATE[view.state ?? ""] ?? "未知状态"}）`;
}

export function evaluationStatus(
  state: string | null,
  evaluation: SkillEvaluation | null | undefined,
): { text: string | null; action: "evaluate" | "admit" | null } {
  if (state === "QUARANTINED") return { text: "待评估", action: "evaluate" };
  if (state !== "TRIAL") return { text: null, action: null };
  if (!evaluation || !evaluation.mission_id) return { text: "评估未开始", action: "evaluate" };
  if (evaluation.passed) return { text: "评估已通过，可以准入", action: "admit" };
  if (evaluation.expired) return { text: "评估未通过（试用期已过）", action: "evaluate" };
  if (evaluation.mission_status === "FAILED")
    return { text: "评估未通过（试用任务失败）", action: "evaluate" };
  if (evaluation.mission_status === "CANCELLED")
    return { text: "评估未通过（试用任务已取消）", action: "evaluate" };
  return { text: "评估中（试用任务在任务编排页运行）", action: null };
}

export type SkillReply = { ok?: boolean; request_id?: unknown; data?: unknown; error?: string };

export const SKILL_DONE: Record<string, string> = {
  orchestration_skill_install_file_response: "已安装，各档位同步为同一版本（需评估准入后才能使用）",
  orchestration_skill_evaluate_response:
    "已开始评估：试用任务会在任务编排页运行，审阅通过后回到这里点「准入」",
  orchestration_skill_admit_response: "已准入，各档位下一次使用即生效",
};
