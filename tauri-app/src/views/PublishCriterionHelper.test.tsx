// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PublishCriterionHelper, plainPublishMention, publishCriterion } from "./PublishCriterionHelper";

afterEach(cleanup);

describe("PublishCriterionHelper", () => {
  it("填文件名就加上系统认得的那一行，不重复加", () => {
    const onChange = vi.fn();
    render(<PublishCriterionHelper criteria={"NOTES.md 有三个例子"} onChange={onChange} disabled={false} publish={{ enabled: true }} />);
    fireEvent.change(screen.getByLabelText("完成后发布的文件名"), { target: { value: " NOTES.md " } });
    fireEvent.click(screen.getByText("加入成功条件"));
    expect(onChange).toHaveBeenCalledWith("NOTES.md 有三个例子\naction:file_publish.publish:NOTES.md");
    cleanup();
    const again = vi.fn();
    render(<PublishCriterionHelper criteria={publishCriterion("NOTES.md")} onChange={again} disabled={false} publish={{ enabled: true }} />);
    fireEvent.change(screen.getByLabelText("完成后发布的文件名"), { target: { value: "NOTES.md" } });
    fireEvent.click(screen.getByText("加入成功条件"));
    expect(again).not.toHaveBeenCalled();
  });

  it("用普通中文写了发布就提醒；发布目录没授权时说明原因并禁用", () => {
    expect(plainPublishMention("NOTES.md 已发布到授权目录")).toBe(true);
    expect(plainPublishMention("NOTES.md 已发布\naction:file_publish.publish:NOTES.md")).toBe(false);
    expect(plainPublishMention("写三句话")).toBe(false);
    render(<PublishCriterionHelper criteria={"NOTES.md 已发布到授权目录"} onChange={() => {}} disabled={false} publish={{ enabled: true }} />);
    expect(screen.getByRole("status").textContent).toContain("系统只认");
    cleanup();
    render(<PublishCriterionHelper criteria={""} onChange={() => {}} disabled={false} publish={{ enabled: false, reason: "未授权发布目录" }} />);
    expect(screen.getByText(/发布目录还没授权/).textContent).toContain("未授权发布目录");
    expect((screen.getByLabelText("完成后发布的文件名") as HTMLInputElement).disabled).toBe(true);
  });
});
