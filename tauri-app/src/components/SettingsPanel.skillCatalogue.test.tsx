// @vitest-environment jsdom
// NEXT-TG-1.0 §11：设置页「任务技能目录」——读统一目录、各档位状态、从本地安装、暂停/退役（退役先确认）、被拒说明原因。

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SkillCatalogueSection, poolLabel, poolStatus } from "./SettingsPanel";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ControlChannel, ConnectionState } from "../ws/ControlChannel";

class FakeControlChannel {
  state: ConnectionState = "connected";
  sent: ControlMessage[] = [];
  private listeners = new Set<(message: IncomingMessage) => void>();
  send(message: ControlMessage) { this.sent.push(message); return true; }
  onMessage(listener: (message: IncomingMessage) => void) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  onStateChange() { return () => undefined; }
  reply(type: string, payload: Record<string, unknown>) {
    const request = [...this.sent].reverse().find((m) => m.type === type);
    const id = request?.request_id;
    act(() => { for (const l of this.listeners) l({ type: `${type}_response`, payload: { request_id: id, ...payload } } as unknown as IncomingMessage); });
  }
}

const OWNER = "deepseek-native-256k-v1";
const MEMBER = "deepseek-native-512k-thinking-v1";

function catalogue(state: string, memberUsable = true) {
  return {
    owner: OWNER, members: [MEMBER],
    skills: [{
      skill_ref: { kind: "skill", id: "notes", revision: 1, content_hash: "a".repeat(64) }, skill_id: "notes", version: 1,
      name: "notes", description: "记笔记的技能。", state,
      pools: {
        [OWNER]: { state, usable: state === "ADMITTED", reasons: [] },
        [MEMBER]: { state, usable: state === "ADMITTED" && memberUsable, reasons: memberUsable ? [] : ["DEPENDENCY_UNRESOLVED"] },
      },
    }],
  };
}

function mount() {
  const channel = new FakeControlChannel();
  render(<SkillCatalogueSection getChannel={() => channel as unknown as ControlChannel} />);
  return channel;
}

describe("SkillCatalogueSection", () => {
  afterEach(cleanup);

  it("档位名称与不可用原因用大白话", () => {
    expect(poolLabel(OWNER)).toBe("256K");
    expect(poolLabel(MEMBER)).toBe("512K 思考");
    expect(poolStatus({ state: "ADMITTED", usable: false, reasons: ["DEPENDENCY_UNRESOLVED"] })).toContain("缺少所需工具");
    expect(poolStatus({ state: "SUSPENDED", usable: false, reasons: ["STATE_SUSPENDED"] })).toContain("已暂停");
  });

  it("读统一目录并逐档位显示；从本地路径安装后刷新", async () => {
    const channel = mount();
    await waitFor(() => expect(channel.sent).toHaveLength(1));
    expect(channel.sent[0]).toMatchObject({ type: "orchestration_skill_catalogue", payload: {} });
    channel.reply("orchestration_skill_catalogue", { ok: true, data: { owner: OWNER, members: [MEMBER], skills: [] } });
    expect(screen.getByTestId("skill-catalogue-status").textContent).toContain("还没有技能");
    fireEvent.change(screen.getByLabelText("技能包路径"), { target: { value: "/Users/me/notes.zip" } });
    fireEvent.click(screen.getByTestId("skill-install"));
    await waitFor(() => expect(channel.sent).toHaveLength(2));
    expect(channel.sent[1]).toMatchObject({ type: "orchestration_skill_install_file", payload: { path: "/Users/me/notes.zip" } });
    channel.reply("orchestration_skill_install_file", { ok: true, data: { response: { error: null }, catalogue: catalogue("QUARANTINED") } });
    expect(screen.getAllByTestId("skill-row")).toHaveLength(1);
    expect(screen.getByText(/第 1 版 · 待评估/)).toBeTruthy();
    expect(screen.getByText(/各档位同步为同一版本/)).toBeTruthy();
  });

  it("可用的技能可暂停；缺工具的档位写明原因；被拒时显示原因", async () => {
    const channel = mount();
    await waitFor(() => expect(channel.sent).toHaveLength(1));
    channel.reply("orchestration_skill_catalogue", { ok: true, data: catalogue("ADMITTED", false) });
    expect(screen.getByText(/512K 思考：不可用（这个档位缺少所需工具）/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "暂停" }));
    await waitFor(() => expect(channel.sent).toHaveLength(2));
    expect(channel.sent[1]).toMatchObject({ type: "orchestration_skill_lifecycle", payload: { action: "SUSPEND" } });
    channel.reply("orchestration_skill_lifecycle", { ok: true, data: { response: { error: { code: "STATE_COMBINATION_INVALID" } }, catalogue: catalogue("ADMITTED", false) } });
    expect(screen.getByRole("alert").textContent).toContain("STATE_COMBINATION_INVALID");
  });

  it("退役先确认，确认后才发送", async () => {
    const channel = mount();
    await waitFor(() => expect(channel.sent).toHaveLength(1));
    channel.reply("orchestration_skill_catalogue", { ok: true, data: catalogue("SUSPENDED") });
    expect(screen.getByRole("button", { name: "恢复" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "退役" }));
    expect(await screen.findByText(/退役后所有档位都不能再使用这个版本/)).toBeTruthy();
    expect(channel.sent).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "确认退役" }));
    await waitFor(() => expect(channel.sent).toHaveLength(2));
    expect(channel.sent[1]).toMatchObject({ type: "orchestration_skill_lifecycle", payload: { action: "RETIRE" } });
  });
});
