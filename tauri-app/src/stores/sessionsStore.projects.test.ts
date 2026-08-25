// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { beforeEach, describe, expect, it } from "vitest";
import { useSessionsStore } from "./sessionsStore";

describe("project catalog projection", () => {
  beforeEach(() => {
    useSessionsStore.setState({
      project_catalog: { revision: null, projects: [], next_cursor: null, loading: false, error: null },
      project_session_pages: {},
      project_inspections: {},
    });
  });

  it("dedupes pinned descriptors and replaces pages after revision changes", () => {
    const store = useSessionsStore.getState();
    store.apply_project_catalog_page({
      request_id: "r1",
      schema_version: 1,
      catalog_revision: 4,
      items: [{ project_id: "p1", display_name: "P", project_root: "/p", root_kind: "folder", project_revision: 1, created_at: 1, updated_at: 2, last_opened_at: 3, availability: "available" }],
      pinned: { project_id: "p1", display_name: "P", project_root: "/p", root_kind: "folder", project_revision: 1, created_at: 1, updated_at: 2, last_opened_at: 3, availability: "available" },
      next_cursor: null,
    });
    expect(useSessionsStore.getState().project_catalog.projects).toHaveLength(1);

    store.apply_project_catalog_page({
      request_id: "r2",
      schema_version: 1,
      catalog_revision: 5,
      items: [{ project_id: "p2", display_name: "Q", project_root: "/q", root_kind: "folder", project_revision: 1, created_at: 1, updated_at: 2, last_opened_at: 4, availability: "available" }],
      pinned: null,
      next_cursor: null,
    }, true);
    expect(useSessionsStore.getState().project_catalog.projects.map((p) => p.project_id)).toEqual(["p2"]);
  });

  it("keeps one zero-message Session after duplicate ACK/page delivery", () => {
    const page = {
      request_id: "r1",
      schema_version: 1 as const,
      catalog_revision: 9,
      scope: { kind: "project" as const, project_id: "p1" },
      items: [{ session_id: "s0", project_id: "p1", session_kind: "project" as const, execution_kind: "project_root" as const, execution_root: "/p", source_session_id: null, activity_at: 2, created_at: 2, turn_count: 0, preview: "", availability: "available" as const }],
      pinned: { session_id: "s0", project_id: "p1", session_kind: "project" as const, execution_kind: "project_root" as const, execution_root: "/p", source_session_id: null, activity_at: 2, created_at: 2, turn_count: 0, preview: "", availability: "available" as const },
      next_cursor: null,
    };
    useSessionsStore.getState().apply_project_session_page(page);
    useSessionsStore.getState().apply_project_session_page(page, true);
    expect(useSessionsStore.getState().project_session_pages["project:p1"].sessions).toHaveLength(1);
  });
});
