// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ControlMessage, IncomingMessage } from "../../types/messages";
import { MissionStory } from "./MissionStory";
import {
  headline, itemLine, parseTurnDetail, reuseCards, stepNames, storyFromExecution, turnBadge, turnGist, turnTitle,
  type StoryItem, type TurnCard, type TurnDetail,
} from "./storyModel";
import { mergePages, parseExecutionPage } from "../liveGraph/model";
import { M, snapshot } from "../liveGraph/fixture";
import { PROTOCOL_ERROR_TEXT, guardIncoming } from "../../ws/orchestrationContracts";

const SNAP = "taskgraph.execution_snapshot";
const DETAIL = "taskgraph.execution_detail";

class FakeChannel {
  readonly sent: ControlMessage[] = [];
  private readonly listeners = new Set<(message: IncomingMessage) => void>();
  send = (message: ControlMessage) => { this.sent.push(message); return true; };
  onMessage = (listener: (message: IncomingMessage) => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  emit(message: unknown) {
    act(() => { for (const listener of [...this.listeners]) listener(message as IncomingMessage); });
  }
  all(type: string): ControlMessage[] { return this.sent.filter((m) => m.type === type); }
  reply(data: unknown, ok = true, errorCode?: string) {
    const request = [...this.sent].reverse().find((m) => m.type === SNAP);
    this.emit({ type: SNAP + "_response", payload: { request_id: request?.request_id, ok, data, error: ok ? undefined : "读不到", error_code: errorCode } });
  }
  detail(nodeId: string, items: unknown[], hidden = 0) {
    const request = [...this.sent].reverse().find((m) => m.type === DETAIL && (m.payload as { node_id?: string }).node_id === nodeId);
    this.emit({ type: DETAIL + "_response", payload: { request_id: request?.request_id, ok: true,
      data: { schema_version: 1, mission_id: M, node: { node_id: nodeId }, turn: null, items, hidden_items: hidden } } });
  }
  push(mission = M) {
    this.emit({ type: "mission_changed", payload: { mission_id: mission, status: "ACTIVE", last_seq: 1 } });
  }
}

afterEach(() => { cleanup(); vi.useRealTimers(); });

const WORK_ITEMS = [
  { t: "tool", tool: "workspace_list", ok: true, files: [], file_count: 0 },
  { t: "tool", tool: "workspace_read_file", ok: false, error: "no such file: sources/需求.md", count: 2 },
  { t: "say", text: "资料不在工作区，我按任务描述来写。" },
  { t: "tool", tool: "workspace_write_file", ok: true, path: "NOTES.md", bytes: 5933 },
  { t: "submit", outcome: "candidate", text: "写好了 NOTES.md" },
];
const REVIEW_ITEMS = [{ t: "verdict", verdict: "REWORK", reasons: [{ verdict: "FAIL", text: "总预算是假设值" }, { verdict: "INFO", text: "格式没问题" }] }];

const view = (extra: Record<string, unknown> = {}, overrides = {}) => mergePages([parseExecutionPage(snapshot(extra, overrides), M)]);
const detailOf = (items: unknown[]): TurnDetail =>
  parseTurnDetail({ mission_id: M, node: { node_id: "n" }, items, hidden_items: 0 }, M, "n");
const storyWith = (details: Record<string, unknown[]> = {}) =>
  storyFromExecution(view(), new Map(Object.entries(details).map(([id, items]) => [id, detailOf(items)])));
const card = (id: string, details: Record<string, unknown[]> = {}) =>
  storyWith(details).cards.find((c) => c.id === id) as TurnCard;

describe("storyModel（走 SDK 执行过程接口）", () => {
  it("执行过程节点按时间变成卡片：模型回合与节点事件", () => {
    const story = storyWith();
    expect(story.cards.map((c) => c.id + ":" + c.kind)).toEqual([
      "planning:p1:plan", "plan_revision:1:event", "attempt:a1:work", "check:r-a1:review", "attempt:b1:work",
      "check:r-b1:event", "repair_request:rr:event", "planning:p2:plan", "attempt:b2:work"]);
    expect(card("attempt:b2").state).toBe("running");
    expect(card("attempt:a1").items).toBeNull(); // 明细没读之前没有行
    expect(() => parseTurnDetail({ mission_id: M, node: { node_id: "x" }, items: [] }, M, "y")).toThrow();
  });

  it("明细每一行说人话：找不到文件、重复次数、写入大小；系统提醒一类的行不认", () => {
    const work = card("attempt:a1", { "attempt:a1": [...WORK_ITEMS, { t: "feedback", text: "请调用工具" }] });
    expect(work.items!.map((i) => itemLine(i).text)).toEqual([
      "查看工作区：空的", "读取文件：找不到 sources/需求.md ×2", "资料不在工作区，我按任务描述来写。",
      "写入 NOTES.md（5.8 KB）", "提交结果：写好了 NOTES.md"]);
    const run = itemLine({ t: "tool", tool: "run_tests", ok: true, passed: false, tail: "1 failed" } as StoryItem);
    expect([run.text, run.tone]).toEqual(["运行检查：未通过（1 failed）", "bad"]);
    const verdict = itemLine(card("check:r-a1", { "check:r-a1": REVIEW_ITEMS }).items![0]);
    expect(verdict.text).toBe("结论：要求返工");
    expect(verdict.list).toEqual([{ tag: "不满足", good: false, text: "总预算是假设值" }, { tag: "提示", good: null, text: "格式没问题" }]);
  });

  it("结果标签：进行中 / 已停止 / 审阅没读明细时用节点自带结论", () => {
    expect(turnBadge(card("attempt:b2"), false).text).toBe("进行中…");
    expect(turnBadge(card("attempt:b2"), true).text).toBe("已停止");
    expect(turnBadge(card("attempt:a1"), false).text).toBe("已提交");
    expect(turnBadge(card("check:r-a1"), false)).toEqual({ text: "通过", tone: "good" });
    expect(turnBadge(card("check:r-a1", { "check:r-a1": REVIEW_ITEMS }), false)).toEqual({ text: "要求返工", tone: "bad" });
  });

  it("折叠摘要：没读明细用 SDK 摘要；读了明细给写了哪些文件、失败几次", () => {
    expect(turnGist(card("attempt:a1"))).toBe("写好了 NOTES.md");
    expect(turnGist(card("attempt:a1", { "attempt:a1": WORK_ITEMS }))).toBe("写入 NOTES.md · 2 次操作失败 · 写好了 NOTES.md");
  });

  it("步骤名用文件，顶部一句话给进度与结局", () => {
    const story = storyWith();
    const names = stepNames(story.steps);
    expect(names.get("task-a")).toEqual({ number: 1, title: "产出 NOTES.md", key: "notes", current: true });
    expect(turnTitle(card("attempt:b2"), names)).toBe("执行者 · 第 2 步「产出 NOTES.md」 · 第 2 次尝试");
    expect(turnTitle(card("check:r-a1"), names)).toBe("审阅员 · 检查第 1 步「产出 NOTES.md」");
    expect(headline(story, names, "ACTIVE")).toBe("正在进行：执行者 · 第 2 步「产出 NOTES.md」 · 第 2 次尝试 · 已完成 1/2 步");
    expect(headline(story, names, "FAILED")).toBe("任务失败 · 已完成 1/2 步");
  });

  it("内容没变的卡片沿用旧对象（不重画）", () => {
    const first = storyWith().cards;
    const second = storyWith({ "attempt:b2": WORK_ITEMS }).cards;
    const merged = reuseCards(first, second);
    expect(merged[2]).toBe(first[2]);
    expect(merged[8]).not.toBe(first[8]);
  });
});

describe("MissionStory", () => {
  it("读执行过程；进行中的回合自动展开并读它的明细；点开别的卡片再读那一张", () => {
    const channel = new FakeChannel();
    render(<MissionStory missionId={M} channel={channel} missionStatus="ACTIVE" />);
    expect(channel.all(SNAP)).toHaveLength(1);
    expect(channel.all(SNAP)[0].payload).toEqual({ mission_id: M });
    expect(channel.all("mission_story")).toHaveLength(0);
    channel.reply(snapshot());
    expect(screen.getByRole("status").textContent).toContain("已完成 1/2 步");
    expect(channel.all(DETAIL).map((m) => m.payload)).toEqual([{ mission_id: M, node_id: "attempt:b2" }]);
    expect(screen.getByTestId("story-card-attempt:b2").textContent).toContain("正在读取这一回合的明细");
    channel.detail("attempt:b2", [{ t: "say", text: "开始发布" }]);
    expect(screen.getByTestId("story-card-attempt:b2").textContent).toContain("开始发布（模型生成）");
    const work = screen.getByTestId("story-card-attempt:a1");
    expect(work.textContent).toContain("写好了 NOTES.md"); // 折叠：SDK 摘要
    fireEvent.click(screen.getByRole("button", { name: /执行者 · 第 1 步/ }));
    expect(channel.all(DETAIL).at(-1)?.payload).toEqual({ mission_id: M, node_id: "attempt:a1" });
    channel.detail("attempt:a1", WORK_ITEMS);
    expect(work.textContent).toContain("读取文件：找不到 sources/需求.md ×2");
    const failed = screen.getByTestId("story-card-check:r-b1").textContent;
    expect(failed).toContain("第 2 步「产出 NOTES.md」没通过检查");
    expect(failed).toContain("artifact_not_in_result: NOTES.md");
  });

  it("分页读完再显示；翻页期间过程变了从第一页重读一次", () => {
    const channel = new FakeChannel();
    render(<MissionStory missionId={M} channel={channel} missionStatus="ACTIVE" />);
    channel.reply(snapshot({ complete: false, next_cursor: "c1" }));
    expect(channel.all(SNAP).at(-1)?.payload).toEqual({ mission_id: M, cursor: "c1" });
    channel.reply(null, false, "SNAPSHOT_CHANGED");
    expect(channel.all(SNAP)).toHaveLength(3);
    expect(channel.all(SNAP).at(-1)?.payload).toEqual({ mission_id: M });
    channel.reply(snapshot());
    expect(screen.getByTestId("story-card-attempt:a1")).toBeTruthy();
  });

  it("推送最快 2 秒读一次，在途只合并成一次；进行中且展开的回合随重读刷新明细", () => {
    vi.useFakeTimers();
    const channel = new FakeChannel();
    render(<MissionStory missionId={M} channel={channel} missionStatus="ACTIVE" />);
    channel.reply(snapshot());
    channel.detail("attempt:b2", []);
    channel.push();
    channel.push();
    channel.push("mission-other");
    expect(channel.all(SNAP)).toHaveLength(1); // throttled
    act(() => { vi.advanceTimersByTime(2000); });
    expect(channel.all(SNAP)).toHaveLength(2);
    channel.push(); // in flight → one follow-up after the answer
    channel.push();
    channel.reply(snapshot({ execution_cut: { observed_at_ms: 2, imported_through_seq: 11, execution_hash: "h2", runtime_source_watermarks: [], coverage: "COMPLETE" } }));
    expect(channel.all(DETAIL).filter((m) => (m.payload as { node_id: string }).node_id === "attempt:b2")).toHaveLength(2);
    act(() => { vi.advanceTimersByTime(2000); });
    expect(channel.all(SNAP)).toHaveLength(3);
  });

  it("进行中的回合 10 分钟没动静：提示可能卡住并告诉外面", () => {
    const onStalled = vi.fn();
    const channel = new FakeChannel();
    render(<MissionStory missionId={M} channel={channel} missionStatus="ACTIVE" onStalled={onStalled} />);
    const old = Date.now() - 11 * 60 * 1000;
    const nodes = (snapshot().execution_nodes as Record<string, unknown>[]).map((n) => n.node_id === "attempt:b2" ? { ...n, at_ms: old } : n);
    channel.reply(snapshot({}, { nodes }));
    expect(screen.getByTestId("story-card-attempt:b2").textContent).toContain("可能卡住");
    expect(onStalled).toHaveBeenLastCalledWith(1);
  });

  it("读取失败给出提示", () => {
    const channel = new FakeChannel();
    render(<MissionStory missionId={M} channel={channel} missionStatus="ACTIVE" />);
    channel.reply(null, false);
    expect(screen.getByRole("alert").textContent).toBe("读不到");
  });

  it("U09：回合明细收到坏消息——卡片保留摘要，显示协议错那句话", () => {
    const channel = new FakeChannel();
    render(<MissionStory missionId={M} channel={channel} missionStatus="ACTIVE" />);
    channel.reply(snapshot());
    const ask = channel.all(DETAIL).at(-1)!;
    channel.emit(guardIncoming({ type: DETAIL + "_response", payload: { request_id: ask.request_id, ok: true,
      data: { schema_version: 1, mission_id: M, node: { node_id: "attempt:b2" }, turn: null, items: [], hidden_items: 0 } } }));
    expect(screen.getByRole("alert").textContent).toBe(PROTOCOL_ERROR_TEXT);
    expect(screen.getByTestId("story-card-attempt:a1").textContent).toContain("写好了 NOTES.md");
  });
});
