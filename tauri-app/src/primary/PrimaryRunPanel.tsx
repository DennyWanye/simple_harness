// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { useEffect, useMemo, useRef, useState } from "react";
import { controlWS } from "../code-panel/controlWs";
import { PermissionPopup } from "../components/PermissionPopup";
import { ProjectDirectoryCard } from "../components/MessageStreamPanel";
import { usePermissionRequests } from "../hooks/usePermissionRequests";
import type { ProjectDirectoryRequest } from "../types/skillPlatform";
import type { PrimaryRun, PrimaryPort } from "./controller";
import { primaryRunChannel } from "./runChannel";
import { record } from "./requests";

export function PrimaryRunPanel({ run, primaryRef, port, onStop, onToolResult, visible = true, refreshVersion = 0 }: { run: PrimaryRun; primaryRef: string; port: PrimaryPort; onStop: () => void; onToolResult?: () => void; visible?: boolean; refreshVersion?: number }) {
  const { execution_session_ref, sdk_run_ref, run_ref, generation } = run;
  const channel = useMemo(() => primaryRunChannel(port, { execution_session_ref, sdk_run_ref, run_ref, generation, primary_ref: primaryRef }, controlWS), [port,execution_session_ref, sdk_run_ref, run_ref, generation, primaryRef]);
  useEffect(() => { channel.start(); return () => channel.dispose(); }, [channel]);
  const [decisionStatus, setDecisionStatus] = useState("");
  const permissions = usePermissionRequests(channel);
  const lastRefresh = useRef(refreshVersion);
  useEffect(() => {
    if (lastRefresh.current === refreshVersion) return;
    lastRefresh.current = refreshVersion;
    channel.send({ type: "permissions_pending_list" });
  }, [channel, refreshVersion]);
  const [directory, setDirectory] = useState<ProjectDirectoryRequest["payload"] | null>(null);
  const [directoryError, setDirectoryError] = useState("");
  const [tools, setTools] = useState<Array<{ id: string; name: string; status: string }>>([]);
  useEffect(() => channel.on_message((raw) => {
    const msg = record(raw), p = record(msg.payload);
    if (msg.type === "primary_decision_status") {
      setDecisionStatus(String(p.error || ({ allowed: "已允许本次操作", denied: "已拒绝本次操作；可停止当前任务", expired: "本次授权已过期，未允许执行" }[String(p.outcome)] ?? (p.state === "waiting" ? (Number(p.pending_count) > 0 ? "等待授权" : "运行等待中，暂无可操作的授权请求。") : ""))));
    }
    if (msg.type === "project_directory_request" && p.decision_id && p.nonce && typeof p.version === "number") {
      setDirectory(p as unknown as ProjectDirectoryRequest["payload"]);
      setDirectoryError("");
    }
    if (msg.type === "project_directory_error" && p.decision_id === directory?.decision_id) {
      setDirectoryError(String(p.error ?? "项目位置未能应用"));
    }
    if (msg.type === "tool_call" || msg.type === "tool_result") {
      if (msg.type === "tool_result") onToolResult?.();
      const id = String(p.call_id ?? "");
      const name = msg.type === "tool_call" ? p.name : p.tool;
      if (!id || typeof name !== "string") return;
      setTools((old) => [...old.filter((tool) => tool.id !== id), {
        id, name,
        status: msg.type === "tool_call" ? "执行中" : p.ok === false ? "失败" : "已返回",
      }].slice(-20));
    }
  }), [channel, directory?.decision_id, onToolResult]);
  return <>
    {decisionStatus && <p role="status">{decisionStatus}</p>}
    {tools.length > 0 && <details><summary>本次工具活动</summary><ul>{tools.map((tool) => <li key={tool.id}>{tool.name} · {tool.status}</li>)}</ul></details>}
    {directory && <ProjectDirectoryCard key={directory.decision_id} request={directory} error={directoryError}
      submittedLabel="已提交位置选择，等待运行确认"
      onConfirm={(parent, folder) => channel.send({ type: "project_directory_response", payload: {
        session_id: directory.session_id, run_id: directory.run_id, request_id: directory.request_id,
        decision_id: directory.decision_id, nonce: directory.nonce, version: directory.version, wait_ref: directory.wait_ref,
        parent_directory: parent, folder_name: folder, directory_mode: directory.directory_mode ?? "create_new",
      } })} />}
    <PermissionPopup portalToBody allowSession={false} request={visible ? permissions.current : null} onResolve={permissions.resolve}
      resolving={permissions.resolving} resolveError={permissions.resolveError} onStopRun={onStop} />
  </>;
}
