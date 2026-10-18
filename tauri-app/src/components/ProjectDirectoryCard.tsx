// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useState, type CSSProperties } from "react";
import { useDepsChanged } from "../hooks/useDepsChanged";
import { invoke } from "@tauri-apps/api/core";
import type { ProjectDirectoryRequest } from "../types/skillPlatform";
import "./ProjectDirectoryCard.styles.css";

export function ProjectDirectoryCard({
  request,
  error,
  onConfirm,
  submittedLabel = "项目位置已确认",
}: {
  request: ProjectDirectoryRequest["payload"];
  error?: string | null;
  onConfirm?: (parentDirectory: string, folderName: string) => boolean;
  submittedLabel?: string;
}) {
  const [parent, setParent] = useState(request.parent_directory ?? "");
  const [folderName, setFolderName] = useState(request.folder_name);
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [pickerError, setPickerError] = useState<string | null>(null);
  // 出错时解除"已提交"，允许改了再提交。
  if (useDepsChanged([error]) && error) setSubmitted(false);
  const usesExistingProject = request.directory_mode === "use_existing";
  const trimmedParent = parent.trim();
  const displayParent =
    trimmedParent.length > 1 ? trimmedParent.replace(/[\\/]+$/, "") : trimmedParent;
  const pathSeparator = trimmedParent.includes("\\") && !trimmedParent.includes("/") ? "\\" : "/";
  const finalPath = parent
    ? usesExistingProject
      ? displayParent
      : displayParent === "/"
        ? `/${folderName.trim()}`
        : `${displayParent}${pathSeparator}${folderName.trim()}`
    : "";

  const chooseParent = async () => {
    if (busy || submitted) return;
    setBusy(true);
    setPickerError(null);
    try {
      const selected = await invoke<string | null>("open_directory_dialog");
      if (selected?.trim()) setParent(selected.trim());
    } catch {
      setPickerError("无法打开文件夹选择器，请重试。");
    } finally {
      setBusy(false);
    }
  };

  const confirm = () => {
    if (!parent || (!usesExistingProject && !folderName.trim()) || !onConfirm) return;
    if (onConfirm(parent, folderName.trim())) setSubmitted(true);
  };

  return (
    <div data-testid="project-directory-card" className="sh-ProjectDirectoryCard-1">
      <div className="sh-ProjectDirectoryCard-2">
        {submitted ? submittedLabel : request.title || "选择项目保存位置"}
      </div>
      <div className="sh-ProjectDirectoryCard-3">
        {request.directory_mode === "use_existing"
          ? `${request.project_kind}「${request.project_name}」将继续使用你确认的现有项目文件夹。`
          : `${request.project_kind}「${request.project_name}」将创建在你选择的文件夹下面。`}
      </div>
      {!submitted && (
        <>
          <div className="sh-ProjectDirectoryCard-4">
            {!usesExistingProject && (
              <input
                aria-label="项目文件夹名"
                value={folderName}
                onChange={(event) => setFolderName(event.target.value)}
                className="sh-ProjectDirectoryCard-5"
              />
            )}
            <button
              type="button"
              onClick={() => void chooseParent()}
              disabled={busy}
              className="sh-ProjectDirectoryCard-6"
            >
              {busy ? "选择中…" : parent ? "更换位置" : "选择文件夹"}
            </button>
          </div>
          {finalPath && (
            <div title={finalPath} className="sh-ProjectDirectoryCard-7">
              {request.directory_mode === "use_existing"
                ? `将使用：${finalPath}`
                : `将创建到：${finalPath}`}
            </div>
          )}
          <button
            type="button"
            disabled={!parent || (!usesExistingProject && !folderName.trim())}
            onClick={confirm}
            style={{
              ...projectDirectoryButtonStyle,
              justifySelf: "start",
              opacity: !parent || (!usesExistingProject && !folderName.trim()) ? 0.45 : 1,
            }}
          >
            {request.directory_mode === "use_existing" ? "使用此文件夹" : "在这里创建"}
          </button>
        </>
      )}
      {submitted && (
        <div className="sh-ProjectDirectoryCard-8">
          {request.directory_mode === "use_existing"
            ? "Agent 已收到位置，正在继续现有项目。"
            : "Agent 已收到位置，正在继续创建项目。"}
        </div>
      )}
      {(error || pickerError) && (
        <div role="alert" className="sh-ProjectDirectoryCard-9">
          {error || pickerError}
        </div>
      )}
    </div>
  );
}

const projectDirectoryButtonStyle: CSSProperties = {
  border: "1px solid rgba(125,211,252,0.3)",
  borderRadius: 7,
  background: "rgba(14,116,144,0.22)",
  color: "#bae6fd",
  padding: "6px 9px",
  cursor: "pointer",
};
