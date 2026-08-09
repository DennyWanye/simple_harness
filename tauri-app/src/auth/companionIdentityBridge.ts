import {
  getWindowControlCredential,
  type WindowControlCredential,
} from "./windowControlCredential";

export interface IdentityChallenge {
  connectionId: string;
  controlEpoch: string;
  challenge: string;
  requestSeq: string;
  bindingEpoch: string;
}

export interface CompanionIdentityBind {
  kind: "companion_profile_bind";
  auth_snapshot: {
    /** 2026-08-09：relay 托管登录移除后只剩 local 一种身份来源。
     *  字段保留是为了让"快照形状不对"仍显式失败（后端 validate_auth_snapshot
     *  对未知 mode 抛 auth_snapshot_mode_invalid），而不是静默按 local 处理。 */
    mode: "local";
    user_id: null;
  };
  credential: WindowControlCredential;
}

const TRANSIENT_IDENTITY_BIND_ERRORS = new Set([
  "auth_snapshot_mismatch",
]);

export function isTransientIdentityBindError(code: unknown): boolean {
  return (
    typeof code === "string" &&
    TRANSIENT_IDENTITY_BIND_ERRORS.has(code)
  );
}

export function identityBindRetryDelayMs(attempt: number): number {
  const boundedAttempt = Math.max(0, Math.min(Math.trunc(attempt), 5));
  return Math.min(1_000 * 2 ** boundedAttempt, 30_000);
}

/**
 * 构造 companion_profile_bind 命令。
 *
 * 2026-08-09 修复（真机冷启动实测）：原实现按「adapter.currentUser() 是否为空」
 * 二选一 —— 非空就声明 `mode: "relay"`。而 `ManualAuthAdapter.currentUser()`
 * **恒返回一个本地占位用户** `{id:"local"}`，于是手动 provider 模式下每次绑定都
 * 声明 relay；后端权威已是 local ⇒ `auth_snapshot_mismatch` 永久拒绝，输入框
 * 一直卡在「正在恢复身份…」，每 30s 重试一次且无恢复入口。
 *
 * relay 已整体移除，身份只可能来自本地 profile，这里直接恒定声明 local。
 */
export async function buildIdentityBind(
  challenge: IdentityChallenge,
): Promise<CompanionIdentityBind> {
  const authSnapshot = { mode: "local" as const, user_id: null };
  const body = { auth_snapshot: authSnapshot };
  const credential = await getWindowControlCredential({
    ...challenge,
    commandKind: "companion_profile_bind",
    body,
    requestedScope: "identity_bind",
  });
  return { kind: "companion_profile_bind", auth_snapshot: authSnapshot, credential };
}
