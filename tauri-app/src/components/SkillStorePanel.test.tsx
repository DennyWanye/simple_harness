// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ControlMessage, IncomingMessage } from "../types/messages";
import { SkillStorePanel } from "./SkillStorePanel";

class FakeChannel {
  readonly sent: ControlMessage[] = [];
  private listener: ((message: IncomingMessage) => void) | null = null;

  send = (message: ControlMessage) => {
    this.sent.push(message);
    return true;
  };

  onMessage = (listener: (message: IncomingMessage) => void) => {
    this.listener = listener;
    return () => {
      if (this.listener === listener) this.listener = null;
    };
  };

  emit(message: unknown) {
    this.listener?.(message as IncomingMessage);
  }
}

afterEach(cleanup);

describe("SkillStorePanel managed global install", () => {
  it("renders the exact batch and sends one digest-bound decision", () => {
    const channel = new FakeChannel();
    render(<SkillStorePanel open variant="page" channel={channel} />);

    act(() => channel.emit({
      type: "skill_install_pending",
      payload: {
        ok: true,
        intent_id: "intent-1",
        digest: "digest-1",
        decision_nonce: "nonce-1",
        decision_version: 2,
        repository_url: "https://github.com/o/r",
        resolved_commit: "a".repeat(40),
        expires_at: "2026-08-28T12:00:00Z",
        project: {
          project_id: "project-a",
          project_name: "Project A",
          project_revision: 3,
          project_identity: "identity-a",
        },
        members: [
          { name: "plan-bs", description: "Brainstorm", permission_categories: ["read_file"] },
          { name: "plan-test", description: "Execute", permission_categories: ["skill_install"] },
        ],
      },
    }));

    expect(screen.getByText("全局安装 2 个 Skill")).toBeTruthy();
    expect(screen.getByText(/安装后可在所有会话中使用/)).toBeTruthy();
    expect(screen.getByText("plan-bs")).toBeTruthy();
    expect(screen.getByText("plan-test")).toBeTruthy();
    expect(screen.getByText(/commit: a{40}/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "我明白风险，继续安装" }));

    expect(channel.sent.at(-1)).toEqual({
      type: "skill_install_confirm",
      payload: {
        intent_id: "intent-1",
        digest: "digest-1",
        decision_nonce: "nonce-1",
        decision_version: 2,
        decision: "approve",
      },
    });
  });

  it("does not announce success before runtime verification", () => {
    const channel = new FakeChannel();
    render(<SkillStorePanel open variant="page" channel={channel} />);
    act(() => channel.emit({
      type: "skill_install_pending",
      payload: {
        ok: true,
        intent_id: "intent-1",
        digest: "digest-1",
        decision_nonce: "nonce-1",
        decision_version: 1,
        repository_url: "https://github.com/o/r",
        resolved_commit: "a".repeat(40),
        project: { project_id: "project-a", project_name: "Project A", project_revision: 3 },
        members: [{ name: "plan-test", permission_categories: [] }],
      },
    }));
    act(() => channel.emit({
      type: "skill_install_confirm_response",
      payload: { ok: true, intent_id: "intent-1", status: "published_pending_runtime_verification", runtime_verified: false },
    }));

    expect(screen.queryByText(/安装成功/)).toBeNull();
    expect(screen.getByText(/安装尚未完成/)).toBeTruthy();
    expect(channel.sent.at(-1)).toEqual({
      type: "skill_install_status",
      payload: { intent_id: "intent-1" },
    });

    act(() => channel.emit({
      type: "skill_install_status_response",
      payload: { ok: true, status: "succeeded", runtime_verified: true },
    }));
    expect(screen.getByText(/安装成功：所有会话的新 Run 均可使用这些 Skill/)).toBeTruthy();
  });

  it("rejects an incomplete stage projection without a confirmation card", () => {
    const channel = new FakeChannel();
    render(<SkillStorePanel open variant="page" channel={channel} />);
    act(() => channel.emit({
      type: "skill_install_pending",
      payload: { ok: true, intent_id: "intent-1", members: [] },
    }));
    expect(screen.getByRole("alert").textContent).toContain("skill_install_contract_invalid");
    expect(screen.queryByText(/确认安装/)).toBeNull();
  });
});
