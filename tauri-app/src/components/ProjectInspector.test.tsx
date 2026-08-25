// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { ProjectInspector } from "./ProjectInspector";
import { useSessionsStore } from "../stores/sessionsStore";
import { controlWS } from "../code-panel/controlWs";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("../code-panel/controlWs", () => ({ controlWS: { send: vi.fn(() => true), on_message: vi.fn(() => () => {}) } }));

const project = { project_id: "p1", display_name: "Project One", project_root: "/old", root_kind: "git" as const, project_revision: 3, availability: "missing" as const, created_at: 1, updated_at: 2, last_opened_at: 3 };

describe("ProjectInspector", () => {
  beforeEach(() => {
    vi.mocked(controlWS.send).mockClear();
    useSessionsStore.setState({
      project_session_pages: { "project:p1": { revision: 1, next_cursor: null, loading: false, error: null, sessions: [{ session_id: "s1", project_id: "p1", session_kind: "project", execution_kind: "explicit", execution_root: "/worktree", source_session_id: null, title: "", preview: "", turn_count: 0, activity_at: 2, created_at: 1, availability: "missing" }] } },
      project_inspections: { p1: { request_id: "seed", project, git: { available: true, branch: "main", dirty: true } } },
    });
  });
  afterEach(cleanup);

  it("shows immutable roots/Git and exposes relocate only while missing", async () => {
    vi.mocked(invoke).mockResolvedValueOnce("/new");
    render(<ProjectInspector activeSid="s1" onSwitchSid={() => {}} />);
    expect(screen.getAllByText("/old").length).toBeGreaterThan(0);
    expect(screen.getAllByText("/worktree").length).toBeGreaterThan(0);
    expect(screen.getByText(/main · 有未提交更改/)).toBeTruthy();
    expect(screen.queryByText("修改根目录")).toBeNull();
    fireEvent.click(screen.getByTestId("project-relocate"));
    await vi.waitFor(() => expect(controlWS.send).toHaveBeenCalledWith(expect.objectContaining({ type: "project_relocate", payload: { project_id: "p1", new_path: "/new", expected_project_revision: 3 } })));
  });
});
