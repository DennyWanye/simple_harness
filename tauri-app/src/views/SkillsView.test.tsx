// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * SkillsView 测试（T10，WB-6）。
 *
 * 覆盖：page 宿主渲染（非弹窗）、能力中心 ↔ 旧 Skill Store 互跳的
 * 视图内本地化（不经 App 层浮层开关）。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SkillsView } from "./SkillsView";

afterEach(cleanup);

describe("SkillsView（T10，WB-6）", () => {
  it("默认以 page 宿主渲染能力中心（非弹窗）", () => {
    render(<SkillsView channel={null} />);

    expect(screen.getByTestId("view-skills")).toBeTruthy();
    expect(screen.getByRole("region", { name: "能力中心" })).toBeTruthy();
    // 页面化：无模态语义。
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "关闭能力中心" }),
    ).toBeNull();
  });

  it("互跳本地化：打开旧 Skill Store → 视图内切换；返回能力中心复原", () => {
    render(<SkillsView channel={null} />);

    fireEvent.click(
      screen.getByRole("button", { name: "打开旧 Skill Store" }),
    );
    // SkillStore 同样以 page 内嵌（非 dialog、无关闭钮）。
    expect(screen.getByRole("region", { name: "技能商店" })).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("button", { name: "关闭" })).toBeNull();
    expect(screen.queryByRole("region", { name: "能力中心" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "返回能力中心" }));
    expect(screen.getByRole("region", { name: "能力中心" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "技能商店" })).toBeNull();
  });
});
