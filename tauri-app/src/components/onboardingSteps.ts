// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

export type OnboardingTestState = "idle" | "testing" | "ok" | "failed";

/** The distinct onboarding step kinds. */
export type OnboardingStepId = "welcome" | "connectModel" | "ready";

export interface OnboardingStepDef {
  id: OnboardingStepId;
}

/**
 * Ordered step list. 保留 edition 形参以免惊动调用方签名与既有单测；
 * 2026-08-09 起只有一条路径，恒返回三步。Pure — unit-testable.
 */
export function stepsForEdition(_edition?: string): OnboardingStepDef[] {
  return [{ id: "welcome" }, { id: "connectModel" }, { id: "ready" }];
}

/**
 * Pure: may the user advance from the step at `index` of `steps`?
 *  - last step                → false (host shows "完成", not "下一步")
 *  - a `connectModel` step     → only when the connection test passed
 *  - any other step            → always (intro-style steps)
 * Exported so it can be unit-tested without a DOM.
 */
export function nextStepAllowed(
  steps: OnboardingStepDef[],
  index: number,
  testState: OnboardingTestState,
): boolean {
  const step = steps[index];
  if (!step) return false;
  if (index >= steps.length - 1) return false;
  if (step.id === "connectModel") return testState === "ok";
  return true;
}
