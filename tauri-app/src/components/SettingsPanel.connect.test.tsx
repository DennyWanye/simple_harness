// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 设置页读取类区块：挂载时没连上就显示读取中，连上后自动发一次；没有通道直接说明。
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  GlobalBudgetSection,
  PublishDirSection,
  SkillCatalogueSection,
  StorageUsageSection,
} from "./SettingsPanel";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ControlChannel, ConnectionState } from "../ws/ControlChannel";

afterEach(cleanup);

// 假通道：没连上时 send 返回 false（同真实通道）；connect() 推"connected"；emit() 推一条消息。
class WaitingChannel {
  state: ConnectionState;
  sent: ControlMessage[] = [];
  private states = new Set<(state: ConnectionState) => void>();
  private listeners = new Set<(message: IncomingMessage) => void>();
  constructor(state: ConnectionState = "connecting") {
    this.state = state;
  }
  send(message: ControlMessage) {
    if (this.state !== "connected") return false;
    this.sent.push(message);
    return true;
  }
  onMessage(fn: (message: IncomingMessage) => void) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  emit(message: unknown) {
    act(() => this.listeners.forEach((fn) => fn(message as IncomingMessage)));
  }
  // 按请求类型回一条回执，回执带上最近一次同类请求的请求号。
  reply(type: string, payload: Record<string, unknown>) {
    const request = [...this.sent].reverse().find((m) => m.type === type);
    const id = request?.request_id;
    this.emit({
      type: `${type}_response`,
      request_id: id,
      payload: { request_id: id, ...payload },
    });
  }
  onStateChange(fn: (state: ConnectionState) => void) {
    this.states.add(fn);
    return () => this.states.delete(fn);
  }
  connect() {
    act(() => {
      this.state = "connected";
      this.states.forEach((fn) => fn("connected"));
    });
  }
}

describe("设置页读取类区块的连接时机", () => {
  it("数据占用：没连上时显示读取中，连上后发一次测量请求", () => {
    const channel = new WaitingChannel();
    render(<StorageUsageSection getChannel={() => channel as unknown as ControlChannel} />);
    expect(screen.getByTestId("storage-usage").textContent).toContain("读取中");
    expect(screen.queryByText(/连接不可用/)).toBeNull();
    expect(channel.sent).toHaveLength(0);
    channel.connect();
    expect(channel.sent.map((m) => m.type)).toEqual(["orchestration_storage_get"]);
  });

  it("全局预算：没连上时显示读取中，连上后发一次状态请求；没有通道直接说明", () => {
    const channel = new WaitingChannel();
    render(<GlobalBudgetSection getChannel={() => channel as unknown as ControlChannel} />);
    expect(screen.getByTestId("global-budget-status").textContent).toBe("读取中…");
    channel.connect();
    expect(channel.sent.map((m) => m.type)).toEqual(["orchestration_status"]);
    cleanup();

    render(<GlobalBudgetSection getChannel={() => null} />);
    expect(screen.getByTestId("global-budget-status").textContent).toBe("连接不可用");
  });

  it("通道换成新对象并连上后，两个读取区块各自动读一次", () => {
    const dead = new WaitingChannel();
    const live = new WaitingChannel();
    const storage = render(
      <StorageUsageSection getChannel={() => dead as unknown as ControlChannel} />,
    );
    storage.rerender(<StorageUsageSection getChannel={() => live as unknown as ControlChannel} />);
    live.connect();
    expect(live.sent.map((m) => m.type)).toEqual(["orchestration_storage_get"]);
    cleanup();

    const deadBudget = new WaitingChannel();
    const liveBudget = new WaitingChannel();
    const budget = render(
      <GlobalBudgetSection getChannel={() => deadBudget as unknown as ControlChannel} />,
    );
    budget.rerender(
      <GlobalBudgetSection getChannel={() => liveBudget as unknown as ControlChannel} />,
    );
    liveBudget.connect();
    expect(liveBudget.sent.map((m) => m.type)).toEqual(["orchestration_status"]);
    expect(deadBudget.sent).toHaveLength(0);
  });
});

// 复核问题 4：先挂在已连接通道 A，再换成未连接通道 B，B 连上后各区块要自动读，回执要显示到界面。
describe("通道换成新对象：新通道连上后读取并显示回执", () => {
  const as = (channel: WaitingChannel) => () => channel as unknown as ControlChannel;

  it("发布目录：新通道连上后自动读一次，回执里的目录显示出来，不出现连接不可用", () => {
    const first = new WaitingChannel("connected");
    const second = new WaitingChannel();
    const view = render(<PublishDirSection getChannel={as(first)} />);
    expect(first.sent.map((m) => m.type)).toEqual(["orchestration_publish_dir_get"]);

    view.rerender(<PublishDirSection getChannel={as(second)} />);
    expect(screen.queryByText(/连接不可用/)).toBeNull();
    second.connect();
    expect(second.sent.map((m) => m.type)).toEqual(["orchestration_publish_dir_get"]);

    second.reply("orchestration_publish_dir_get", {
      ok: true,
      data: {
        configured: "/Users/me/发布",
        publish: { enabled: true, root: "/Users/me/发布" },
        active_missions: 0,
      },
    });
    expect(screen.getByTestId("publish-dir-status").textContent).toBe("已授权：/Users/me/发布");
    expect((screen.getByLabelText("发布目录路径") as HTMLInputElement).value).toBe(
      "/Users/me/发布",
    );
    expect(screen.queryByText(/连接不可用/)).toBeNull();
  });

  it("技能目录：新通道连上后自动读一次，回执里的技能显示出来，不出现连接不可用", () => {
    const first = new WaitingChannel("connected");
    const second = new WaitingChannel();
    const view = render(<SkillCatalogueSection getChannel={as(first)} />);
    expect(first.sent.map((m) => m.type)).toEqual(["orchestration_skill_catalogue"]);

    view.rerender(<SkillCatalogueSection getChannel={as(second)} />);
    expect(screen.queryByText(/连接不可用/)).toBeNull();
    second.connect();
    expect(second.sent.map((m) => m.type)).toEqual(["orchestration_skill_catalogue"]);

    second.reply("orchestration_skill_catalogue", {
      ok: true,
      data: {
        owner: null,
        members: [],
        skills: [
          {
            skill_ref: { kind: "skill", id: "notes", revision: 1 },
            skill_id: "notes",
            version: 1,
            name: "记笔记",
            description: "记笔记的技能。",
            state: "ADMITTED",
            pools: {},
          },
        ],
      },
    });
    expect(screen.getByTestId("skill-catalogue-status").textContent).toContain("共 1 个技能版本");
    expect(screen.getByTestId("skill-row").textContent).toContain("记笔记");
    expect(screen.queryByText(/连接不可用/)).toBeNull();
  });

  it("数据占用：新通道连上后收到回执，界面离开读取中并显示占用", () => {
    const first = new WaitingChannel("connected");
    const second = new WaitingChannel();
    const view = render(<StorageUsageSection getChannel={as(first)} />);
    view.rerender(<StorageUsageSection getChannel={as(second)} />);
    second.connect();

    second.reply("orchestration_storage_get", {
      ok: true,
      data: {
        bytes: 3.2 * 1024 ** 3,
        measured_at_ms: 1,
        warn_bytes: 5 * 1024 ** 3,
        over_warn: false,
        measured: true,
        breakdown: [],
      },
    });
    const text = screen.getByTestId("storage-usage").textContent ?? "";
    expect(text).not.toContain("读取中…");
    expect(text).toContain("任务与会话数据：3.2 GB");
  });

  it("全局预算：新通道连上后收到回执，界面离开读取中并显示额度", () => {
    const first = new WaitingChannel("connected");
    const second = new WaitingChannel();
    const view = render(<GlobalBudgetSection getChannel={as(first)} />);
    view.rerender(<GlobalBudgetSection getChannel={as(second)} />);
    second.connect();

    second.reply("orchestration_status", {
      ok: true,
      data: {
        global_budget: {
          month: "2026-10",
          max_tokens: 2_000_000_000,
          opened: true,
          used_tokens: 1_310_000,
          reserved_tokens: 3_000_000,
          remaining_tokens: 1_995_690_000,
        },
      },
    });
    const text = screen.getByTestId("global-budget-status").textContent ?? "";
    expect(text).not.toContain("读取中…");
    expect(text).toContain("2026 年 10 月：上限 20 亿；已用 131 万");
  });
});
