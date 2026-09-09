// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * ArtifactsView（T12，WB-7）— 产物库视图。
 *
 * D4 数据源：Rust `list_artifacts` command（扫 <user_data>/artifacts，
 * 返回 [{name,path,size,modified_at}]，modified_at 倒序已由 Rust 侧保证）。
 * 无 App props（invoke 自足）。卡片布局对齐 code-panel/ArtifactCard.tsx
 * 的 File 子卡（标题+meta+操作行+状态行），但样式全走 theme tokens
 * （T15：views/ 零硬编码色值）。操作复用 artifact_ops 既有 command：
 * artifact_open / artifact_show_in_folder。
 */
import React, { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import { tokens } from "../theme/tokens";
import {
  INTERACTIVE_CLASS,
  bannerStyle,
  buttonStyle,
  cardStyle,
  dark,
  emptyState,
  metaText,
  titleText,
  viewHeader,
} from "../theme/components";
import { Icon, type IconName } from "../components/Icon";

/** 与 src-tauri/src/artifact_ops.rs 的 ArtifactListEntry 对应。 */
export interface ArtifactListEntry {
  name: string;
  path: string;
  size: number;
  /** Unix 毫秒时间戳（Rust 侧按此倒序）。 */
  modified_at: number;
}

// ─── 展示辅助（对齐 ArtifactCard 的 File 子卡语义） ──────────

/** 文件类型 → 内联线性图标（不引图标库、不用 emoji）。 */
function extIcon(name: string): IconName {
  const ext = name.toLowerCase().split(".").pop() ?? "";
  if (["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"].includes(ext)) return "grid";
  if (["ppt", "pptx", "xls", "xlsx", "csv", "tsv"].includes(ext)) return "layers";
  return "file";
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatModified(ms: number): string {
  if (!ms) return "时间未知";
  try {
    return new Date(ms).toLocaleString();
  } catch {
    return "时间未知";
  }
}

function errorMessage(e: unknown): string {
  if (typeof e === "string") return e;
  if (e instanceof Error) return e.message;
  try {
    return JSON.stringify(e);
  } catch {
    return "未知错误";
  }
}

// ─── 单卡（打开 / 在文件夹中显示 + 状态反馈） ────────────────

type RowActionId = "open" | "show_in_folder";
type RowStatus = "idle" | "pending" | "success" | "error";

interface RowAction {
  id: RowActionId;
  status: RowStatus;
  message: string;
}

const ROW_ACTION_COMMAND: Record<RowActionId, string> = {
  open: "artifact_open",
  show_in_folder: "artifact_show_in_folder",
};

const ROW_ACTION_SUCCESS: Record<RowActionId, string> = {
  open: "已请求系统打开文件",
  show_in_folder: "已在文件夹中定位",
};

function statusColor(status: RowStatus): string {
  if (status === "success") return tokens.color.success.border;
  if (status === "error") return tokens.color.danger.border;
  return tokens.color.accent.border;
}

const ArtifactRow: React.FC<{ entry: ArtifactListEntry }> = ({ entry }) => {
  const [current, setCurrent] = useState<RowAction | null>(null);

  const run = useCallback(
    async (id: RowActionId) => {
      setCurrent({ id, status: "pending", message: "正在处理..." });
      try {
        await invoke(ROW_ACTION_COMMAND[id], { path: entry.path });
        setCurrent({ id, status: "success", message: ROW_ACTION_SUCCESS[id] });
      } catch (e) {
        setCurrent({
          id,
          status: "error",
          message: `操作失败：${errorMessage(e)}`,
        });
      }
    },
    [entry.path],
  );

  const pending = current?.status === "pending";

  return (
    <li
      data-testid="artifact-item"
      style={{ ...cardStyle, listStyle: "none" }}
    >
      <div
        data-testid="artifact-name"
        style={{
          display: "flex",
          alignItems: "center",
          gap: tokens.space.sm,
          fontWeight: tokens.weight.semibold,
          color: dark.text,
          fontSize: tokens.text.md.size,
          marginBottom: tokens.space.xs,
          wordBreak: "break-all",
        }}
      >
        <Icon name={extIcon(entry.name)} size={16} style={{ color: dark.textMuted }} />
        {entry.name}
      </div>
      <div
        style={{
          ...metaText,
          marginBottom: tokens.space.md,
        }}
      >
        {humanSize(entry.size)} · {formatModified(entry.modified_at)}
      </div>
      <div
        style={{
          display: "flex",
          gap: tokens.space.xs + 2,
          flexWrap: "wrap",
        }}
      >
        <button
          type="button"
          data-testid="artifact-action-open"
          disabled={pending}
          aria-busy={pending && current?.id === "open"}
          className={INTERACTIVE_CLASS}
          style={{
            ...buttonStyle("ghost", "sm"),
            cursor: pending ? "wait" : "pointer",
          }}
          onClick={() => void run("open")}
        >
          打开
        </button>
        <button
          type="button"
          data-testid="artifact-action-show_in_folder"
          disabled={pending}
          aria-busy={pending && current?.id === "show_in_folder"}
          className={INTERACTIVE_CLASS}
          style={{
            ...buttonStyle("ghost", "sm"),
            cursor: pending ? "wait" : "pointer",
          }}
          onClick={() => void run("show_in_folder")}
        >
          在文件夹中显示
        </button>
      </div>
      {current ? (
        <div
          data-testid="artifact-action-status"
          role={current.status === "error" ? "alert" : "status"}
          style={{
            ...metaText,
            marginTop: tokens.space.sm,
            color: statusColor(current.status),
            wordBreak: "break-all",
          }}
        >
          {current.message}
        </div>
      ) : null}
    </li>
  );
};

// ─── 视图 ────────────────────────────────────────────────────

export const ArtifactsView: React.FC = () => {
  // null = 加载中（首帧即触发 refresh）。
  const [entries, setEntries] = useState<ArtifactListEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    // 不在 await 前同步 setState（react-hooks/set-state-in-effect）；
    // 成功路径顺带清 error。
    try {
      const list = await invoke<ArtifactListEntry[]>("list_artifacts");
      setEntries(Array.isArray(list) ? list : []);
      setError(null);
    } catch (e) {
      setEntries([]);
      setError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    // fetch-on-mount：refresh 的首个同步语句是 try/await，setState 全部
    // 发生在 await 之后；规则的保守估计在此为误报，定点豁免。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  return (
    <section
      data-testid="view-artifacts"
      aria-label="产物库"
      style={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        color: dark.text,
        fontFamily: tokens.font.ui,
      }}
    >
      <header style={viewHeader}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h1 style={titleText}>产物库</h1>
          <p style={{ ...metaText, margin: `${tokens.space.xs}px 0 0` }}>
            任务生成的文件，按修改时间倒序
            {entries && entries.length > 0 ? ` · 共 ${entries.length} 项` : ""}
          </p>
        </div>
        <button
          type="button"
          data-testid="artifacts-refresh"
          className={INTERACTIVE_CLASS}
          style={buttonStyle("ghost", "md")}
          onClick={() => void refresh()}
        >
          刷新
        </button>
      </header>

      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          padding: `${tokens.space.xl}px ${tokens.space.xl}px`,
        }}
      >
        {error ? (
          <div
            role="alert"
            style={{
              ...bannerStyle("error"),
              marginBottom: tokens.space.md,
            }}
          >
            产物列表加载失败：{error}
          </div>
        ) : null}

        {entries === null ? (
          <div style={emptyState}>
            <span className="bp-spinner" aria-hidden />
            加载中…
          </div>
        ) : entries.length === 0 ? (
          <div data-testid="artifacts-empty" style={emptyState}>
            <Icon name="file" size={28} strokeWidth={1.2} style={{ color: dark.textFaint }} />
            <div
              style={{
                color: dark.text,
                fontSize: tokens.text.md.size,
                fontWeight: tokens.weight.semibold,
              }}
            >
              暂无产物
            </div>
            <p style={{ margin: 0, maxWidth: 420 }}>
              任务生成的文件会出现在这里；消息流中的产物卡片不受影响。
            </p>
          </div>
        ) : (
          <ul
            data-testid="artifacts-list"
            style={{
              display: "grid",
              gap: tokens.space.sm,
              margin: "0 auto",
              padding: 0,
              maxWidth: 760,
            }}
          >
            {entries.map((entry) => (
              <ArtifactRow key={entry.path} entry={entry} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
};

export default ArtifactsView;
