import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { webcrypto } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import {
  bytesToHex,
  canonicalRequestHash,
  encodeControlBody,
  parseCanonicalJson,
  parseCanonicalU64,
} from "./controlCommandCanonical";
import { getWindowControlCredential } from "./windowControlCredential";

Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });

const vectors = JSON.parse(
  readFileSync(
    resolve(process.cwd(), "..", "tests", "fixtures", "control-command-canonical-v1.json"),
    "utf8",
  ),
);

describe("control-command-canonical-v1 shared vectors", () => {
  it("matches TLV and request hashes", async () => {
    for (const item of vectors.valid) {
      const body = parseCanonicalJson(item.raw_body_json);
      expect(bytesToHex(encodeControlBody(body))).toBe(item.tlv_hex);
      expect(
        await canonicalRequestHash(
          item.command_kind,
          item.request_seq,
          item.binding_epoch,
          body,
        ),
      ).toBe(item.request_hash);
      if (item.equivalent_raw_body_json) {
        expect(
          bytesToHex(encodeControlBody(parseCanonicalJson(item.equivalent_raw_body_json))),
        ).toBe(item.tlv_hex);
      }
    }
  });

  it("rejects every raw JSON and u64 reject vector", () => {
    for (const item of vectors.reject_raw_json) {
      expect(() => parseCanonicalJson(item.raw)).toThrow();
    }
    for (const item of vectors.reject_u64) {
      expect(() => parseCanonicalU64(item, "fixture")).toThrow();
    }
    for (const encoded of vectors.reject_utf8_hex) {
      expect(() => new TextDecoder("utf-8", { fatal: true }).decode(
        Uint8Array.from(encoded.match(/../g), (pair: string) => Number.parseInt(pair, 16)),
      )).toThrow();
    }
  });

  it("passes only canonical facts to the injected-window invoke command", async () => {
    const invokeFn = vi.fn(async (_command: string, args: Record<string, string>) => ({
      ...args,
      backendProcessInstanceId: "process",
      windowLabel: "main",
      scope: args.requestedScope,
      challengeHash: "a".repeat(64),
      canonicalRequestHash: args.requestHash,
      nonce: "nonce",
      issuedAt: "1",
      expiresAt: "2",
      signatureHex: "b".repeat(128),
    }));
    const credential = await getWindowControlCredential(
      {
        connectionId: "connection",
        controlEpoch: "1",
        challenge: "challenge",
        requestSeq: "1",
        bindingEpoch: "1",
        commandKind: "companion_profile_bind",
        body: { auth_snapshot: { mode: "local", user_id: null } },
        requestedScope: "identity_bind",
      },
      invokeFn as never,
    );
    expect(invokeFn).toHaveBeenCalledWith(
      "get_window_control_credential",
      expect.not.objectContaining({ windowLabel: expect.anything() }),
    );
    expect(credential.scope).toBe("identity_bind");
  });
});
