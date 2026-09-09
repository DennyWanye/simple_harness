import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("permission mode production wiring", () => {
  // MM-D4（2026-09-09）：设置面板的复选框不再由 localStorage 默认值起手
  // ——那正是「后端 auto 而界面显示未勾选」的成因。这里把「设置面板不读
  // 缓存作为初始值」锁住；能力中心仍保留缓存默认 auto（只做占位展示）。
  it("keeps the settings checkbox off any cached default", () => {
    const settings = readFileSync("src/components/SettingsPanel.tsx", "utf8");
    expect(settings).not.toContain("cached === null ? true");
    expect(settings).not.toContain('localStorage.getItem("deskpet.auto_mode")');
    // 加载中 = 无权威快照 → 不可交互，且写入携带显式目标模式。
    expect(settings).toContain("const loading = policy === null;");
    expect(settings).toContain("const checked = policy?.mode === \"auto\";");
    expect(settings).toContain("buildAutoModeSetMessage(target,");
    expect(settings).toContain("自动模式（推荐）");
  });

  it("defaults the capability center placeholder to automatic mode", () => {
    const center = readFileSync("src/components/CapabilityCenterPanel.tsx", "utf8");
    expect(center).toContain('cached === null || cached === "true" ? "auto"');
  });
});
