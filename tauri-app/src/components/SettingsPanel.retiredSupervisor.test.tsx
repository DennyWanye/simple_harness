// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SettingsPanel } from "./SettingsPanel";

describe("SettingsPanel retired supervisor UI", () => {
  it("does not expose the removed supervisor controls", () => {
    const html = renderToStaticMarkup(
      <SettingsPanel
        open
        onClose={() => undefined}
        getChannel={() => null}
        lastMessage={null}
        secret=""
      />,
    );

    expect(html).not.toContain("supervisor");
    expect(html).not.toContain("AutoResumeOrchestrator");
    expect(html).not.toContain("auto-resume-toggle");
  });
});

describe("SettingsPanel page variant（T11，WB-8）", () => {
  it("逐设置项保留：除「桌宠形象」外原区块无缺失，且无浮层 dialog 结构", () => {
    const html = renderToStaticMarkup(
      <SettingsPanel
        open
        variant="page"
        onClose={() => undefined}
        getChannel={() => null}
        lastMessage={null}
        secret=""
        autostart={{ ready: true, enabled: false, toggle: () => undefined }}
      />,
    );

    // 原设置项逐项保留（WB-8）。
    for (const section of [
      "LLM Providers",
      "模型状态",
      "权限",
      "数据目录",
      "关于与更新",
      "危险区",
    ]) {
      expect(html).toContain(section);
    }
    // T11：自启开关移入设置页。
    expect(html).toContain("autostart-toggle");
    expect(html).toContain("开机自动启动");
    // 桌宠形象区块删除（acceptance only-add 例外清单）。
    expect(html).not.toContain("桌宠形象");
    // page variant 去 backdrop/fixed 模态结构与关闭按钮。
    expect(html).not.toContain("aria-modal");
    expect(html).not.toContain("关闭设置");
  });

  it("overlay variant（默认）保持浮层结构与关闭按钮", () => {
    const html = renderToStaticMarkup(
      <SettingsPanel
        open
        onClose={() => undefined}
        getChannel={() => null}
        lastMessage={null}
        secret=""
      />,
    );
    expect(html).toContain("aria-modal");
    expect(html).toContain("关闭设置");
    // 自启开关未传 → 「通用」节不渲染。
    expect(html).not.toContain("autostart-toggle");
  });
});
