// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

type ChatErrorPayload = {
  error?: unknown;
  detail?: unknown;
  reason?: unknown;
};

const PROVIDER_NOT_READY_PATTERNS = [
  "SDK Runtime ingress is closed",
  "SDK Runtime is unavailable",
  "Provider chain is empty",
  "no LLM provider is available",
];

export function chatErrorMessage(payload: ChatErrorPayload): string {
  const parts = [payload.error, payload.detail, payload.reason]
    .filter((value): value is string =>
      typeof value === "string" && value.trim().length > 0,
    )
    .map((value) => value.trim());
  const technical = parts.join(" — ");

  if (PROVIDER_NOT_READY_PATTERNS.some((pattern) => technical.includes(pattern))) {
    return "模型服务尚未就绪，请前往“设置 → LLM Providers”检查服务地址、模型和 API Key。";
  }
  return technical || "请求失败（后端未提供错误详情）";
}
