// @vitest-environment jsdom
// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ModelContextCard } from "./ModelContextCard";
import type { ControlChannel } from "../ws/ControlChannel";
import type { IncomingMessage } from "../types/messages";

afterEach(cleanup);
function setup() {
  const sent: Array<{ type: string; payload?: { model?: string } }> = [];
  const listeners = new Set<(m: IncomingMessage) => void>();
  const channel = {
    send(m: (typeof sent)[number]) {
      sent.push(m);
      return true;
    },
    onMessage(fn: (m: IncomingMessage) => void) {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
  };
  render(<ModelContextCard getChannel={() => channel as unknown as ControlChannel} />);
  const emit = (message: unknown) =>
    act(() => {
      for (const fn of listeners) fn(message as IncomingMessage);
    });
  const catalog = (model: string) =>
    emit({
      type: "models_list_response",
      payload: {
        default_model: model,
        models: [{ id: model }, { id: "another-model" }],
      },
    });
  const context = (model: string, window: number) =>
    emit({
      type: "model_context_get_response",
      payload: {
        model,
        resolved: { context_window: window, compact_at_pct: 0.8, source: "override" },
        builtin: {},
      },
    });
  return { sent, catalog, context };
}

it("initially resolves the active provider model, with no hard-coded GPT request", () => {
  const { sent, catalog, context } = setup();
  expect(sent.some((m) => m.type === "model_context_get")).toBe(false);
  catalog("qwen38-flash-next");
  expect(sent.at(-1)).toEqual({
    type: "model_context_get",
    payload: { model: "qwen38-flash-next" },
  });
  context("qwen38-flash-next", 262144);
  expect((screen.getByTestId("model-context-select") as HTMLSelectElement).value).toBe(
    "qwen38-flash-next",
  );
  expect(screen.queryByText(/400K/)).toBeNull();
});

it("ignores late model responses and saves edits against the displayed model", () => {
  const { sent, catalog, context } = setup();
  catalog("qwen38-flash-next");
  fireEvent.change(screen.getByTestId("model-context-select"), {
    target: { value: "another-model" },
  });
  context("another-model", 131072);
  context("qwen38-flash-next", 262144);
  expect(screen.queryByText(/256K/)).toBeNull();
  fireEvent.click(screen.getByText("保存到全局"));
  expect(sent.at(-1)?.payload?.model).toBe("another-model");
});

it("catalog refresh does not override an explicit user selection", () => {
  const { sent, catalog, context } = setup();
  catalog("qwen38-flash-next");
  fireEvent.change(screen.getByTestId("model-context-select"), {
    target: { value: "another-model" },
  });
  context("another-model", 131072);
  const count = sent.length;
  catalog("qwen38-flash-next");
  expect(sent).toHaveLength(count);
  expect((screen.getByTestId("model-context-select") as HTMLSelectElement).value).toBe(
    "another-model",
  );
});

// 复核问题 4：先挂在已连接通道 A，换成未连接通道 B，B 连上后要重读模型状态，回执显示到界面。
class SwapChannel {
  state: "connecting" | "connected";
  sent: Array<{ type: string; payload?: { model?: string } }> = [];
  private listeners = new Set<(m: IncomingMessage) => void>();
  private states = new Set<(state: "connected") => void>();
  constructor(state: "connecting" | "connected") {
    this.state = state;
  }
  send(m: { type: string; payload?: { model?: string } }) {
    if (this.state !== "connected") return false;
    this.sent.push(m);
    return true;
  }
  onMessage(fn: (m: IncomingMessage) => void) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  onStateChange(fn: (state: "connected") => void) {
    this.states.add(fn);
    return () => this.states.delete(fn);
  }
  connect() {
    act(() => {
      this.state = "connected";
      this.states.forEach((fn) => fn("connected"));
    });
  }
  emit(message: unknown) {
    act(() => this.listeners.forEach((fn) => fn(message as IncomingMessage)));
  }
}

it("通道换成新对象：新通道连上后重读模型状态，收到回执后不停在加载中", () => {
  const first = new SwapChannel("connected");
  const second = new SwapChannel("connecting");
  const catalog = {
    type: "models_list_response",
    payload: { default_model: "qwen38-flash-next", models: [{ id: "qwen38-flash-next" }] },
  };
  const context = (window: number) => ({
    type: "model_context_get_response",
    payload: {
      model: "qwen38-flash-next",
      resolved: { context_window: window, compact_at_pct: 0.8, source: "override" },
      builtin: {},
    },
  });
  const view = render(<ModelContextCard getChannel={() => first as unknown as ControlChannel} />);
  first.emit(catalog);
  first.emit(context(128000));
  expect(screen.getByTestId("model-context-card").textContent).toContain("上下文总长度： 128K");

  view.rerender(<ModelContextCard getChannel={() => second as unknown as ControlChannel} />);
  second.connect();
  const types = second.sent.map((m) => m.type);
  expect(types).toContain("models_list");
  expect(types).toContain("context_compaction_get");
  expect(second.sent).toContainEqual({
    type: "model_context_get",
    payload: { model: "qwen38-flash-next" },
  });

  second.emit(catalog);
  second.emit(context(256000));
  const card = screen.getByTestId("model-context-card").textContent ?? "";
  expect(card).not.toContain("加载中…");
  expect(card).toContain("上下文总长度： 256K");
});
