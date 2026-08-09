// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * auth/ — 用户级账户体系入口。
 *
 * 现状：本次 commit 仅做 scaffold。OSS 主干代码未 wire 进来。
 * 既有的 sessionsStore / 后端 ProviderRegistry / Settings 面板等
 * provider 管理路径**保持原样**。等 Relay adapter 进闭源仓库 +
 * 调用方迁移到 adapter 入口时，再统一切换。
 *
 * 使用：
 *   import { getAuthAdapter } from "@/auth";
 *   const auth = getAuthAdapter();      // 当前 edition 决定的 adapter
 *   const user = auth.currentUser();
 *
 * 切换：构建期通过 Vite import.meta.env.VITE_AUTH_EDITION 决定。
 *   - "null"   → NullAuthAdapter
 *   - "manual" → ManualAuthAdapter (默认，且是唯一的产品路径)
 *
 * 2026-08-09：relay（托管账号登录）整套移除。产品只支持用户手动填写
 * LLM provider（baseUrl + apiKey），身份走本地 profile。
 */
export { NullAuthAdapter } from "./NullAuthAdapter";
export { ManualAuthAdapter } from "./ManualAuthAdapter";
export {
  type AuthAdapter,
  type AuthEdition,
  type AuthEvent,
  type LoginCredentials,
  type Provider,
  type ProviderModel,
  type RegisterCredentials,
  type UsageSummary,
  type User,
  NotSupportedError,
} from "./types";

import { type AuthAdapter, type AuthEdition } from "./types";
import { ManualAuthAdapter } from "./ManualAuthAdapter";
import { NullAuthAdapter } from "./NullAuthAdapter";

let _instance: AuthAdapter | null = null;

/** Singleton accessor — selects adapter based on build-time edition. */
export function getAuthAdapter(): AuthAdapter {
  if (_instance) return _instance;
  // Vite injects strings only; default to "manual" for OSS build.
  const edition: AuthEdition =
    ((import.meta as ImportMeta & { env?: Record<string, string> }).env
      ?.VITE_AUTH_EDITION as AuthEdition) ?? "manual";
  _instance = buildAdapter(edition);
  return _instance;
}

/** Construct a fresh adapter (escape hatch for tests). */
export function buildAdapter(edition: AuthEdition): AuthAdapter {
  switch (edition) {
    case "null":
      return new NullAuthAdapter();
    case "manual":
      return new ManualAuthAdapter();
    default: {
      const _exhaust: never = edition;
      throw new Error(`Unknown AuthEdition: ${String(_exhaust)}`);
    }
  }
}

/** Test-only — reset the singleton between vitest cases. */
export function _resetAuthAdapterForTests(): void {
  _instance = null;
}
