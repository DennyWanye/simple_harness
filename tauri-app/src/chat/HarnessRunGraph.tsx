// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useEffect, useRef } from "react";

import type { PublicRunPhaseTaxonomy } from "../types/messages";

export interface HarnessRunGraphNode {
  id: string;
  taxonomy: PublicRunPhaseTaxonomy;
  title: string;
  summary: string;
  status: string;
  statusLabel?: string;
}

interface Props {
  nodes: HarnessRunGraphNode[];
  selectedNodeId: string | null;
  currentNodeId: string | null;
  headline: string;
  subline?: string | null;
  onSelectNode: (nodeId: string) => void;
}

const COMPLETE = new Set(["completed", "completed_with_recovery", "succeeded", "settled", "ready"]);
const ACTIVE = new Set([
  "running",
  "claimed",
  "active",
  "prepared",
  "admitted",
  "starting",
]);
const WAITING = new Set(["waiting", "pending", "created"]);
const FAILED = new Set(["failed", "unknown", "cancelled", "rejected"]);

function nodeState(status: string): string {
  if (COMPLETE.has(status)) return "complete";
  if (ACTIVE.has(status)) return "active";
  if (WAITING.has(status)) return "waiting";
  if (FAILED.has(status)) return "failed";
  return "idle";
}

function nodeMark(status: string, index: number): string {
  if (COMPLETE.has(status)) return "✓";
  if (FAILED.has(status)) return "!";
  if (ACTIVE.has(status)) return "▶";
  if (WAITING.has(status)) return "…";
  return String(index + 1);
}

function statusText(status: string): string {
  return {
    completed: "已完成",
    completed_with_recovery: "修复后完成",
    running: "进行中",
    waiting: "等待中",
    failed: "失败",
    cancelled: "已取消",
    unknown: "信息不完整",
  }[status] ?? status;
}

export function HarnessRunGraph({
  nodes,
  selectedNodeId,
  currentNodeId,
  headline,
  subline,
  onSelectNode,
}: Props) {
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const foundCurrent = nodes.findIndex((node) => node.id === currentNodeId);
  const currentIndex = foundCurrent < 0 ? Math.max(0, nodes.length - 1) : foundCurrent;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !currentNodeId) return;
    const current = canvas.querySelector<HTMLElement>(
      ".harness-run-graph__row.is-current",
    );
    if (!current) return;
    canvas.scrollTop = Math.max(
      0,
      current.offsetTop + current.offsetHeight - canvas.clientHeight,
    );
  }, [currentNodeId, nodes.length]);

  return (
    <section className="harness-run-graph" data-testid="harness-run-graph">
      <header className="harness-run-graph__header">
        <div>
          <strong>运行图</strong>
          <span>沿着箭头看 Agent 现在走到哪里</span>
        </div>
        <span>
          {nodes.length === 0 ? "尚未开始" : `${currentIndex + 1}/${nodes.length}`}
        </span>
      </header>

      <div className="harness-run-graph__now" aria-live="polite">
        <span>{currentNodeId ? "当前" : "结果"}</span>
        <strong>{headline}</strong>
        {subline && <small>{subline}</small>}
      </div>

      {nodes.length === 0 ? (
        <div className="harness-run-graph__empty">Agent 还没有产生执行步骤</div>
      ) : (
        <div
          ref={canvasRef}
          className="harness-run-graph__canvas"
          role="list"
          aria-label="Agent 运行图"
        >
          {nodes.map((node, index) => {
            const state = nodeState(node.status);
            const statusLabel = node.statusLabel ?? statusText(node.status);
            const selected = node.id === selectedNodeId;
            const current = node.id === currentNodeId;
            return (
              <div
                key={node.id}
                role="listitem"
                className={[
                  "harness-run-graph__row",
                  `is-${state}`,
                  `is-${node.taxonomy}`,
                  selected ? "is-selected" : "",
                  current ? "is-current" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
              >
                {index > 0 && (
                  <span className="harness-run-graph__edge" aria-hidden="true">
                    ↓
                  </span>
                )}
                <button
                  type="button"
                  className="harness-run-graph__node"
                  aria-current={current ? "step" : undefined}
                  aria-pressed={selected}
                  aria-label={`第 ${index + 1} 步：${node.title}，${statusLabel}`}
                  onClick={() => onSelectNode(node.id)}
                >
                  <span className="harness-run-graph__mark" aria-hidden="true">
                    {nodeMark(node.status, index)}
                  </span>
                  <span className="harness-run-graph__content">
                    <span className="harness-run-graph__title">
                      <strong>{node.title}</strong>
                      <small>{statusLabel}</small>
                    </span>
                    <span className="harness-run-graph__summary">{node.summary}</span>
                  </span>
                </button>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
