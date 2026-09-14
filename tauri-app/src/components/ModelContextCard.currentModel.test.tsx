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
    send(m: (typeof sent)[number]) { sent.push(m); return true; },
    onMessage(fn: (m: IncomingMessage) => void) {
      listeners.add(fn); return () => { listeners.delete(fn); };
    },
  };
  render(<ModelContextCard getChannel={() => channel as unknown as ControlChannel} />);
  const emit = (message: unknown) => act(() => {
    for (const fn of listeners) fn(message as IncomingMessage);
  });
  const catalog = (model: string) => emit({ type: "models_list_response", payload: {
    default_model: model, models: [{ id: model }, { id: "another-model" }],
  } });
  const context = (model: string, window: number) => emit({ type: "model_context_get_response", payload: {
    model, resolved: { context_window: window, compact_at_pct: 0.8, source: "override" },
    builtin: {},
  } });
  return { sent, catalog, context };
}

it("initially resolves the active provider model, with no hard-coded GPT request", () => {
  const { sent, catalog, context } = setup();
  expect(sent.some(m => m.type === "model_context_get")).toBe(false);
  catalog("qwen38-flash-next");
  expect(sent.at(-1)).toEqual({ type: "model_context_get", payload: { model: "qwen38-flash-next" } });
  context("qwen38-flash-next", 262144);
  expect((screen.getByTestId("model-context-select") as HTMLSelectElement).value).toBe("qwen38-flash-next");
  expect(screen.queryByText(/400K/)).toBeNull();
});

it("ignores late model responses and saves edits against the displayed model", () => {
  const { sent, catalog, context } = setup();
  catalog("qwen38-flash-next");
  fireEvent.change(screen.getByTestId("model-context-select"), { target: { value: "another-model" } });
  context("another-model", 131072);
  context("qwen38-flash-next", 262144);
  expect(screen.queryByText(/256K/)).toBeNull();
  fireEvent.click(screen.getByText("保存到全局"));
  expect(sent.at(-1)?.payload?.model).toBe("another-model");
});

it("catalog refresh does not override an explicit user selection", () => {
  const { sent, catalog, context } = setup();
  catalog("qwen38-flash-next");
  fireEvent.change(screen.getByTestId("model-context-select"), { target: { value: "another-model" } });
  context("another-model", 131072);
  const count = sent.length;
  catalog("qwen38-flash-next");
  expect(sent).toHaveLength(count);
  expect((screen.getByTestId("model-context-select") as HTMLSelectElement).value).toBe("another-model");
});
