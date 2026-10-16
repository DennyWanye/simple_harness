// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 决策回复与分支补丁草稿：换了对象就清空，同一对象重渲染不清。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DecisionPanel } from "./DecisionPanel";
import { ReplayDialog } from "./ReplayDialog";
import type { CheckpointView, WorkflowDecisionView } from "./types";

afterEach(() => cleanup());

const decision = (version: number): WorkflowDecisionView => ({
  decision_id: "d1",
  run_id: "run-1",
  kind: "free_text",
  status: "open",
  prompt: "请补充说明",
  nonce: "n",
  version,
  created_at: 1,
});

const checkpoint = (id: string): CheckpointView => ({
  checkpoint_id: id,
  created_at: 1,
  status: "completed",
  requires_effect_confirmation: true,
});

const draftOf = (label: string) => (screen.getByLabelText(label) as HTMLTextAreaElement).value;

describe("DecisionPanel 草稿", () => {
  it("同一决策重渲染保留草稿，决策版本变了清空", () => {
    const handlers = { onResolve: vi.fn(), onReopen: vi.fn(), onCancel: vi.fn() };
    const { rerender } = render(<DecisionPanel decision={decision(1)} {...handlers} />);
    fireEvent.change(screen.getByLabelText("决策回复"), { target: { value: "继续" } });
    rerender(<DecisionPanel decision={decision(1)} {...handlers} submitting />);
    expect(draftOf("决策回复")).toBe("继续");
    rerender(<DecisionPanel decision={decision(2)} {...handlers} />);
    expect(draftOf("决策回复")).toBe("");
  });
});

describe("ReplayDialog 草稿", () => {
  it("关掉再打开、或换了 Checkpoint 时清空补丁与副作用确认", () => {
    const handlers = { onClose: vi.fn(), onSubmit: vi.fn() };
    const { rerender } = render(<ReplayDialog open checkpoint={checkpoint("c1")} {...handlers} />);
    const fill = () => {
      fireEvent.change(screen.getByLabelText("状态补丁"), { target: { value: '{"a": 1}' } });
      fireEvent.click(screen.getByRole("checkbox"));
    };
    const confirmed = () => (screen.getByRole("checkbox") as HTMLInputElement).checked;
    fill();
    rerender(<ReplayDialog open checkpoint={checkpoint("c1")} {...handlers} submitting />);
    expect(draftOf("状态补丁")).toBe('{"a": 1}');
    expect(confirmed()).toBe(true);

    rerender(<ReplayDialog open={false} checkpoint={checkpoint("c1")} {...handlers} />);
    rerender(<ReplayDialog open checkpoint={checkpoint("c1")} {...handlers} />);
    expect(draftOf("状态补丁")).toBe("");
    expect(confirmed()).toBe(false);

    fill();
    rerender(<ReplayDialog open checkpoint={checkpoint("c2")} {...handlers} />);
    expect(draftOf("状态补丁")).toBe("");
    expect(confirmed()).toBe(false);
  });
});
