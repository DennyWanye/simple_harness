import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ActionApprovalSummary } from "./ActionApprovalSummary";
import { actionHeadline } from "./actionHeadline";

describe("ActionApprovalSummary", () => {
  it("把发布写成一句人话，系统写的理由标「系统生成」", () => {
    const summary = {
      connector: "file_publish", operation: "publish", target: "README.md",
      params: { artifact_path: "README.md", content_hash: "ee58" },
      reason: "用户在确认页批准的操作：发布 README.md 到 README.md", reason_source: "system",
    };
    render(<ActionApprovalSummary summary={summary} action={{}} />);
    expect(screen.getByText("动作审批：发布 README.md")).toBeTruthy();
    expect(screen.getByText("（系统生成）")).toBeTruthy();
    expect(screen.queryByText(/模型生成/)).toBeNull();
    expect(screen.queryByText(/content_hash|\{/)).toBeNull();
  });

  it("模型写的理由仍标「模型生成，未核实」；源与目标不同时写明", () => {
    const action = { connector: "file_publish", operation: "publish", target: "reports/weekly.md",
      params: { artifact_path: "NOTES.md" }, reason: { text: "按要求发布", source: "model (untrusted)" } };
    render(<ActionApprovalSummary summary={undefined} action={action} />);
    expect(screen.getByText("动作审批：把 NOTES.md 发布为 reports/weekly.md")).toBeTruthy();
    expect(screen.getByText("（模型生成，未核实）")).toBeTruthy();
  });

  it("其他连接器写成 连接器.操作 → 目标", () => {
    expect(actionHeadline({ connector: "mail", operation: "send", target: "a@b" }, {})).toBe("mail.send → a@b");
  });
});
