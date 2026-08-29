// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { ProjectPickerDialog } from "./ProjectPickerDialog";
import { controlWS } from "../code-panel/controlWs";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("../code-panel/controlWs", () => ({ controlWS: { send: vi.fn(() => true), on_message: vi.fn() } }));

let listeners: Array<(message: unknown) => void> = [];
describe("ProjectPickerDialog", () => {
  beforeEach(() => {
    listeners = [];
    vi.mocked(invoke).mockResolvedValue("/repo/child");
    vi.mocked(controlWS.send).mockClear();
    vi.mocked(controlWS.on_message).mockImplementation((listener: (message: unknown) => void) => { listeners.push(listener); return () => {}; });
  });
  afterEach(cleanup);

  it("previews selected/Git/effective roots and defaults to Git root", async () => {
    render(<ProjectPickerDialog open onClose={() => {}} onProjectSelected={() => {}} />);
    await act(async () => fireEvent.click(screen.getByTestId("project-picker-choose")));
    const request = vi.mocked(controlWS.send).mock.calls.at(-1)![0] as unknown as { request_id: string; payload: Record<string, unknown> };
    expect(request.payload.mode).toBe("git_root");
    act(() => listeners.forEach((listener) => listener({ type: "project_preview_register_response", request_id: request.request_id, payload: { ok: true, preview: { selected_path: "/repo/child", detected_git_root: "/repo", canonical_root: "/repo", root_kind: "git", display_name: "repo" } } })));
    expect(screen.getByText("/repo/child")).toBeTruthy();
    expect(screen.getAllByText("/repo")).toHaveLength(2);
    fireEvent.click(screen.getByTestId("project-picker-explicit-child"));
    expect((vi.mocked(controlWS.send).mock.calls.at(-1)![0] as { payload: Record<string, unknown> }).payload.mode).toBe("selected_folder");
  });

  it("uses the exact selected folder for an ordinary Session", async () => {
    render(
      <ProjectPickerDialog
        open
        onClose={() => {}}
        onProjectSelected={() => {}}
        onDefaultSelected={() => {}}
      />,
    );
    await act(async () => fireEvent.click(screen.getByTestId("project-picker-choose")));
    const request = vi.mocked(controlWS.send).mock.calls.at(-1)![0] as unknown as {
      payload: Record<string, unknown>;
    };
    expect(request.payload.mode).toBe("selected_folder");
    expect(screen.getByText("创建普通 Session")).toBeTruthy();
  });
});
