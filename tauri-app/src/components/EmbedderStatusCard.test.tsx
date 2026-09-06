import { act, fireEvent, render, screen, cleanup } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EmbedderStatusCard } from "./EmbedderStatusCard";
import type { ControlChannel } from "../ws/ControlChannel";
import type { IncomingMessage, EmbedderStatusResponse } from "../types/messages";

afterEach(cleanup);

it("renders cold/loading/ready/failed from metadata and refresh only queries", () => {
  let receive: (m: IncomingMessage) => void = () => {};
  const send = vi.fn();
  const unsubscribe = vi.fn();
  const channel = { send, onMessage: (fn: typeof receive) => { receive = fn; return unsubscribe; } };
  const getChannel = () => channel as unknown as ControlChannel;
  const { unmount } = render(<EmbedderStatusCard getChannel={getChannel} />);
  const update = (state: "cold" | "loading" | "ready" | "failed") => {
    const message: EmbedderStatusResponse = { type: "embedder_status_response", payload: {
      state, is_ready: state === "ready", is_mock: false, model_name: "tencent/WeMM-Embedding-2B",
      model_path: "/local/wemm", ...(state === "failed" ? { reason: "wemm_load_failed" } : {}),
    } };
    act(() => receive(message));
  };
  update("cold");
  expect(screen.getByText("按需加载")).toBeTruthy();
  expect(screen.queryByText("加载中…")).toBeNull();
  expect(screen.getByText("/local/wemm")).toBeTruthy();
  update("loading");
  expect(screen.getByText("加载中…")).toBeTruthy();
  update("ready");
  expect(screen.getByText("tencent/WeMM-Embedding-2B 已就绪 ✓")).toBeTruthy();
  update("failed");
  expect(screen.getByText("加载失败")).toBeTruthy();
  fireEvent.click(screen.getByTestId("embedder-status-refresh"));
  expect(send.mock.calls).toEqual([[{ type: "embedder_status", payload: {} }], [{ type: "embedder_status", payload: {} }]]);
  expect(screen.queryByText(/BGE-M3/)).toBeNull();
  unmount();
  expect(unsubscribe).toHaveBeenCalledOnce();
});

it("preserves absent-service and mock compatibility", () => {
  let receive: (m: IncomingMessage) => void = () => {};
  const channel = { send: vi.fn(), onMessage: (fn: typeof receive) => { receive = fn; return () => {}; } };
  render(<EmbedderStatusCard getChannel={() => channel as unknown as ControlChannel} />);
  act(() => receive({ type: "embedder_status_response", payload: { is_ready: false, is_mock: false, model_path: "", reason: "embedder_not_registered" } }));
  expect(screen.getByText("未启动")).toBeTruthy();
  act(() => receive({ type: "embedder_status_response", payload: { is_ready: true, is_mock: true, model_path: "/mock" } }));
  expect(screen.getByText("Mock 模式 ⚠")).toBeTruthy();
});
