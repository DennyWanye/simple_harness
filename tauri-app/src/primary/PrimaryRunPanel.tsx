// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { useEffect, useMemo, useState } from "react";
import { controlWS } from "../code-panel/controlWs";
import { PermissionPopup } from "../components/PermissionPopup";
import { ProjectDirectoryCard } from "../components/MessageStreamPanel";
import { usePermissionRequests } from "../hooks/usePermissionRequests";
import type { ProjectDirectoryRequest } from "../types/skillPlatform";
import type { PrimaryRun } from "./controller";
import { primaryRunChannel } from "./runChannel";
import { record } from "./requests";

export function PrimaryRunPanel({ run, onStop }: { run: PrimaryRun; onStop: () => void }) {
  const { execution_session_ref, sdk_run_ref, run_ref } = run;
  const channel = useMemo(() => primaryRunChannel(controlWS, { execution_session_ref, sdk_run_ref, run_ref }), [execution_session_ref, sdk_run_ref, run_ref]);
  const permissions = usePermissionRequests(channel);
  const [directory, setDirectory] = useState<ProjectDirectoryRequest["payload"] | null>(null);
  const [directoryError, setDirectoryError] = useState("");
  const [tools, setTools] = useState<Array<{ id: string; name: string; status: string }>>([]);
  useEffect(() => channel.on_message((raw) => {
    const msg = record(raw), p = record(msg.payload);
    if (msg.type === "project_directory_request" && p.decision_id && p.nonce && typeof p.version === "number") {
      setDirectory(p as unknown as ProjectDirectoryRequest["payload"]);
      setDirectoryError("");
    }
    if (msg.type === "project_directory_error" && p.decision_id === directory?.decision_id) {
      setDirectoryError(String(p.error ?? "项目位置未能应用"));
    }
    if (msg.type === "tool_call" || msg.type === "tool_result") {
      const id = String(p.call_id ?? "");
      const name = msg.type === "tool_call" ? p.name : p.tool;
      if (!id || typeof name !== "string") return;
      setTools((old) => [...old.filter((tool) => tool.id !== id), {
        id, name,
        status: msg.type === "tool_call" ? "执行中" : p.ok === false ? "失败" : "已返回",
      }].slice(-20));
    }
  }), [channel, directory?.decision_id]);
  return <>
    {tools.length > 0 && <details><summary>本次工具活动</summary><ul>{tools.map((tool) => <li key={tool.id}>{tool.name} · {tool.status}</li>)}</ul></details>}
    {directory && <ProjectDirectoryCard key={directory.decision_id} request={directory} error={directoryError}
      submittedLabel="已提交位置选择，等待运行确认"
      onConfirm={(parent, folder) => channel.send({ type: "project_directory_response", payload: {
        session_id: directory.session_id, run_id: directory.run_id, request_id: directory.request_id,
        decision_id: directory.decision_id, nonce: directory.nonce, version: directory.version, wait_ref: directory.wait_ref,
        parent_directory: parent, folder_name: folder, directory_mode: directory.directory_mode ?? "create_new",
      } })} />}
    <PermissionPopup request={permissions.current} onResolve={permissions.resolve}
      resolving={permissions.resolving} resolveError={permissions.resolveError} onStopRun={onStop} />
  </>;
}
