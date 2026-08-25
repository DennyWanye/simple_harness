// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Virtuoso } from "react-virtuoso";

import { controlWS } from "../code-panel/controlWs";
import { projectRequest, responsePayload, type ProjectCatalogResponse, type ProjectSessionsResponse, type SessionCreateResponse } from "../chat/projectSessionProtocol";
import { useSessionsStore } from "../stores/sessionsStore";
import { useControlWsState } from "../hooks/useControlWsState";
import type { ProjectDescriptor, ProjectSessionDescriptor, ProjectScope } from "../types/projectSessions";
import { projectScopeKey } from "../types/projectSessions";
import { topicDisplayLabel } from "../chat/topicTitle";
import { formatRelativeSec } from "../relativeTime";
import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import { ProjectPickerDialog } from "./ProjectPickerDialog";
import { ConfirmDialog } from "../code-panel/ConfirmDialog";
import { MAX_TITLE_LEN, normalizeTopicTitle } from "../chat/topicTitle";

export interface SessionListProps { activeSid: string; onSwitchSid: (sid: string) => void; }

export const PROJECT_GROUP_REFRESH_EVENT = "simple-harness:project-group-refresh";

type FlatRow =
  | { kind: "project"; project: ProjectDescriptor }
  | { kind: "scope"; scope: ProjectScope; label: string }
  | { kind: "session"; session: ProjectSessionDescriptor; scope: ProjectScope }
  | { kind: "load"; scope: ProjectScope }
  | { kind: "catalog-load" }
  | { kind: "empty"; scope: ProjectScope };

const PAGE_SIZE = 50;

export function SessionList({ activeSid, onSwitchSid }: SessionListProps) {
  const catalog = useSessionsStore((s) => s.project_catalog);
  const pages = useSessionsStore((s) => s.project_session_pages);
  const companionIdentityReady = useSessionsStore((s) => s.companion_owner !== null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(["projectless"]));
  const [picker, setPicker] = useState<{ sourceSid: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editingSid, setEditingSid] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [pendingDelete, setPendingDelete] = useState<ProjectSessionDescriptor | null>(null);
  const pendingCreates = useRef(new Set<string>());
  const acceptedCreates = useRef(new Set<string>());
  const pendingCatalog = useRef(new Map<string, boolean>());
  const pendingPages = useRef(new Map<string, { scope: ProjectScope; append: boolean }>());
  const wsState = useControlWsState();
  const previousWsState = useRef(wsState);

  const requestCatalog = useCallback((cursor: string | null = null) => {
    useSessionsStore.getState().begin_project_catalog(Boolean(cursor));
    const request = projectRequest("project_catalog_page", {
      cursor,
      limit: PAGE_SIZE,
      pinned_session_id: activeSid || null,
    });
    pendingCatalog.current.set(request.request_id, Boolean(cursor));
    controlWS.send(request);
  }, [activeSid]);

  const requestSessions = useCallback((scope: ProjectScope, cursor: string | null = null) => {
    useSessionsStore.getState().begin_project_sessions(projectScopeKey(scope));
    const request = projectRequest("project_sessions_page", {
      scope_kind: scope.scope_kind,
      project_id: scope.scope_kind === "project" ? scope.project_id : null,
      cursor,
      limit: PAGE_SIZE,
      pinned_session_id: activeSid || null,
    });
    pendingPages.current.set(request.request_id, { scope, append: Boolean(cursor) });
    controlWS.send(request);
  }, [activeSid]);

  const refreshAll = useCallback(() => {
    requestCatalog();
    requestSessions({ scope_kind: "projectless", project_id: null });
    for (const key of expanded) {
      if (key.startsWith("project:")) requestSessions({ scope_kind: "project", project_id: key.slice(8) });
    }
  }, [expanded, requestCatalog, requestSessions]);

  useEffect(() => {
    requestCatalog();
    requestSessions({ scope_kind: "projectless", project_id: null });
  }, [requestCatalog, requestSessions]);

  useEffect(() => controlWS.on_message((message: unknown) => {
    const generic = message as { type?: string };
    if (generic?.type === "session_deleted" || generic?.type === "session_renamed") {
      refreshAll();
      return;
    }
    const catalogResponse = responsePayload<ProjectCatalogResponse>(message, "project_catalog_page_response");
    if (catalogResponse) {
      const append = pendingCatalog.current.get(catalogResponse.request_id);
      if (append === undefined) return;
      pendingCatalog.current.delete(catalogResponse.request_id);
      if (!catalogResponse.payload.ok) {
        if (catalogResponse.payload.error.code === "stale_cursor") return requestCatalog();
        return useSessionsStore.getState().fail_project_catalog(catalogResponse.payload.error.message);
      }
      useSessionsStore.getState().apply_project_catalog_page({ request_id: catalogResponse.request_id, ...catalogResponse.payload }, append);
      if (catalogResponse.payload.pinned && activeSid) {
        requestSessions({ scope_kind: "project", project_id: catalogResponse.payload.pinned.project_id });
      }
      return;
    }
    const sessionsResponse = responsePayload<ProjectSessionsResponse>(message, "project_sessions_page_response");
    if (sessionsResponse) {
      const payload = sessionsResponse.payload;
      const pending = pendingPages.current.get(sessionsResponse.request_id);
      if (!pending) return;
      pendingPages.current.delete(sessionsResponse.request_id);
      if (!payload.ok) {
        if (payload.error.code === "stale_cursor") return requestSessions(pending.scope);
        useSessionsStore.getState().fail_project_sessions(projectScopeKey(pending.scope), payload.error.message);
        return;
      }
      useSessionsStore.getState().apply_project_session_page({ request_id: sessionsResponse.request_id, ...payload }, pending.append);
      return;
    }
    const createResponse = responsePayload<SessionCreateResponse>(message, "session_create_response");
    if (!createResponse || !pendingCreates.current.has(createResponse.request_id)) return;
    if (!createResponse.payload.ok) {
      pendingCreates.current.delete(createResponse.request_id);
      setError(createResponse.payload.error.message);
      return;
    }
    const session = createResponse.payload.session;
    if (acceptedCreates.current.has(session.session_id)) return;
    acceptedCreates.current.add(session.session_id);
    pendingCreates.current.delete(createResponse.request_id);
    useSessionsStore.getState().ensure(session.session_id);
    onSwitchSid(session.session_id);
    setPicker(null);
    refreshAll();
  }), [activeSid, onSwitchSid, refreshAll, requestCatalog, requestSessions]);

  useEffect(() => {
    const previous = previousWsState.current;
    previousWsState.current = wsState;
    if (wsState === "connected" && previous !== "connected") refreshAll();
  }, [refreshAll, wsState]);

  useEffect(() => {
    const refreshProjectGroup = (event: Event) => {
      const projectId = String(
        (event as CustomEvent<{ projectId?: string }>).detail?.projectId ?? "",
      ).trim();
      requestCatalog();
      if (projectId) {
        requestSessions({ scope_kind: "project", project_id: projectId });
      }
    };
    window.addEventListener(PROJECT_GROUP_REFRESH_EVENT, refreshProjectGroup);
    return () => window.removeEventListener(PROJECT_GROUP_REFRESH_EVENT, refreshProjectGroup);
  }, [requestCatalog, requestSessions]);

  const createSession = useCallback((projectId: string | null, sourceSid: string | null = null) => {
    if (!companionIdentityReady) return;
    const request = projectRequest("session_create", {
      project_id: projectId,
      source_session_id: sourceSid,
      execution_kind: projectId ? "project_root" : undefined,
    });
    pendingCreates.current.add(request.request_id);
    setError(null);
    if (!controlWS.send(request)) {
      pendingCreates.current.delete(request.request_id);
      setError("控制通道未连接，请稍后重试。");
    }
  }, [companionIdentityReady]);

  const toggleProject = (projectId: string) => {
    const key = `project:${projectId}`;
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
    if (!expanded.has(key) && !pages[key]) requestSessions({ scope_kind: "project", project_id: projectId });
  };

  const rows = useMemo<FlatRow[]>(() => {
    const result: FlatRow[] = [];
    for (const project of catalog.projects) {
      const scope: ProjectScope = { scope_kind: "project", project_id: project.project_id };
      const key = projectScopeKey(scope);
      result.push({ kind: "project", project });
      if (!expanded.has(key)) continue;
      const page = pages[key];
      if (!page || page.sessions.length === 0) result.push({ kind: "empty", scope });
      for (const session of page?.sessions ?? []) result.push({ kind: "session", session, scope });
      if (page?.next_cursor) result.push({ kind: "load", scope });
    }
    if (catalog.next_cursor) result.push({ kind: "catalog-load" });
    const projectless: ProjectScope = { scope_kind: "projectless", project_id: null };
    result.push({ kind: "scope", scope: projectless, label: "无项目会话" });
    const page = pages.projectless;
    if (!page || page.sessions.length === 0) result.push({ kind: "empty", scope: projectless });
    for (const session of page?.sessions ?? []) result.push({ kind: "session", session, scope: projectless });
    if (page?.next_cursor) result.push({ kind: "load", scope: projectless });
    return result;
  }, [catalog.next_cursor, catalog.projects, expanded, pages]);

  const renderRow = (_index: number, row: FlatRow) => {
    if (row.kind === "project") {
      const key = `project:${row.project.project_id}`;
      return <div data-testid={`project-group-${row.project.project_id}`} style={groupStyle}>
        <button type="button" onClick={() => toggleProject(row.project.project_id)} aria-expanded={expanded.has(key)} style={groupButtonStyle}>
          <span>{expanded.has(key) ? "▾" : "▸"}</span><span style={ellipsisStyle}>{row.project.display_name}</span>
          {row.project.availability === "missing" && <span data-testid={`project-missing-${row.project.project_id}`} style={{ color: tokens.color.warning.fg }}>目录不可用</span>}
        </button>
        <button type="button" aria-label={`在 ${row.project.display_name} 中新建 Session`} onClick={() => createSession(row.project.project_id)} style={tinyButtonStyle}>＋</button>
      </div>;
    }
    if (row.kind === "scope") return <div style={groupStyle}><strong>{row.label}</strong></div>;
    if (row.kind === "empty") return <div data-testid={`session-empty-${projectScopeKey(row.scope)}`} style={emptyStyle}>暂无 Session</div>;
    if (row.kind === "catalog-load") return <button type="button" onClick={() => requestCatalog(catalog.next_cursor)} style={loadStyle}>加载更多项目</button>;
    if (row.kind === "load") {
      const page = pages[projectScopeKey(row.scope)];
      return <button type="button" onClick={() => requestSessions(row.scope, page?.next_cursor ?? null)} style={loadStyle}>加载更多会话</button>;
    }
    const selected = row.session.session_id === activeSid;
    const editing = row.session.session_id === editingSid;
    return <div data-testid={`session-row-${row.session.session_id}`} style={{ ...sessionRowStyle, background: selected ? dark.card : "transparent", borderLeftColor: selected ? dark.accent : "transparent" }}>
      {editing ? <input
        autoFocus
        aria-label="重命名话题"
        data-testid={`session-rename-input-${row.session.session_id}`}
        maxLength={MAX_TITLE_LEN}
        value={draftTitle}
        onChange={(event) => setDraftTitle(event.target.value)}
        onBlur={() => finishRename(row.session)}
        onKeyDown={(event) => {
          if (event.key === "Enter") finishRename(row.session);
          if (event.key === "Escape") setEditingSid(null);
        }}
        style={{ ...sessionButtonStyle, border: `1px solid ${dark.accent}` }}
      /> : <button type="button" data-testid={`session-switch-${row.session.session_id}`} onClick={() => onSwitchSid(row.session.session_id)} onDoubleClick={() => { setEditingSid(row.session.session_id); setDraftTitle(row.session.title ?? ""); }} style={sessionButtonStyle}>
          <span style={ellipsisStyle}>{topicDisplayLabel(row.session)}</span>
          <small style={{ color: dark.textMuted }}>{row.session.turn_count === 0 ? "新建" : row.session.activity_at > 0 ? formatRelativeSec(row.session.activity_at) : ""}</small>
        </button>}
      {row.scope.scope_kind === "projectless" && <button type="button" data-testid={`session-continue-${row.session.session_id}`} title="在项目中继续" aria-label="在项目中继续" onClick={() => setPicker({ sourceSid: row.session.session_id })} style={tinyButtonStyle}>↗</button>}
      <button type="button" aria-label="删除会话" onClick={() => setPendingDelete(row.session)} style={tinyButtonStyle}>×</button>
    </div>;
  };

  const finishRename = (session: ProjectSessionDescriptor) => {
    const title = normalizeTopicTitle(draftTitle);
    setEditingSid(null);
    if (title === (session.title ?? "").trim()) return;
    controlWS.send({ type: "session_rename", payload: { session_id: session.session_id, title } });
  };

  const confirmDelete = () => {
    if (!pendingDelete) return;
    controlWS.send({ type: "session_delete", payload: { session_id: pendingDelete.session_id } });
    if (pendingDelete.session_id === activeSid) onSwitchSid("");
    setPendingDelete(null);
  };

  return <div data-testid="session-list" style={{ display: "flex", flexDirection: "column", minHeight: 0, flex: 1, gap: tokens.space.xs }}>
    <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: tokens.space.xs }}>
      <button type="button" data-testid="session-new-topic" disabled={!companionIdentityReady} onClick={() => createSession(null)} style={primaryButtonStyle}>＋ 新建普通会话</button>
      <button type="button" data-testid="project-add" disabled={!companionIdentityReady} onClick={() => setPicker({ sourceSid: null })} aria-label="添加项目" style={primaryButtonStyle}>⌘＋</button>
    </div>
    {error && <div role="alert" style={{ color: tokens.color.danger.fg, fontSize: tokens.text.sm.size }}>{error}</div>}
    <div style={{ flex: 1, minHeight: 0 }}><Virtuoso data={rows} computeItemKey={(_index, row) => row.kind === "session" ? `s:${row.session.session_id}` : row.kind === "project" ? `p:${row.project.project_id}` : `${row.kind}:${row.kind === "empty" || row.kind === "load" || row.kind === "scope" ? projectScopeKey(row.scope) : "catalog"}`} itemContent={renderRow} /></div>
    {picker && <ProjectPickerDialog open onClose={() => setPicker(null)} onProjectSelected={(projectId) => createSession(projectId, picker.sourceSid)} />}
    {pendingDelete && <ConfirmDialog title="删除会话？" message="该会话将从历史列表移除，项目和项目文件不会被删除。" confirm_label="删除" onConfirm={confirmDelete} onCancel={() => setPendingDelete(null)} />}
  </div>;
}

const ellipsisStyle: React.CSSProperties = { minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const primaryButtonStyle: React.CSSProperties = { padding: `${tokens.space.xs + 2}px ${tokens.space.sm}px`, borderRadius: tokens.radius.md, border: `1px dashed ${dark.borderStrong}`, background: "transparent", color: dark.accent, cursor: "pointer" };
const groupStyle: React.CSSProperties = { minHeight: 36, display: "flex", alignItems: "center", gap: tokens.space.xs, padding: `${tokens.space.xs}px ${tokens.space.sm}px`, color: dark.text };
const groupButtonStyle: React.CSSProperties = { flex: 1, minWidth: 0, display: "flex", gap: tokens.space.xs, alignItems: "center", border: 0, background: "transparent", color: "inherit", cursor: "pointer", textAlign: "left" };
const tinyButtonStyle: React.CSSProperties = { border: 0, background: "transparent", color: dark.accent, cursor: "pointer", padding: tokens.space.xs };
const sessionRowStyle: React.CSSProperties = { minHeight: 44, display: "flex", alignItems: "center", padding: `2px ${tokens.space.sm}px 2px ${tokens.space.md}px`, borderLeft: "3px solid transparent" };
const sessionButtonStyle: React.CSSProperties = { flex: 1, minWidth: 0, display: "flex", flexDirection: "column", alignItems: "stretch", gap: 2, border: 0, background: "transparent", color: dark.text, cursor: "pointer", textAlign: "left" };
const emptyStyle: React.CSSProperties = { padding: `${tokens.space.sm}px ${tokens.space.lg}px`, color: dark.textFaint, fontSize: tokens.text.sm.size };
const loadStyle: React.CSSProperties = { width: "100%", border: 0, background: "transparent", color: dark.accent, cursor: "pointer", padding: tokens.space.sm };
