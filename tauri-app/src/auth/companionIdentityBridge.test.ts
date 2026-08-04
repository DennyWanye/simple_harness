import { webcrypto } from "node:crypto";
import { beforeEach, describe, expect, it, vi } from "vitest";

Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });

const invokeMock = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

describe("companion identity bridge", () => {
  beforeEach(() => {
    invokeMock.mockReset();
    invokeMock.mockResolvedValue({
      backendProcessInstanceId: "process",
      connectionId: "connection",
      controlEpoch: "1",
      windowLabel: "main",
      scope: "identity_bind",
      challengeHash: "a".repeat(64),
      requestSeq: "1",
      commandKind: "companion_profile_bind",
      canonicalRequestHash: "b".repeat(64),
      nonce: "nonce",
      issuedAt: "1",
      expiresAt: "2",
      signatureHex: "c".repeat(128),
    });
  });

  it("binds relay AuthAdapter.User.id without renderer-supplied window label", async () => {
    const { buildIdentityBind } = await import("./companionIdentityBridge");
    const result = await buildIdentityBind(
      {
        connectionId: "connection",
        controlEpoch: "1",
        challenge: "challenge",
        requestSeq: "1",
        bindingEpoch: "1",
      },
      { id: "relay-user", email: "a@example.com", username: "A" },
    );
    expect(result.auth_snapshot).toEqual({ mode: "relay", user_id: "relay-user" });
    expect(invokeMock).toHaveBeenCalledWith(
      "get_window_control_credential",
      expect.objectContaining({ requestedScope: "identity_bind" }),
    );
    expect(invokeMock.mock.calls[0][1]).not.toHaveProperty("windowLabel");
  });

  it("retries only transient trusted-auth convergence failures", async () => {
    const { isTransientIdentityBindError } = await import(
      "./companionIdentityBridge"
    );
    expect(isTransientIdentityBindError("auth_snapshot_mismatch")).toBe(true);
    expect(
      isTransientIdentityBindError("trusted_relay_identity_unavailable"),
    ).toBe(true);
    expect(isTransientIdentityBindError("invalid_signature")).toBe(false);
    expect(isTransientIdentityBindError(undefined)).toBe(false);
  });

  it("backs trusted identity retries off to the relay cooldown scale", async () => {
    const { identityBindRetryDelayMs } = await import(
      "./companionIdentityBridge"
    );
    expect([0, 1, 2, 3, 4, 5, 20].map(identityBindRetryDelayMs)).toEqual([
      1_000,
      2_000,
      4_000,
      8_000,
      16_000,
      30_000,
      30_000,
    ]);
  });
});
