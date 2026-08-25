// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionList } from "./SessionList";
import { controlWS } from "../code-panel/controlWs";
import { useSessionsStore } from "../stores/sessionsStore";

vi.mock("react-virtuoso", () => ({
  Virtuoso: ({ data, itemContent }: { data: unknown[]; itemContent: (index: number, row: unknown) => React.ReactNode }) => <div>{data.map((row, index) => <React.Fragment key={index}>{itemContent(index, row)}</React.Fragment>)}</div>,
}));
vi.mock("../code-panel/controlWs", () => ({ controlWS: {
  send: vi.fn(() => true), on_message: vi.fn(), state: vi.fn(() => "connected"),
} }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

let listeners: Array<(message: unknown) => void> = [];
const emit = (message: unknown) => act(() => listeners.forEach((listener) => listener(message)));
const request = (type: string) => vi.mocked(controlWS.send).mock.calls.map(([value]) => value as { type: string; request_id: string; payload: Record<string, unknown> }).findLast((value) => value.type === type)!;
const project = { project_id: "p1", display_name: "simple_harness", project_root: "/repo", root_kind: "git" as const, project_revision: 2, availability: "missing" as const, created_at: 1, updated_at: 2, last_opened_at: 3 };
const session = { session_id: "s1", project_id: "p1", session_kind: "project" as const, execution_kind: "project_root" as const, execution_root: "/repo", source_session_id: null, title: "项目会话", preview: "", turn_count: 0, activity_at: 4, created_at: 4, availability: "missing" as const };

describe("SessionList project catalog", () => {
  beforeEach(() => {
    listeners = [];
    vi.mocked(controlWS.send).mockClear();
    vi.mocked(controlWS.send).mockReturnValue(true);
    vi.mocked(controlWS.on_message).mockImplementation((listener: (message: unknown) => void) => { listeners.push(listener); return () => { listeners = listeners.filter((value) => value !== listener); }; });
    useSessionsStore.setState({
      companion_owner: { profile_id: "local", profile_generation: 1 },
      project_catalog: { revision: null, projects: [], next_cursor: null, loading: false, error: null },
      project_session_pages: {}, project_inspections: {},
    });
  });
  afterEach(cleanup);

  it("renders grouped/missing Project and a pinned zero-message Session once", async () => {
    render(<SessionList activeSid="s1" onSwitchSid={() => {}} />);
    const catalogRequest = request("project_catalog_page");
    emit({ type: "project_catalog_page_response", request_id: catalogRequest.request_id, payload: { ok: true, schema_version: 1, catalog_revision: 7, items: [project], next_cursor: null, pinned: project } });
    expect(useSessionsStore.getState().project_catalog.projects).toHaveLength(1);
    await waitFor(() => expect(screen.getByTestId("project-missing-p1")).toBeTruthy());
    fireEvent.click(screen.getByText("simple_harness"));
    const pageRequest = request("project_sessions_page");
    emit({ type: "project_sessions_page_response", request_id: pageRequest.request_id, payload: { ok: true, schema_version: 1, catalog_revision: 7, scope: { kind: "project", project_id: "p1" }, items: [session], next_cursor: null, pinned: session } });
    await waitFor(() => expect(screen.getAllByTestId("session-row-s1")).toHaveLength(1));
    expect(screen.getByText("新建")).toBeTruthy();
  });

  it("stale cursor discards continuation and requests the first page", () => {
    render(<SessionList activeSid="" onSwitchSid={() => {}} />);
    const first = request("project_catalog_page");
    emit({ type: "project_catalog_page_response", request_id: first.request_id, payload: { ok: true, schema_version: 1, catalog_revision: 1, items: [project], next_cursor: "opaque", pinned: null } });
    fireEvent.click(screen.getByText("加载更多项目"));
    const continuation = request("project_catalog_page");
    expect(continuation.payload.cursor).toBe("opaque");
    emit({ type: "project_catalog_page_response", request_id: continuation.request_id, payload: { ok: false, error: { code: "stale_cursor", message: "stale" } } });
    expect(request("project_catalog_page").payload.cursor).toBeNull();
  });

  it("ordinary to Project always creates a new Session with source sid", () => {
    const onSwitch = vi.fn();
    render(<SessionList activeSid="ordinary" onSwitchSid={onSwitch} />);
    const ordinaryRequest = vi.mocked(controlWS.send).mock.calls.map(([value]) => value as { type: string; request_id: string }).find((value) => value.type === "project_sessions_page")!;
    const ordinary = { ...session, session_id: "ordinary", project_id: null, session_kind: "projectless", execution_kind: null, execution_root: null, availability: "projectless", title: "普通会话" };
    emit({ type: "project_sessions_page_response", request_id: ordinaryRequest.request_id, payload: { ok: true, schema_version: 1, catalog_revision: 1, scope: { kind: "projectless", project_id: null }, items: [ordinary], next_cursor: null, pinned: ordinary } });
    fireEvent.click(screen.getByTestId("session-continue-ordinary"));
    expect(screen.getByTestId("project-picker-dialog")).toBeTruthy();
    // The original sid remains active until a session_create ACK arrives.
    expect(onSwitch).not.toHaveBeenCalled();
  });

  it("lost/duplicate create ACK switches once and zero-message row comes from refresh", () => {
    const onSwitch = vi.fn();
    render(<SessionList activeSid="" onSwitchSid={onSwitch} />);
    fireEvent.click(screen.getByTestId("session-new-topic"));
    const create = request("session_create");
    expect(create.payload).toMatchObject({ project_id: null, source_session_id: null });
    const response = { type: "session_create_response", request_id: create.request_id, payload: { ok: true, session: { ...session, session_id: "born", project_id: null, session_kind: "projectless", execution_kind: null, execution_root: null, availability: "projectless" }, replayed: false } };
    emit(response); emit(response);
    expect(onSwitch).toHaveBeenCalledTimes(1);
    expect(onSwitch).toHaveBeenCalledWith("born");
  });
});
