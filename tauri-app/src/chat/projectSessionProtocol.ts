// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type {
  ProjectCatalogPage,
  ProjectDescriptor,
  ProjectInspection,
  ProjectRegistrationPreview,
  ProjectSessionDescriptor,
  ProjectSessionPage,
} from "../types/projectSessions";

export type ProjectProtocolErrorCode =
  | "invalid_request" | "path_empty" | "path_not_absolute" | "path_not_found"
  | "path_not_directory" | "path_unreadable" | "git_probe_timeout"
  | "git_root_invalid" | "project_not_found" | "project_identity_mismatch"
  | "project_revision_conflict" | "project_runs_active" | "session_not_found"
  | "session_deleted" | "request_id_conflict" | "binding_immutable"
  | "execution_root_required" | "execution_root_not_directory"
  | "workspace_unavailable" | "stale_cursor" | "cursor_invalid"
  | "upgrade_incomplete" | "provider_registry_unavailable"
  | "provider_binding_stale" | "session_create_failed";

export interface ProjectProtocolError {
  code: ProjectProtocolErrorCode | string;
  message: string;
}

export type ProjectProtocolResult<T> =
  | ({ ok: true } & T)
  | { ok: false; error: ProjectProtocolError };

export interface ProjectWsEnvelope<T = Record<string, unknown>> {
  type: string;
  request_id: string;
  payload: T;
}

export const newProjectRequestId = (): string =>
  globalThis.crypto?.randomUUID?.() ?? `project-${Date.now()}-${Math.random().toString(36).slice(2)}`;

export const projectRequest = <T>(type: string, payload: T): ProjectWsEnvelope<T> => ({
  type,
  request_id: newProjectRequestId(),
  payload,
});

export type ProjectPreviewResponse = ProjectProtocolResult<{ preview: ProjectRegistrationPreview }>;
export type ProjectRegisterResponse = ProjectProtocolResult<{ project: ProjectDescriptor; created: boolean }>;
export type SessionCreateResponse = ProjectProtocolResult<{ session: ProjectSessionDescriptor; replayed: boolean }>;
export type ProjectCatalogResponse = ProjectProtocolResult<Omit<ProjectCatalogPage, "request_id">>;
export type ProjectSessionsResponse = ProjectProtocolResult<Omit<ProjectSessionPage, "request_id">>;
export type ProjectInspectResponse = ProjectProtocolResult<Omit<ProjectInspection, "request_id">>;
export type ProjectRelocateResponse = ProjectProtocolResult<{ project: ProjectDescriptor }>;

export function responsePayload<T>(message: unknown, expectedType: string): {
  request_id: string;
  payload: T;
} | null {
  if (!message || typeof message !== "object") return null;
  const value = message as Record<string, unknown>;
  if (value.type !== expectedType || typeof value.request_id !== "string") return null;
  if (!value.payload || typeof value.payload !== "object") return null;
  return { request_id: value.request_id, payload: value.payload as T };
}
