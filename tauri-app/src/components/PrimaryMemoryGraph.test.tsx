import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PrimaryMemoryPanel } from "./PrimaryMemoryPanel";
import { graph, wire } from "../primary/testing/graphFixture";
// Lifecycle tests only; actual Cytoscape is exercised separately, never counted here.
vi.mock("./MemoryGraphCanvas", () => ({ memoryTypeLabels: { semantic: "事实与偏好" }, MemoryGraphCanvas: () => <div>renderer lifecycle seam</div> }));
afterEach(cleanup);
it("selected details disappear immediately on change/rebind and old replies cannot revive them", async () => {
  const w = wire(); const ui = render(<PrimaryMemoryPanel port={w.port} primaryRef="p" verifiedOwnerKey="owner:1" ready />);
  fireEvent.click(screen.getByRole("button", { name: "关系图" }));
  await act(async () => w.reply(1, graph));
  fireEvent.click(screen.getByRole("button", { name: /秋天偏好/ }));
  expect(screen.getByRole("complementary", { name: "选中记忆详情" })).toBeTruthy();
  await act(async () => w.emit({ type: "human_memory_changed", payload: {} }));
  expect(screen.queryByRole("complementary")).toBeNull();
  const idx = w.sent.findLastIndex((r) => r.operation === "primary.memory.graph");
  await act(async () => w.reply(idx, graph));
  expect(screen.queryByRole("complementary")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /秋天偏好/ }));
  ui.rerender(<PrimaryMemoryPanel port={w.port} primaryRef="p" verifiedOwnerKey={null} ready={false} />);
  expect(screen.queryByRole("complementary")).toBeNull(); expect(screen.queryByText(/秋天偏好/)).toBeNull();
});
