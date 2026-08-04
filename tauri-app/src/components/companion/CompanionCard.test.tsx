// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CompanionEvent } from "../../types/messages";
import {
  companionActionCommand,
  resolveCompanionDetailSelection,
} from "./actionProjection";
import { CompanionCard } from "./CompanionCard";

function baseEvent(overrides: Partial<CompanionEvent> = {}): CompanionEvent {
  return {
    event_id: "event-1",
    profile_id: "profile-a",
    profile_generation: 1,
    session_id: "default",
    seq: 1,
    notification: {
      notification_id: "notice-1",
      kind: "activation",
      summary: "新增本地能力",
      detail_ref: "detail-1",
      detail_version: "detail-v1",
      available_actions: ["confirm", "rollback", "forget"],
    },
    ...overrides,
  };
}

afterEach(cleanup);

describe("CompanionCard", () => {
  it("separates exact-code evaluation authorization from activation", () => {
    const onAction = vi.fn();
    const event = baseEvent({
      decision: {
        kind: "evaluation_authorization",
        candidate_id: "candidate-1",
        candidate_revision: 2,
        candidate_package_hash: "package-hash",
        candidate_code_digest: "evaluation-code-digest",
        suite_hash: "suite-hash",
        runner_policy_hash: "runner-policy-hash",
        nonce: "evaluation-nonce",
        decision_version: 4,
        expires_at: "2026-07-26T00:00:00Z",
        status: "open",
      },
    });
    render(
      <CompanionCard
        event={event}
        onOpenDetail={vi.fn()}
        onAction={onAction}
      />,
    );

    const button = screen.getByRole("button", {
      name: /允许在本机运行这份 exact code 进行评测/,
    });
    expect(button.textContent).toContain("无 OS 沙箱");
    expect(button.textContent).toContain("本机文件/网络/凭据");
    expect(screen.queryByRole("button", { name: /允许激活/ })).toBeNull();
    fireEvent.click(button);
    expect(onAction).toHaveBeenCalledWith(
      event,
      "evaluation_authorization",
      true,
    );
  });

  it("shows the persistent activation warning and exact digests", () => {
    render(
      <CompanionCard
        event={baseEvent({
          decision: {
            kind: "activation",
            candidate_id: "candidate-1",
            pack_id: "pack-1",
            candidate_version: "1.0.0",
            candidate_package_hash: "package-hash-exact",
            candidate_code_digest: "code-digest-exact",
            evaluation_report_hash: "report-hash",
            risk_assessment_hash: "risk-hash",
            scope: "user",
            scope_key: "profile-a",
            owner_key: "companion:profile-a:1",
            expected_binding_generation: 8,
            nonce: "activation-nonce",
            decision_version: 2,
            expires_at: "2026-07-26T00:00:00Z",
            status: "open",
          },
        })}
        onOpenDetail={vi.fn()}
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByText(/激活会让此代码以后可被调用/).textContent).toContain(
      "Job 仅管理生命周期，不提供 OS 沙箱",
    );
    expect(screen.getByText(/本次确认不代表允许以后任何外部发送/)).toBeTruthy();
    expect(screen.getByRole("button", {
      name: "允许激活 exact package/code",
    })).toBeTruthy();
  });

  it("renders resolved decisions read-only as stale", () => {
    render(
      <CompanionCard
        event={baseEvent({
          notification: {
            ...baseEvent().notification,
            available_actions: ["confirm"],
          },
          decision: {
            kind: "action_confirmation",
            decision_id: "decision-1",
            run_id: "run-1",
            call_id: "call-1",
            effect_id: "effect-1",
            tool_name: "send_mail",
            redacted_target_summary: "收件人：已脱敏",
            args_hash: "args-hash",
            capability_hash: "capability-hash",
            scope_hash: "scope-hash",
            expires_at: "2026-07-26T00:00:00Z",
            status: "resolved",
          },
        })}
        onOpenDetail={vi.fn()}
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByTestId("companion-card-stale").textContent).toBe("已失效");
    expect(screen.queryByRole("button", { name: "允许这一次操作" })).toBeNull();
  });
});

describe("typed Companion action projection", () => {
  it("closes an open detail view as soon as its projection is retracted", () => {
    const selected = baseEvent();
    const tombstone = baseEvent({
      tombstone: true,
      redaction_version: 1,
      notification: {
        ...baseEvent().notification,
        detail_ref: "",
        available_actions: [],
      },
    });
    expect(resolveCompanionDetailSelection(selected, [tombstone])).toBeNull();
  });

  it("sends action confirmation as decision_id + allow only", () => {
    const command = companionActionCommand(baseEvent({
      decision: {
        kind: "action_confirmation",
        decision_id: "decision-1",
        run_id: "run-secret",
        call_id: "call-secret",
        effect_id: "effect-secret",
        tool_name: "pay",
        redacted_target_summary: "已脱敏目标",
        args_hash: "args-hash",
        capability_hash: "capability-hash",
        scope_hash: "scope-hash",
        expires_at: "2026-07-26T00:00:00Z",
        status: "open",
      },
    }), "action_confirmation", true);

    expect(command).toEqual({
      commandKind: "companion_growth_action_decision",
      body: { decision_id: "decision-1", allow: true },
    });
  });

  it("keeps evaluation and activation nonce/ack projections separate", () => {
    const evaluation = companionActionCommand(baseEvent({
      decision: {
        kind: "evaluation_authorization",
        candidate_id: "candidate-1",
        candidate_revision: 1,
        candidate_package_hash: "package-hash",
        suite_hash: "suite-hash",
        runner_policy_hash: "runner-hash",
        nonce: "eval-nonce",
        decision_version: 1,
        expires_at: "2026-07-26T00:00:00Z",
      },
    }), "evaluation_authorization", true);
    const activation = companionActionCommand(baseEvent({
      decision: {
        kind: "activation",
        candidate_id: "candidate-1",
        pack_id: "pack-1",
        candidate_version: "1.0.0",
        candidate_package_hash: "package-hash",
        evaluation_report_hash: "report-hash",
        risk_assessment_hash: "risk-hash",
        scope: "user",
        scope_key: "profile-a",
        owner_key: "companion:profile-a:1",
        expected_binding_generation: 1,
        nonce: "activation-nonce",
        decision_version: 2,
        expires_at: "2026-07-26T00:00:00Z",
      },
    }), "activation", true);

    expect(evaluation?.body.nonce).toBe("eval-nonce");
    expect(evaluation?.body).not.toHaveProperty("activation_risk_ack");
    expect(activation?.body.nonce).toBe("activation-nonce");
    expect(activation?.body.activation_risk_ack).toBe("none");

    const executableActivation = companionActionCommand(baseEvent({
      decision: {
        kind: "activation",
        candidate_id: "candidate-code",
        pack_id: "pack-code",
        candidate_version: "1.0.0",
        candidate_package_hash: "package-code-hash",
        candidate_code_digest: "code-digest-exact",
        evaluation_report_hash: "report-code-hash",
        risk_assessment_hash: "risk-code-hash",
        scope: "user",
        scope_key: "profile-a",
        owner_key: "companion:profile-a:1",
        expected_binding_generation: 1,
        nonce: "activation-code-nonce",
        decision_version: 3,
        expires_at: "2026-07-26T00:00:00Z",
      },
    }), "activation", true);
    expect(executableActivation?.body.activation_risk_ack).toBe(
      "persistent_local_code_no_os_sandbox",
    );
  });
});
