import { describe, expect, it } from "vitest";
import { PRIMARY_TURN_TEXT_MAX_BYTES, turnTextByteLength, turnTextRejection } from "./turnText";

describe("foreground turn text bound", () => {
  it("measures UTF-8 bytes, not UTF-16 code units", () => {
    expect(turnTextByteLength("很")).toBe(3);
    expect(turnTextByteLength("a")).toBe(1);
    // The incident's own message: 7 199 characters, 18 393 bytes.
    expect(turnTextByteLength("很".repeat(6_131))).toBe(18_393);
  });
  it("accepts exactly the bound and rejects one byte past it", () => {
    const filler = "很".repeat(Math.floor(PRIMARY_TURN_TEXT_MAX_BYTES / 3));
    const exact = filler + "a".repeat(PRIMARY_TURN_TEXT_MAX_BYTES - turnTextByteLength(filler));
    expect(turnTextByteLength(exact)).toBe(PRIMARY_TURN_TEXT_MAX_BYTES);
    expect(turnTextRejection(exact)).toBe("");
    expect(turnTextRejection(exact + "a")).toContain(String(PRIMARY_TURN_TEXT_MAX_BYTES));
    expect(turnTextRejection(exact + "a")).toContain(String(PRIMARY_TURN_TEXT_MAX_BYTES + 1));
  });
  it("says the message was not sent and the draft was kept", () => {
    const message = turnTextRejection("很".repeat(PRIMARY_TURN_TEXT_MAX_BYTES));
    expect(message).toContain("未发送");
    expect(message).toContain("草稿已保留");
  });
});
