// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { CompanionEvent } from "../../types/messages";

export interface CompanionActionCommand {
  commandKind: string;
  body: Record<string, string | number | boolean>;
}

export function shouldRefreshCompanionProjection(message: unknown): boolean {
  if (!message || typeof message !== "object" || Array.isArray(message)) {
    return false;
  }
  const source = message as {
    type?: unknown;
    payload?: { code?: unknown };
  };
  return source.type === "companion_control_rechallenge" &&
    source.payload?.code === "companion_detail_changed";
}

export function resolveCompanionDetailSelection(
  selected: CompanionEvent | null,
  currentEvents: CompanionEvent[],
): CompanionEvent | null {
  if (!selected) return null;
  const current = currentEvents.find(
    (event) => event.event_id === selected.event_id,
  );
  if (
    !current ||
    current.tombstone === true ||
    !current.notification.detail_ref
  ) {
    return null;
  }
  return current;
}

export function companionActionCommand(
  event: CompanionEvent,
  action: string,
  allow?: boolean,
): CompanionActionCommand | null {
  const decision = event.decision;
  if (decision?.kind === "action_confirmation") {
    return {
      commandKind: "companion_growth_action_decision",
      // Deliberately only these two values. Owner/run/session/nonce/version
      // are reconstructed by the backend from the trusted decision id.
      body: { decision_id: decision.decision_id, allow: allow === true },
    };
  }
  if (decision?.kind === "evaluation_authorization") {
    return {
      commandKind: "companion_growth_evaluation_decision",
      body: {
        candidate_id: decision.candidate_id,
        candidate_revision: decision.candidate_revision,
        candidate_package_hash: decision.candidate_package_hash,
        ...(decision.candidate_code_digest
          ? { candidate_code_digest: decision.candidate_code_digest }
          : {}),
        suite_hash: decision.suite_hash,
        runner_policy_hash: decision.runner_policy_hash,
        nonce: decision.nonce,
        decision_version: decision.decision_version,
        expires_at: decision.expires_at,
        allow: allow === true,
      },
    };
  }
  if (decision?.kind === "activation") {
    return {
      commandKind: "companion_growth_activation_decision",
      body: {
        candidate_id: decision.candidate_id,
        pack_id: decision.pack_id,
        candidate_version: decision.candidate_version,
        candidate_package_hash: decision.candidate_package_hash,
        ...(decision.candidate_code_digest
          ? { candidate_code_digest: decision.candidate_code_digest }
          : {}),
        evaluation_report_hash: decision.evaluation_report_hash,
        risk_assessment_hash: decision.risk_assessment_hash,
        scope: decision.scope,
        scope_key: decision.scope_key,
        owner_key: decision.owner_key,
        expected_binding_generation: decision.expected_binding_generation,
        nonce: decision.nonce,
        decision_version: decision.decision_version,
        expires_at: decision.expires_at,
        allow: allow === true,
        activation_risk_ack: allow === true && decision.candidate_code_digest
          ? "persistent_local_code_no_os_sandbox"
          : "none",
      },
    };
  }
  if (action === "rollback") {
    return {
      commandKind: "companion_rollback",
      body: {
        notification_id: event.notification.notification_id,
        expected_detail_version: event.notification.detail_version,
      },
    };
  }
  if (action === "forget") {
    return {
      commandKind: "companion_forget",
      body: {
        notification_id: event.notification.notification_id,
        expected_detail_version: event.notification.detail_version,
      },
    };
  }
  return null;
}
