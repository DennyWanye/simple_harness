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
    /** Local profile is the sole identity source. The explicit mode keeps
     * malformed snapshots fail-closed at the backend validation boundary. */
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
 * Hosted account authentication no longer exists. Bind the signed local
 * profile directly so frontend identity cannot diverge from backend authority.
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
