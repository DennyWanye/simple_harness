import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("permission mode production wiring", () => {
  it("defaults both permission surfaces to automatic mode", () => {
    const settings = readFileSync("src/components/SettingsPanel.tsx", "utf8");
    const center = readFileSync("src/components/CapabilityCenterPanel.tsx", "utf8");
    expect(settings).toContain("cached === null ? true");
    expect(center).toContain('cached === null || cached === "true" ? "auto"');
    expect(settings).toContain("自动模式（推荐）");
  });
});
