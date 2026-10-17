// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import {
  capabilityOperationProgress,
  redactCapabilityText,
  type CapabilityOperation,
  type CapabilityOperationAction,
} from "../types/capabilities";
import { dark } from "../theme/components";
import "./CapabilityOperationCard.styles.css";

interface Props {
  operation: CapabilityOperation;
  onAction?: (action: CapabilityOperationAction, operation: CapabilityOperation) => void;
  compact?: boolean;
}

const STATUS_LABEL: Record<CapabilityOperation["status"], string> = {
  queued: "排队中",
  running: "进行中",
  waiting_external: "等待外部操作",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  unknown: "状态待确认",
};

const KIND_LABEL: Record<CapabilityOperation["kind"], string> = {
  activate: "启用",
  install: "安装",
  update: "更新",
  build: "生成",
  repair: "修复",
  rollback: "回滚",
  uninstall: "卸载",
};

const ACTION_LABEL: Record<CapabilityOperationAction, string> = {
  cancel: "取消",
  retry: "重试",
  rollback: "回滚",
  uninstall: "卸载",
};

const PHASE_LABEL: Record<CapabilityOperation["phase"], string> = {
  planned: "已规划",
  staged: "已暂存",
  verified: "清单已验证",
  environment_ready: "环境已就绪",
  candidate_ready: "候选运行时已就绪",
  publish_intent: "准备发布",
  catalog_swapped: "目录已切换",
  bound: "能力已绑定",
  published: "已发布",
};

function SafeText({ children, collapseAt = 180 }: { children: unknown; collapseAt?: number }) {
  const safe = redactCapabilityText(children);
  if (!safe) return null;
  if (safe.length <= collapseAt) return <span>{safe}</span>;
  return (
    <details>
      <summary>{safe.slice(0, collapseAt)}…</summary>
      <div className="sh-CapabilityOperationCard-1">{safe}</div>
    </details>
  );
}

export function CapabilityOperationCard({ operation, onAction, compact = false }: Props) {
  const progress = capabilityOperationProgress(operation);
  const failed = operation.status === "failed" || operation.status === "unknown";
  const accent = failed
    ? "#f87171"
    : operation.status === "succeeded"
      ? "#34d399"
      : operation.status === "waiting_external"
        ? "#fbbf24"
        : "#38bdf8";

  return (
    <article
      data-testid={`capability-operation-${operation.operation_id}`}
      data-status={operation.status}
      style={{
        display: "grid",
        gap: compact ? 6 : 10,
        padding: compact ? "9px 10px" : 14,
        borderRadius: 9,
        border: `1px solid ${accent}66`,
        background: compact ? "rgba(15,23,42,0.72)" : dark.card,
        color: dark.text,
        boxShadow: compact ? undefined : "0 8px 24px rgba(0,0,0,0.20)",
      }}
    >
      <header className="sh-CapabilityOperationCard-2">
        <div className="sh-CapabilityOperationCard-3">
          <strong>{redactCapabilityText(operation.capability_name)}</strong>
          <span className="sh-CapabilityOperationCard-4">
            {KIND_LABEL[operation.kind]} · {PHASE_LABEL[operation.phase]}
          </span>
        </div>
        <span
          style={{
            flexShrink: 0,
            color: accent,
            border: `1px solid ${accent}66`,
            borderRadius: 999,
            padding: "2px 7px",
            fontSize: 11,
          }}
        >
          {STATUS_LABEL[operation.status]}
        </span>
      </header>

      <div
        role="progressbar"
        aria-label={`${operation.capability_name}操作进度`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progress}
        className="sh-CapabilityOperationCard-5"
      >
        <div
          style={{
            width: `${progress}%`,
            height: "100%",
            background: accent,
            transition: "width 180ms ease",
          }}
        />
      </div>

      <div className="sh-CapabilityOperationCard-6">
        <span>{progress}%</span>
        <span data-testid="capability-authorization-state">
          {operation.authorization_mode === "auto"
            ? "Auto 已授权（审计记录）"
            : operation.status === "waiting_external"
              ? "Manual：等待授权"
              : "Manual：按需确认"}
        </span>
      </div>

      {operation.current_validation ? (
        <section>
          <strong className="sh-CapabilityOperationCard-7">正在验证</strong>
          <div className="sh-CapabilityOperationCard-8">
            <SafeText>{operation.current_validation}</SafeText>
          </div>
        </section>
      ) : null}

      {operation.latest_result ? (
        <section>
          <strong className="sh-CapabilityOperationCard-7">最近结果</strong>
          <div className="sh-CapabilityOperationCard-8">
            <SafeText>{operation.latest_result}</SafeText>
          </div>
        </section>
      ) : null}

      {operation.error_message ? (
        <section role="alert" className="sh-CapabilityOperationCard-9">
          <strong className="sh-CapabilityOperationCard-7">
            {operation.error_code ? `失败：${operation.error_code}` : "操作失败"}
          </strong>
          <div className="sh-CapabilityOperationCard-8">
            <SafeText>{operation.error_message}</SafeText>
          </div>
          {operation.recovery_hint ? (
            <div className="sh-CapabilityOperationCard-10">
              恢复建议：<SafeText>{operation.recovery_hint}</SafeText>
            </div>
          ) : null}
        </section>
      ) : null}

      {!compact && operation.artifacts.length > 0 ? (
        <section>
          <strong className="sh-CapabilityOperationCard-7">产物</strong>
          <ul className="sh-CapabilityOperationCard-11">
            {operation.artifacts.map((artifact) => (
              <li key={artifact.artifact_id}>
                <SafeText>{artifact.name}</SafeText>
                {artifact.kind ? ` · ${redactCapabilityText(artifact.kind)}` : ""}
                {artifact.ref ? (
                  <code className="sh-CapabilityOperationCard-12">
                    {" "}
                    {redactCapabilityText(artifact.ref)}
                  </code>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {!compact && operation.verification_receipts.length > 0 ? (
        <section>
          <strong className="sh-CapabilityOperationCard-7">Verification Receipt</strong>
          <ul className="sh-CapabilityOperationCard-11">
            {operation.verification_receipts.map((receipt) => (
              <li key={receipt.receipt_id}>
                {receipt.status === "passed"
                  ? "通过"
                  : receipt.status === "failed"
                    ? "失败"
                    : "待确认"}
                {" · "}
                <SafeText>{receipt.summary}</SafeText>
                {receipt.ref ? (
                  <code className="sh-CapabilityOperationCard-12">
                    {" "}
                    {redactCapabilityText(receipt.ref)}
                  </code>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {onAction && operation.available_actions.length > 0 ? (
        <footer className="sh-CapabilityOperationCard-13">
          {operation.available_actions.map((action) => (
            <button
              key={action}
              type="button"
              onClick={() => onAction(action, operation)}
              style={{
                border: `1px solid ${dark.borderStrong}`,
                borderRadius: 6,
                padding: "5px 9px",
                background: action === "uninstall" ? "rgba(127,29,29,0.18)" : dark.card,
                color: action === "uninstall" ? "#fca5a5" : dark.text,
                cursor: "pointer",
              }}
            >
              {ACTION_LABEL[action]}
            </button>
          ))}
        </footer>
      ) : null}
    </article>
  );
}
