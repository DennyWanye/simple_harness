// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Project/session catalog transport. Paths in these values are display-only:
 * the backend's immutable SessionProjectBinding remains execution authority.
 */
export type ProjectScope =
  | { scope_kind: "project"; project_id: string }
  | { scope_kind: "projectless"; project_id?: null };

/** Opaque base64url JSON. The client never parses or manufactures cursors. */
export type ProjectCatalogCursor = string;
export type ProjectSessionCursor = string;

export interface ProjectDescriptor {
  project_id: string;
  display_name: string;
  project_root: string;
  root_kind: "git" | "folder";
  project_revision: number;
  created_at: number;
  updated_at: number;
  last_opened_at: number;
  availability: "available" | "missing";
}

export interface ProjectSessionDescriptor {
  session_id: string;
  project_id: string | null;
  session_kind: "project" | "projectless";
  execution_kind: "project_root" | "explicit" | null;
  activity_at: number;
  created_at: number;
  turn_count: number;
  preview: string;
  title?: string;
  execution_root: string | null;
  source_session_id: string | null;
  availability: "available" | "missing" | "projectless";
}

export interface ProjectCatalogPage {
  request_id: string;
  schema_version: 1;
  catalog_revision: number;
  items: ProjectDescriptor[];
  next_cursor: ProjectCatalogCursor | null;
  pinned: ProjectDescriptor | null;
}

export interface ProjectSessionPage {
  request_id: string;
  schema_version: 1;
  catalog_revision: number;
  scope: { kind: "project" | "projectless"; project_id: string | null };
  items: ProjectSessionDescriptor[];
  next_cursor: ProjectSessionCursor | null;
  pinned: ProjectSessionDescriptor | null;
}

export interface ProjectRegistrationPreview {
  selected_path: string;
  detected_git_root: string | null;
  canonical_root: string;
  root_kind: "git" | "folder";
  display_name: string;
}

export interface ProjectInspection {
  request_id: string;
  project: ProjectDescriptor;
  git: {
    available: boolean;
    branch?: string | null;
    dirty?: boolean | null;
    error_code?: string | null;
  } | null;
}

export interface ProjectCatalogState {
  revision: number | null;
  projects: ProjectDescriptor[];
  next_cursor: ProjectCatalogCursor | null;
  loading: boolean;
  error: string | null;
}

export interface ProjectSessionPageState {
  revision: number | null;
  sessions: ProjectSessionDescriptor[];
  next_cursor: ProjectSessionCursor | null;
  loading: boolean;
  error: string | null;
}

export const projectScopeKey = (scope: ProjectScope): string =>
  scope.scope_kind === "project" ? `project:${scope.project_id}` : "projectless";

export function dedupeById<T>(
  rows: readonly T[],
  pinned: T | null | undefined,
  id: (row: T) => string,
): T[] {
  const result: T[] = [];
  const seen = new Set<string>();
  for (const row of pinned ? [pinned, ...rows] : rows) {
    const key = id(row);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    result.push(row);
  }
  return result;
}
