import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("ordinary Session workspace production wiring", () => {
  it("offers an automatic workspace without requiring a chosen Project", () => {
    const picker = readFileSync("src/components/ProjectPickerDialog.tsx", "utf8");
    expect(picker).toContain("project-picker-default");
    expect(picker).toContain("使用默认目录");
    expect(picker).toContain('onDefaultSelected ? "selected_folder" : "git_root"');
  });

  it("renders the automatic workspace identity returned by the Host", () => {
    const list = readFileSync("src/components/SessionList.tsx", "utf8");
    expect(list).toContain('workspace_kind === "automatic"');
    expect(list).toContain("默认目录");
  });
});
