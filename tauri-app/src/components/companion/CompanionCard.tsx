// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { CSSProperties } from "react";

import type { CompanionEvent } from "../../types/messages";

export interface CompanionCardProps {
  event: CompanionEvent;
  onOpenDetail: (event: CompanionEvent) => void;
  onAction: (
    event: CompanionEvent,
    action: string,
    allow?: boolean,
  ) => void | Promise<void>;
  busy?: boolean;
}

const terminalStatuses = new Set([
  "confirmed",
  "rejected",
  "resolved",
  "expired",
  "rolled_back",
  "forgotten",
  "stale",
]);

function shortHash(value?: string): string {
  if (!value) return "无";
  return value;
}

function isReadOnly(event: CompanionEvent): boolean {
  return event.tombstone === true ||
    terminalStatuses.has(event.decision?.status || "") ||
    event.notification.available_actions.length === 0;
}

function isInvalid(event: CompanionEvent): boolean {
  return event.tombstone === true ||
    terminalStatuses.has(event.decision?.status || "");
}

export function CompanionCard({
  event,
  onOpenDetail,
  onAction,
  busy = false,
}: CompanionCardProps) {
  const { notification, decision } = event;
  const readOnly = isReadOnly(event);
  const invalid = isInvalid(event);
  const canViewDetail = !!notification.detail_ref && !event.tombstone;

  return (
    <article
      data-testid={`companion-card-${notification.notification_id}`}
      style={{
        width: "100%",
        boxSizing: "border-box",
        border: event.importance === "important"
          ? "1px solid rgba(251,191,36,0.52)"
          : "1px solid rgba(129,140,248,0.36)",
        borderRadius: 12,
        padding: 12,
        background: "linear-gradient(145deg, rgba(30,35,56,.96), rgba(19,24,39,.96))",
        color: "#e5e7eb",
      }}
    >
      <div style={{ fontSize: 12, color: "#a5b4fc", marginBottom: 8 }}>
        伙伴成长 · {notification.kind}
      </div>
      <div style={{ display: "grid", gap: 7, fontSize: 13, lineHeight: 1.55 }}>
        {event.tombstone ? (
          <div>{notification.summary}</div>
        ) : (
          <>
            <div><strong>改了什么：</strong>{notification.summary}</div>
            <div><strong>为什么：</strong>证据与原因已记录，可在详情中查看。</div>
            <div><strong>评测：</strong>评测报告与当前生效状态以详情页为准。</div>
          </>
        )}
      </div>

      {decision?.kind === "evaluation_authorization" && (
        <div data-testid="companion-evaluation-decision" style={decisionBoxStyle}>
          <div>候选包：{shortHash(decision.candidate_package_hash)}</div>
          <div>代码摘要：{shortHash(decision.candidate_code_digest)}</div>
          <div>评测套件：{shortHash(decision.suite_hash)}</div>
          {decision.status === "confirmed" && (
            <strong>仅已授权本机评测，尚未激活。</strong>
          )}
          {!readOnly && (
            <div style={buttonRowStyle}>
              <button
                type="button"
                disabled={busy}
                onClick={() => void onAction(event, "evaluation_authorization", true)}
              >
                允许在本机运行这份 exact code 进行评测（无 OS 沙箱，代码可直接访问本机文件/网络/凭据）
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void onAction(event, "evaluation_authorization", false)}
              >
                拒绝评测
              </button>
            </div>
          )}
        </div>
      )}

      {decision?.kind === "activation" && (
        <div data-testid="companion-activation-decision" style={decisionBoxStyle}>
          <div>包摘要：{shortHash(decision.candidate_package_hash)}</div>
          <div>代码摘要：{shortHash(decision.candidate_code_digest)}</div>
          <strong style={{ color: "#fbbf24" }}>
            激活会让此代码以后可被调用；Job 仅管理生命周期，不提供 OS 沙箱，代码可直接访问本机文件/网络/凭据。
          </strong>
          <div>本次确认不代表允许以后任何外部发送、删除或付费动作。</div>
          {!readOnly && (
            <div style={buttonRowStyle}>
              <button
                type="button"
                disabled={busy}
                onClick={() => void onAction(event, "activation", true)}
              >
                允许激活 exact package/code
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void onAction(event, "activation", false)}
              >
                拒绝激活
              </button>
            </div>
          )}
        </div>
      )}

      {decision?.kind === "action_confirmation" && (
        <div data-testid="companion-action-confirmation" style={decisionBoxStyle}>
          <div>工具：{decision.tool_name}</div>
          <div>目标：{decision.redacted_target_summary}</div>
          <div>参数摘要：{shortHash(decision.args_hash)}</div>
          <div>有效期至：{decision.expires_at}</div>
          {!readOnly && (
            <div style={buttonRowStyle}>
              <button
                type="button"
                disabled={busy}
                onClick={() => void onAction(event, "action_confirmation", true)}
              >
                允许这一次操作
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void onAction(event, "action_confirmation", false)}
              >
                拒绝这一次操作
              </button>
            </div>
          )}
        </div>
      )}

      <div style={{ ...buttonRowStyle, marginTop: 10 }}>
        {canViewDetail && (
          <button type="button" onClick={() => onOpenDetail(event)}>
            查看详情
          </button>
        )}
        {!readOnly && notification.available_actions.includes("rollback") && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void onAction(event, "rollback")}
          >
            撤销
          </button>
        )}
        {!readOnly && notification.available_actions.includes("forget") && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void onAction(event, "forget")}
          >
            遗忘
          </button>
        )}
        {invalid && <span data-testid="companion-card-stale">已失效</span>}
      </div>
    </article>
  );
}

const decisionBoxStyle: CSSProperties = {
  marginTop: 10,
  padding: 9,
  borderRadius: 9,
  background: "rgba(15,23,42,.72)",
  display: "grid",
  gap: 5,
  fontSize: 12,
  overflowWrap: "anywhere",
};

const buttonRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 7,
  alignItems: "center",
};
