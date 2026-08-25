// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { controlWS } from "../code-panel/controlWs";
import { projectRequest, responsePayload, type ProjectInspectResponse, type ProjectRelocateResponse, type SessionCreateResponse } from "../chat/projectSessionProtocol";
import { useSessionsStore } from "../stores/sessionsStore";
import { ProjectPickerDialog } from "./ProjectPickerDialog";
import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import "./ProjectInspector.css";
import { PROJECT_GROUP_REFRESH_EVENT } from "./SessionList";

export function ProjectInspector({ activeSid, onSwitchSid }: { activeSid: string; onSwitchSid: (sid: string) => void }) {
  const pages = useSessionsStore((s) => s.project_session_pages);
  const inspections = useSessionsStore((s) => s.project_inspections);
  const [expanded, setExpanded] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef(new Map<string, "inspect" | "create" | "relocate">());
  const session = useMemo(() => Object.values(pages).flatMap((page) => page.sessions).find((item) => item.session_id === activeSid) ?? null, [activeSid, pages]);
  const inspection = session?.project_id ? inspections[session.project_id] : null;

  useEffect(() => {
    if (!session?.project_id) return;
    const request = projectRequest("project_inspect", { project_id: session.project_id });
    pending.current.set(request.request_id, "inspect");
    controlWS.send(request);
  }, [session?.project_id]);

  useEffect(() => controlWS.on_message((message: unknown) => {
    const inspect = responsePayload<ProjectInspectResponse>(message, "project_inspect_response");
    if (inspect && pending.current.get(inspect.request_id) === "inspect") {
      pending.current.delete(inspect.request_id);
      if (!inspect.payload.ok) return setError(inspect.payload.error.message);
      useSessionsStore.getState().apply_project_inspection({ request_id: inspect.request_id, ...inspect.payload });
      return;
    }
    const created = responsePayload<SessionCreateResponse>(message, "session_create_response");
    if (created && pending.current.get(created.request_id) === "create") {
      pending.current.delete(created.request_id);
      if (!created.payload.ok) return setError(created.payload.error.message);
      useSessionsStore.getState().ensure(created.payload.session.session_id);
      onSwitchSid(created.payload.session.session_id);
      setPickerOpen(false);
      return;
    }
    const relocated = responsePayload<ProjectRelocateResponse>(message, "project_relocate_response");
    if (relocated && pending.current.get(relocated.request_id) === "relocate") {
      pending.current.delete(relocated.request_id);
      if (!relocated.payload.ok) return setError(relocated.payload.error.message);
      const currentGit = inspection?.git ?? null;
      useSessionsStore.getState().apply_project_inspection({ request_id: relocated.request_id, project: relocated.payload.project, git: currentGit });
      window.dispatchEvent(new CustomEvent(PROJECT_GROUP_REFRESH_EVENT, {
        detail: { projectId: relocated.payload.project.project_id },
      }));
    }
  }), [inspection?.git, onSwitchSid]);

  const create = (projectId: string | null, sourceSid: string | null) => {
    const request = projectRequest("session_create", { project_id: projectId, source_session_id: sourceSid, execution_kind: projectId ? "project_root" : undefined });
    pending.current.set(request.request_id, "create");
    controlWS.send(request);
  };
  const relocate = async () => {
    if (!inspection) return;
    try {
      const newPath = await invoke<string | null>("open_directory_dialog");
      if (!newPath) return;
      const request = projectRequest("project_relocate", { project_id: inspection.project.project_id, new_path: newPath, expected_project_revision: inspection.project.project_revision });
      pending.current.set(request.request_id, "relocate");
      controlWS.send(request);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };
  const open = async () => {
    if (!inspection) return;
    try { await invoke("open_project_directory", { path: inspection.project.project_root }); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };

  const label = !activeSid ? "未选择会话" : session?.session_kind === "projectless" ? "普通会话" : inspection?.project.display_name ?? "项目加载中…";
  const content = <>
    <strong style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>{label}</strong>
    {session?.session_kind === "projectless" && <button type="button" data-testid="inspector-continue" onClick={() => setPickerOpen(true)} style={buttonStyle}>在项目中继续</button>}
  </>;
  const projectDetails = inspection && <div style={detailsStyle}>
    <Field label="项目根目录" value={inspection.project.project_root} />
    {session?.execution_root && session.execution_root !== inspection.project.project_root && <Field label="执行目录" value={session.execution_root} />}
    <Field label="Git" value={inspection.git?.available ? `${inspection.git.branch || "detached"}${inspection.git.dirty ? " · 有未提交更改" : " · 干净"}` : "不可用"} />
    <div style={{ display: "flex", flexWrap: "wrap", gap: tokens.space.xs }}>
      <button type="button" onClick={() => void navigator.clipboard.writeText(session?.execution_root || inspection.project.project_root)} style={buttonStyle}>复制路径</button>
      <button type="button" onClick={() => void open()} style={buttonStyle}>打开目录</button>
      <button type="button" onClick={() => create(inspection.project.project_id, null)} style={buttonStyle}>新建同项目 Session</button>
      {inspection.project.availability === "missing" && <button type="button" data-testid="project-relocate" onClick={() => void relocate()} style={buttonStyle}>重新定位同一项目</button>}
    </div>
    {error && <div role="alert" style={{ color: tokens.color.danger.fg }}>{error}</div>}
  </div>;

  return <>
    <div className="project-inspector-compact" data-testid="project-inspector-compact" style={barStyle} onClick={() => setExpanded((value) => !value)}>{content}<span>{expanded ? "▴" : "▾"}</span></div>
    <aside className="project-inspector-rail" data-testid="project-inspector-rail" style={railStyle}>
      <div style={barStyle}>{content}</div>
      {projectDetails}
    </aside>
    {expanded && <div className="project-inspector-compact" style={{ ...detailsStyle, top: 42, height: "auto" }}>{projectDetails}</div>}
    {pickerOpen && <ProjectPickerDialog open onClose={() => setPickerOpen(false)} onProjectSelected={(projectId) => create(projectId, activeSid)} />}
  </>;
}

function Field({ label, value }: { label: string; value: string }) { return <div><div style={{ color: dark.textMuted, fontSize: tokens.text.xs.size }}>{label}</div><div data-bp-selectable="" style={{ wordBreak: "break-all" }}>{value}</div></div>; }
const railStyle: React.CSSProperties = { flexDirection: "column", borderLeft: `1px solid ${dark.hairline}`, background: dark.bgSolid, color: dark.text };
const barStyle: React.CSSProperties = { alignItems: "center", gap: tokens.space.sm, padding: `0 ${tokens.space.md}px`, borderBottom: `1px solid ${dark.hairline}`, background: dark.card, color: dark.text };
const detailsStyle: React.CSSProperties = { display: "flex", flexDirection: "column", gap: tokens.space.md, padding: tokens.space.md, background: dark.bgSolid, color: dark.text };
const buttonStyle: React.CSSProperties = { border: `1px solid ${dark.borderStrong}`, borderRadius: tokens.radius.sm, background: dark.card, color: dark.text, cursor: "pointer", padding: `${tokens.space.xs}px ${tokens.space.sm}px` };
