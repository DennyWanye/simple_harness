// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/** 任务列表的 5 段进度条：创建 → 规划 → 执行 → 验证 → 完成（live-view 方案第 6 步）。 */
import { STAGES, progressOf } from "./progress";

export function MissionProgress({ status, uiState, counts }: {
  status: string; uiState?: string; counts?: { completed: number; total: number };
}) {
  const { stage, ended } = progressOf(status, uiState, counts?.total ?? 0);
  const current = ended === "failed" ? "#ef4444" : ended === "cancelled" ? "#94a3b8" : "#3b82f6";
  const label = STAGES[stage] + (ended === "failed" ? "（失败）" : ended === "cancelled" ? "（已取消）" : "");
  return (
    <span data-testid="mission-progress" aria-label={"进度：" + label} title={"进度：" + label}
      style={{ display: "flex", alignItems: "center", gap: 6, width: "100%" }}>
      <span style={{ display: "flex", gap: 2, flex: 1 }}>
        {STAGES.map((name, index) => (
          <span key={name} style={{ flex: 1, height: 4, borderRadius: 2,
            background: index < stage ? "#22c55e" : index === stage ? (stage === 4 ? "#22c55e" : current) : "#94a3b833" }} />
        ))}
      </span>
      {counts && counts.total > 0 && <span style={{ fontSize: 11, opacity: 0.75 }}>{counts.completed + "/" + counts.total}</span>}
    </span>
  );
}
