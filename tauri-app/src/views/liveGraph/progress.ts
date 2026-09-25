// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/** 列表进度条的纯逻辑。 */

export const STAGES = ["创建", "规划", "执行", "验证", "完成"] as const;

export type Progress = { stage: number; ended: "failed" | "cancelled" | null };

/** 由任务状态、界面状态词和子任务数得出当前在第几段；失败/取消停在当时所在的一段。 */
export function progressOf(status: string, uiState: string | undefined, total: number): Progress {
  if (status === "COMPLETED") return { stage: 4, ended: null };
  const reached = uiState === "verifying" ? 3 : status === "ACTIVE" || total > 0 ? 2 : status === "PLANNING" ? 1 : 0;
  if (status === "FAILED") return { stage: reached, ended: "failed" };
  if (status === "CANCELLED") return { stage: reached, ended: "cancelled" };
  return { stage: reached, ended: null };
}
