// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import React from "react";
import type { MissionRow, OrchestrationStatus } from "../stores/missionsStore";
import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import { MissionProgress } from "./liveGraph/MissionProgress";
import { asText as text, backgroundTrouble, type MissionEvent } from "../stores/missionsStore";
import { STALE_NOTE } from "../ws/orchestrationContracts";
import {
  TERMINAL,
  RECOVERY_ISOLATED_TEXT,
  stateLabel,
  show,
  box,
  button,
  muted,
  heading,
} from "./missionsViewShared";
import type { Json } from "./missionsViewShared";

export interface MissionListAsideProps {
  status: OrchestrationStatus | null;
  setCreating: React.Dispatch<React.SetStateAction<boolean>>;
  store: {
    selectedId: string | null;
    detail: Record<string, unknown> | null;
    status: OrchestrationStatus | null;
    policy: Record<string, unknown> | null;
    missions: MissionRow[];
    error: string | null;
    listError: string | null;
    selectedEvents: MissionEvent[] | undefined;
    selectedHasMore: boolean;
    selectedLoading: boolean;
    select: (missionId: string | null) => void;
    setError: (error: string | null) => void;
  };
  selectedRef: React.RefObject<string | null>;
  setArtifact: React.Dispatch<React.SetStateAction<Json | null>>;
  selectedId: string | null;
  open: (missionId: string) => void;
  stalled: Record<string, number>;
  drift: Json[];
}

/** 任务列表侧栏（新建入口、行与状态徽标、卡住与漂移提示）。9H 从 MissionsView 的渲染里原样挪出，只读属性、不持有状态。 */
export function MissionListAside({
  status,
  setCreating,
  store,
  selectedRef,
  setArtifact,
  selectedId,
  open,
  stalled,
  drift,
}: MissionListAsideProps) {
  return (
    <aside
      style={{
        width: 280,
        flexShrink: 0,
        borderRight: `1px solid ${tokens.color.surface.hairline}`,
        padding: tokens.space.md,
        display: "flex",
        flexDirection: "column",
        gap: tokens.space.sm,
        overflowY: "auto",
      }}
    >
      {backgroundTrouble(status) ? (
        <div
          role="alert"
          data-testid="background-trouble"
          style={{ ...box, borderColor: tokens.color.warning.bg, flexShrink: 0 }}
        >
          {backgroundTrouble(status)}
        </div>
      ) : null}
      {/* flexShrink 0: with many rows the column scrolls instead of squashing them (2026-09-25) */}
      <button
        type="button"
        style={{ ...button, flexShrink: 0 }}
        onClick={() => {
          setCreating(true);
          store.select(null);
          selectedRef.current = null;
          setArtifact(null);
        }}
      >
        新建任务
      </button>
      {store.listError ? (
        <div
          role="alert"
          data-testid="mission-list-error"
          style={{ ...box, borderColor: tokens.color.warning.bg, flexShrink: 0 }}
        >
          {store.listError}
          {store.missions.length > 0 ? STALE_NOTE : ""}
        </div>
      ) : null}
      {store.missions.length === 0 ? (
        <div style={{ color: dark.textMuted }}>还没有任务。点「新建任务」开始。</div>
      ) : (
        store.missions.map((row) => (
          <button
            key={row.id}
            type="button"
            data-testid={`mission-row-${row.id}`}
            data-status={row.status}
            data-ui-state={row.ui_state || undefined}
            aria-current={selectedId === row.id ? "true" : undefined}
            onClick={() => open(row.id)}
            style={{
              ...button,
              height: "auto",
              flexShrink: 0,
              padding: tokens.space.sm,
              textAlign: "left",
              display: "flex",
              flexDirection: "column",
              gap: 2,
            }}
          >
            <span>{row.goal}</span>
            <span style={muted}>
              {stateLabel(row.ui_state, row.status)}
              {row.pending_approvals ? ` · 待审批：${row.pending_approvals}` : ""}
              {row.blocked && row.ui_state !== "unknown" ? " · 结果未知" : ""}
              {stalled[row.id] ? ` · ${stalled[row.id]} 个步骤可能卡住` : ""}
            </span>
            {row.recovery_isolated && !TERMINAL.has(row.status) && (
              <span
                data-testid={`mission-isolated-${row.id}`}
                style={{ color: dark.warning }}
                title={`对不上的表：${row.recovery_isolated.tables.join("、") || "未知"}`}
              >
                {RECOVERY_ISOLATED_TEXT}
              </span>
            )}
            <MissionProgress status={row.status} uiState={row.ui_state} counts={row.task_counts} />
          </button>
        ))
      )}
      <div style={{ ...box, marginTop: "auto", flexShrink: 0 }} aria-label="策略状态">
        <div style={heading}>策略（只读）</div>
        <div style={muted}>当前版本：{text(store.policy?.active_version_id) || "—"}</div>
        {drift.map((item) => (
          <div
            key={text(item.name)}
            role="status"
            style={{
              ...box,
              borderColor: tokens.color.warning.bg,
              padding: tokens.space.sm,
              marginTop: tokens.space.sm,
              fontSize: tokens.text.xs.size,
            }}
          >
            {`配置与生效策略不一致：${text(item.name)} 配置 ${show(item.config)}，生效 ${show(item.active)}（修改要经策略晋级才生效）`}
          </div>
        ))}
      </div>
    </aside>
  );
}
