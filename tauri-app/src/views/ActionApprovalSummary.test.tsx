import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ActionApprovalSummary } from "./ActionApprovalSummary";
import { actionHeadline } from "./actionHeadline";

describe("ActionApprovalSummary", () => {
  afterEach(cleanup);
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

  it("系统按原内容重交时，卡上写明上次为什么没生效", () => {
    const summary = { connector: "file_publish", operation: "publish", target: "README.md",
      params: { artifact_path: "README.md" }, reason: "系统准备的申请", reason_source: "system",
      previous_attempt: { outcome: "service_refused", reason: "conflict: reports/README.v1.md already exists", attempt: 1 } };
    render(<ActionApprovalSummary summary={summary} action={{}} />);
    expect(screen.getByTestId("previous-attempt").textContent).toBe(
      "上次没有生效：发布服务拒绝了（conflict: reports/README.v1.md already exists），这是重新提交的申请。");
  });

  it("人裁定没生效后重交：只写结局，不把内部登记码给人看（2026-10-03 真机）", () => {
    const summary = { connector: "file_publish", operation: "publish", target: "minutes.md",
      params: { artifact_path: "minutes.md" }, reason: "系统准备的申请", reason_source: "system",
      previous_attempt: { outcome: "human_ruled_not_applied", reason: "human_ruled_failed", attempt: 1 } };
    render(<ActionApprovalSummary summary={summary} action={{}} />);
    expect(screen.getByTestId("previous-attempt").textContent).toBe(
      "上次没有生效：你裁定它没有生效，这是重新提交的申请。");
  });

  it("其他连接器写成 连接器.操作 → 目标", () => {
    expect(actionHeadline({ connector: "mail", operation: "send", target: "a@b" }, {})).toBe("mail.send → a@b");
  });
});
