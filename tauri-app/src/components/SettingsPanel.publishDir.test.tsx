// @vitest-environment jsdom
// NEXT-TG-1.0 §9：设置页「任务发布目录」——读当前授权、保存前确认、保存后显示生效状态、可撤销。

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PublishDirSection, buildPublishDirMessage } from "./SettingsPanel";
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

function mount() {
  const channel = new FakeControlChannel();
  render(<PublishDirSection getChannel={() => channel as unknown as ControlChannel} />);
  return channel;
}

describe("PublishDirSection", () => {
  afterEach(cleanup);

  it("读当前授权并显示；未授权时说明带发布的任务无法发布", async () => {
    const channel = mount();
    await waitFor(() => expect(channel.sent).toHaveLength(1));
    expect(channel.sent[0]).toEqual(buildPublishDirMessage(String(channel.sent[0].request_id)));
    channel.reply("orchestration_publish_dir_get", { ok: true, data: { configured: "", publish: { enabled: false, reason: "未授权发布目录" }, active_missions: 0 } });
    expect(screen.getByTestId("publish-dir-status").textContent).toContain("未授权");
    expect(screen.queryByTestId("publish-dir-revoke")).toBeNull();
  });

  it("保存前确认，确认后发送设置；后台拒绝时显示原因", async () => {
    const channel = mount();
    await waitFor(() => expect(channel.sent).toHaveLength(1));
    channel.reply("orchestration_publish_dir_get", { ok: true, data: { configured: "", publish: {}, active_missions: 2 } });
    fireEvent.change(screen.getByLabelText("发布目录路径"), { target: { value: "/Users/me/pub" } });
    fireEvent.click(screen.getByTestId("publish-dir-save"));
    expect(await screen.findByText(/正在进行的 2 个任务会暂停几秒后自动接着做/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "授权" }));
    await waitFor(() => expect(channel.sent).toHaveLength(2));
    expect(channel.sent[1].payload).toEqual({ path: "/Users/me/pub" });
    channel.reply("orchestration_publish_dir_set", { ok: false, error_code: "no_hardlinks", error: "这个磁盘不支持硬链接" });
    expect(screen.getByRole("alert").textContent).toContain("不支持硬链接");
  });

  it("授权生效后可以撤销", async () => {
    const channel = mount();
    await waitFor(() => expect(channel.sent).toHaveLength(1));
    channel.reply("orchestration_publish_dir_get", { ok: true, data: { configured: "/p", publish: { enabled: true, root: "/p" }, active_missions: 0 } });
    expect(screen.getByTestId("publish-dir-status").textContent).toContain("已授权：/p");
    fireEvent.click(screen.getByTestId("publish-dir-revoke"));
    fireEvent.click(await screen.findByRole("button", { name: "撤销" }));
    await waitFor(() => expect(channel.sent).toHaveLength(2));
    expect(channel.sent[1].payload).toEqual({ path: "" });
    channel.reply("orchestration_publish_dir_set", { ok: true, data: { configured: "", publish: { enabled: false, reason: "未授权发布目录" }, active_missions: 0 } });
    expect(screen.getByText("已撤销授权")).toBeTruthy();
  });
});
