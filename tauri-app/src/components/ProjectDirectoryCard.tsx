import { useState, type CSSProperties } from "react";
import { useDepsChanged } from "../hooks/useDepsChanged";
import { invoke } from "@tauri-apps/api/core";
import type { ProjectDirectoryRequest } from "../types/skillPlatform";
import type { MessageStreamPanelProps } from "./messageStreamTypes";

export function ProjectDirectoryCard({
  request,
  error,
  onConfirm,
  submittedLabel = "项目位置已确认",
}: {
  request: ProjectDirectoryRequest["payload"];
  error?: string | null;
  onConfirm?: MessageStreamPanelProps["onProjectDirectoryConfirm"];
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
    <div
      data-testid="project-directory-card"
      style={{
        alignSelf: "flex-start",
        width: "min(96%, 520px)",
        padding: "10px 12px",
        borderRadius: 10,
        border: "1px solid rgba(125, 211, 252, 0.24)",
        background: "rgba(15, 23, 42, 0.72)",
        color: "#e2e8f0",
        display: "grid",
        gap: 8,
        fontSize: 12,
      }}
    >
      <div style={{ fontWeight: 650 }}>
        {submitted ? submittedLabel : request.title || "选择项目保存位置"}
      </div>
      <div style={{ color: "#94a3b8", lineHeight: 1.45 }}>
        {request.directory_mode === "use_existing"
          ? `${request.project_kind}「${request.project_name}」将继续使用你确认的现有项目文件夹。`
          : `${request.project_kind}「${request.project_name}」将创建在你选择的文件夹下面。`}
      </div>
      {!submitted && (
        <>
          <div style={{ display: "flex", gap: 7 }}>
            {!usesExistingProject && (
              <input
                aria-label="项目文件夹名"
                value={folderName}
                onChange={(event) => setFolderName(event.target.value)}
                style={{
                  minWidth: 0,
                  flex: 1,
                  border: "1px solid rgba(148,163,184,0.24)",
                  borderRadius: 7,
                  background: "rgba(2,6,23,0.55)",
                  color: "#e2e8f0",
                  padding: "6px 8px",
                }}
              />
            )}
            <button
              type="button"
              onClick={() => void chooseParent()}
              disabled={busy}
              style={projectDirectoryButtonStyle}
            >
              {busy ? "选择中…" : parent ? "更换位置" : "选择文件夹"}
            </button>
          </div>
          {finalPath && (
            <div
              title={finalPath}
              style={{
                color: "#bae6fd",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
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
        <div style={{ color: "#86efac" }}>
          {request.directory_mode === "use_existing"
            ? "Agent 已收到位置，正在继续现有项目。"
            : "Agent 已收到位置，正在继续创建项目。"}
        </div>
      )}
      {(error || pickerError) && (
        <div role="alert" style={{ color: "#fca5a5" }}>
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
