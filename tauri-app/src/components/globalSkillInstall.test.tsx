import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("global Skill install production wiring", () => {
  it("presents installation as global to all Sessions", () => {
    const source = readFileSync("src/components/SkillStorePanel.tsx", "utf8");
    expect(source).toContain("全局");
    expect(source).toContain("所有会话");
    expect(source).not.toContain("安装到这个项目");
  });
});
