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

  // 2026-08-09：本用例原先断言 `{ mode: "relay", user_id: "relay-user" }`——
  // 它把 relay 时代的形状钉死了，于是阶段2/3 删掉 relay 之后它仍然绿着，
  // 掩护了真机上「身份永久绑不上」的缺陷（ManualAuthAdapter 恒返回本地占位
  // 用户 ⇒ 旧逻辑声明 mode=relay ⇒ 后端 local 权威判 auth_snapshot_mismatch）。
  // 现在反过来钉住：身份快照**恒为 local**，且不接受任何用户参数。
  it("身份快照恒为 local，不带 user_id", async () => {
    const { buildIdentityBind } = await import("./companionIdentityBridge");
    const result = await buildIdentityBind({
      connectionId: "connection",
      controlEpoch: "1",
      challenge: "challenge",
      requestSeq: "1",
      bindingEpoch: "1",
    });
    expect(result.auth_snapshot).toEqual({ mode: "local", user_id: null });
    expect(invokeMock).toHaveBeenCalledWith(
      "get_window_control_credential",
      expect.objectContaining({ requestedScope: "identity_bind" }),
    );
    expect(invokeMock.mock.calls[0][1]).not.toHaveProperty("windowLabel");
  });

  // 防止 relay 分支被悄悄接回：buildIdentityBind 只接受 challenge 一个参数，
  // 没有任何“按用户身份分流”的入口。
  it("buildIdentityBind 只有 challenge 一个入参（无身份来源分流口）", async () => {
    const { buildIdentityBind } = await import("./companionIdentityBridge");
    expect(buildIdentityBind.length).toBe(1);
  });

  it("retries only transient trusted-auth convergence failures", async () => {
    const { isTransientIdentityBindError } = await import(
      "./companionIdentityBridge"
    );
    expect(isTransientIdentityBindError("auth_snapshot_mismatch")).toBe(true);
    // relay 专属的两个瞬时码随 relay 一并移除，后端不再产出——不应再被当作可重试。
    expect(
      isTransientIdentityBindError("trusted_relay_identity_unavailable"),
    ).toBe(false);
    expect(
      isTransientIdentityBindError("trusted_relay_access_token_missing"),
    ).toBe(false);
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
