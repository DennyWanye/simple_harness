// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  mergeCapabilityOperations,
  reduceCapabilityOperationEvent,
  redactCapabilityText,
  type CapabilityOperation,
} from "../types/capabilities";
import { CapabilityOperationCard } from "./CapabilityOperationCard";

afterEach(cleanup);

function operation(
  overrides: Partial<CapabilityOperation> = {},
): CapabilityOperation {
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
      {
        artifact_id: "artifact-1",
        name: "project report",
        kind: "json",
        ref: "artifact://godot-check",
      },
    ],
    verification_receipts: [
      {
        receipt_id: "receipt-1",
        status: "passed",
        summary: "manifest hash and healthcheck passed",
        ref: "receipt://verification-1",
      },
    ],
    event_seq: 2,
    ...overrides,
  };
}

describe("CapabilityOperationCard", () => {
  it("shows progress, artifacts, receipts and every recovery action", () => {
    const onAction = vi.fn();
    render(
      <CapabilityOperationCard operation={operation()} onAction={onAction} />,
    );

    expect(
      screen.getByTestId("capability-operation-operation-1").textContent,
    ).toContain("候选运行时已就绪");
    expect(screen.getByText("Auto 已授权（审计记录）")).toBeTruthy();
    expect(screen.getByText("project report")).toBeTruthy();
    expect(screen.getByText(/manifest hash/)).toBeTruthy();

    for (const action of ["取消", "重试", "回滚", "卸载"]) {
      fireEvent.click(screen.getByRole("button", { name: action }));
    }
    expect(onAction.mock.calls.map((call) => call[0])).toEqual([
      "cancel",
      "retry",
      "rollback",
      "uninstall",
    ]);
  });

  it("redacts credentials and keeps long diagnostic text collapsed", () => {
    const longResult = `api_key=sk-supersecret999 ${"诊断内容".repeat(70)}`;
    const rendered = render(
      <CapabilityOperationCard
        operation={operation({
          latest_result: longResult,
          error_message: "Authorization: Bearer tsk_privatecredential",
          recovery_hint: "--token key_privatecredential retry",
          status: "failed",
        })}
      />,
    );

    expect(rendered.container.textContent).not.toContain("supersecret999");
    expect(rendered.container.textContent).not.toContain("privatecredential");
    expect(rendered.container.textContent).toContain("[REDACTED]");
    const folded = rendered.container.querySelector("details");
    expect(folded).not.toBeNull();
    expect(folded?.hasAttribute("open")).toBe(false);
  });

  it("distinguishes a Manual external wait from Auto audit state", () => {
    render(
      <CapabilityOperationCard
        operation={operation({
          authorization_mode: "manual",
          status: "waiting_external",
        })}
      />,
    );
    expect(screen.getByText("Manual：等待授权")).toBeTruthy();
    expect(screen.queryByText(/Auto 已授权/)).toBeNull();
  });
});

describe("capability operation reducers", () => {
  it("accepts newer pushes and ignores stale event sequences", () => {
    const current = [operation({ event_seq: 4, latest_result: "new" })];
    const stale = operation({ event_seq: 3, latest_result: "stale" });
    const fresh = operation({
      event_seq: 5,
      phase: "published",
      status: "succeeded",
      latest_result: "published",
    });

    expect(mergeCapabilityOperations(current, [stale])[0].latest_result).toBe(
      "new",
    );
    expect(mergeCapabilityOperations(current, [fresh])[0]).toMatchObject({
      event_seq: 5,
      phase: "published",
      status: "succeeded",
      latest_result: "published",
    });
  });

  it("reduces a partial progress event onto the safe operation projection", () => {
    const reduced = reduceCapabilityOperationEvent([operation()], {
      operation: {
        operation_id: "operation-1",
        event_seq: 3,
        phase: "publish_intent",
        current_validation: "publishing",
      },
    });
    expect(reduced[0]).toMatchObject({
      capability_id: "godot",
      event_seq: 3,
      phase: "publish_intent",
      current_validation: "publishing",
    });
  });

  it("redacts common token and secret formats", () => {
    expect(
      redactCapabilityText(
        "token=abc123456; --api-key sk_secret123 Authorization: Bearer xyz789",
      ),
    ).toBe(
      "token=[REDACTED]; --api-key [REDACTED] Authorization: Bearer [REDACTED]",
    );
  });
});
