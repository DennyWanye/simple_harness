import type { User } from "./types";
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
    mode: "relay" | "local";
    user_id: string | null;
  };
  credential: WindowControlCredential;
}

const TRANSIENT_IDENTITY_BIND_ERRORS = new Set([
  "auth_snapshot_mismatch",
  "trusted_relay_access_token_missing",
  "trusted_relay_identity_unavailable",
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

export async function buildIdentityBind(
  challenge: IdentityChallenge,
  user: User | null,
): Promise<CompanionIdentityBind> {
  const authSnapshot = user
    ? { mode: "relay" as const, user_id: user.id }
    : { mode: "local" as const, user_id: null };
  const body = { auth_snapshot: authSnapshot };
  const credential = await getWindowControlCredential({
    ...challenge,
    commandKind: "companion_profile_bind",
    body,
    requestedScope: "identity_bind",
  });
  return { kind: "companion_profile_bind", auth_snapshot: authSnapshot, credential };
}
