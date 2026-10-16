// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

export function buildAutoResumeBannerText(attempts: number, max: number): string {
  return `🔄 agent 自愈中... (尝试 ${attempts}/${max})`;
}

export function shouldShowAutoResumeBanner(attempts: number, inflight: boolean): boolean {
  return attempts > 0 && inflight;
}
