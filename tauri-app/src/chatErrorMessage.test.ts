import { describe, expect, it } from "vitest";

import { chatErrorMessage } from "./chatErrorMessage";

describe("chatErrorMessage", () => {
  it("turns a closed SDK ingress into an actionable provider hint", () => {
    expect(
      chatErrorMessage({
        error: "SDK Runtime ingress is closed",
        detail: "RuntimeError",
      }),
    ).toContain("设置 → LLM Providers");
  });

  it("preserves unknown provider diagnostics", () => {
    expect(chatErrorMessage({ error: "HTTP 401", detail: "Unauthorized" }))
      .toBe("HTTP 401 — Unauthorized");
  });
});
