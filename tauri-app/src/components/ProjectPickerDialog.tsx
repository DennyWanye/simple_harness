// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import { controlWS } from "../code-panel/controlWs";
import {
  projectRequest,
  responsePayload,
  type ProjectPreviewResponse,
  type ProjectRegisterResponse,
} from "../chat/projectSessionProtocol";
import type { ProjectRegistrationPreview } from "../types/projectSessions";
import "./ProjectPickerDialog.styles.css";

export interface ProjectPickerDialogProps {
  open: boolean;
  onClose: () => void;
  onProjectSelected: (projectId: string) => void;
  onDefaultSelected?: () => void;
}

export function ProjectPickerDialog({
  open,
  onClose,
  onProjectSelected,
  onDefaultSelected,
}: ProjectPickerDialogProps) {
  const [selectedPath, setSelectedPath] = useState("");
  const [mode, setMode] = useState<"git_root" | "selected_folder">("git_root");
  const [preview, setPreview] = useState<ProjectRegistrationPreview | null>(null);
  const [pending, setPending] = useState<{
    kind: "preview" | "register";
    requestId: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(
    () =>
      controlWS.on_message((message: unknown) => {
        if (!pending) return;
        if (pending.kind === "preview") {
          const response = responsePayload<ProjectPreviewResponse>(
            message,
            "project_preview_register_response",
          );
          if (!response || response.request_id !== pending.requestId) return;
          setPending(null);
          if (!response.payload.ok) return setError(response.payload.error.message);
          setPreview(response.payload.preview);
          return;
        }
        const response = responsePayload<ProjectRegisterResponse>(
          message,
          "project_register_response",
        );
        if (!response || response.request_id !== pending.requestId) return;
        setPending(null);
        if (!response.payload.ok) return setError(response.payload.error.message);
        onProjectSelected(response.payload.project.project_id);
      }),
    [onProjectSelected, pending],
  );

  const requestPreview = (path: string, nextMode: "git_root" | "selected_folder") => {
    const request = projectRequest("project_preview_register", {
      selected_path: path,
      mode: nextMode,
    });
    setPending({ kind: "preview", requestId: request.request_id });
    setError(null);
    controlWS.send(request);
  };

  const chooseFolder = async () => {
    try {
      const path = await invoke<string | null>("open_directory_dialog");
      if (!path) return;
      const nextMode = onDefaultSelected ? "selected_folder" : "git_root";
      setSelectedPath(path);
      setMode(nextMode);
      setPreview(null);
      requestPreview(path, nextMode);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const setRegistrationMode = (nextMode: "git_root" | "selected_folder") => {
    if (!selectedPath || nextMode === mode) return;
    setMode(nextMode);
    setPreview(null);
    requestPreview(selectedPath, nextMode);
  };

  const register = () => {
    if (!preview || pending) return;
    const request = projectRequest("project_register", {
      selected_path: selectedPath,
      mode,
      display_name: preview.display_name,
    });
    setPending({ kind: "register", requestId: request.request_id });
    setError(null);
    controlWS.send(request);
  };

  const detectedDifferent = useMemo(
    () =>
      Boolean(preview?.detected_git_root && preview.detected_git_root !== preview.selected_path),
    [preview],
  );
  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="选择项目"
      data-testid="project-picker-dialog"
      className="sh-ProjectPickerDialog-1"
    >
      <div className="sh-ProjectPickerDialog-2">
        <h2 className="sh-ProjectPickerDialog-3">选择项目文件夹</h2>
        <p className="sh-ProjectPickerDialog-4">项目只在新 Session 创建时绑定，之后不能改绑。</p>
        <button
          type="button"
          data-testid="project-picker-choose"
          onClick={() => void chooseFolder()}
          className="sh-ProjectPickerDialog-5"
        >
          选择文件夹…
        </button>
        {preview && (
          <div data-testid="project-picker-preview" className="sh-ProjectPickerDialog-6">
            <PathLine label="所选目录" value={preview.selected_path} />
            <PathLine label="检测到的 Git 根" value={preview.detected_git_root ?? "未检测到"} />
            <PathLine label="最终绑定路径" value={preview.canonical_root} />
            {detectedDifferent && (
              <label className="sh-ProjectPickerDialog-7">
                <input
                  type="checkbox"
                  data-testid="project-picker-explicit-child"
                  checked={mode === "selected_folder"}
                  onChange={(event) =>
                    setRegistrationMode(event.target.checked ? "selected_folder" : "git_root")
                  }
                />
                将所选子目录注册为独立项目
              </label>
            )}
          </div>
        )}
        {pending?.kind === "preview" && <div role="status">正在识别项目…</div>}
        {error && (
          <div role="alert" className="sh-ProjectPickerDialog-8">
            {error}
          </div>
        )}
        <div className="sh-ProjectPickerDialog-9">
          <button type="button" onClick={onClose} className="sh-ProjectPickerDialog-5">
            取消
          </button>
          {onDefaultSelected && (
            <button
              type="button"
              data-testid="project-picker-default"
              onClick={onDefaultSelected}
              disabled={Boolean(pending)}
              className="sh-ProjectPickerDialog-5"
            >
              使用默认目录
            </button>
          )}
          <button
            type="button"
            data-testid="project-picker-confirm"
            onClick={register}
            disabled={!preview || Boolean(pending)}
            className="sh-ProjectPickerDialog-5"
          >
            {pending?.kind === "register"
              ? "创建中…"
              : onDefaultSelected
                ? "创建普通 Session"
                : "创建项目 Session"}
          </button>
        </div>
      </div>
    </div>
  );
}

function PathLine({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <strong>{label}</strong>
      <div data-bp-selectable="" className="sh-ProjectPickerDialog-10">
        {value}
      </div>
    </div>
  );
}
