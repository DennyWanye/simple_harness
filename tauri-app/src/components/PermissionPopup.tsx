// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S20 Wave 1c — PermissionPopup (UI-revamp)
 *
 * 权限请求模态框。三个按钮：拒绝 / 本会话始终允许 / 允许一次。ESC 也是拒绝。
 *
 * 重构要点：
 * - 全部样式走 design tokens (theme/components)
 * - 按类别提供图标 + 配色，敏感类红、写入黄、读取灰
 * - 入场动画 (bp-pop-in)
 * - hover/focus 状态完整
 * - 主按钮回车确认（聚焦在"允许一次"）
 *
 * 规格：openspec/specs/permission-gate/spec.md
 */
import React, { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

import type { PermissionRequest, PermissionCategory } from "../types/skillPlatform";
import { bannerStyle, buttonStyle, surfaceModal } from "../theme/components";
import { tokens } from "../theme/tokens";
import "./PermissionPopup.styles.css";

type Decision = "allow" | "allow_session" | "deny";

interface Props {
  request: PermissionRequest["payload"] | null;
  onResolve: (decision: Decision) => void;
  onStopRun?: () => void;
  allowSession?: boolean;
  resolving?: boolean;
  resolveError?: string | null;
  /** Escape scroll/containing blocks; the owner must suppress hidden-view requests. */
  portalToBody?: boolean;
}

interface CategoryMeta {
  label: string;
  icon: string;
  level: "info" | "warning" | "error";
  hint: string;
}

const CATEGORY_META: Record<PermissionCategory, CategoryMeta> = {
  read_file: {
    label: "读取文件",
    icon: "📖",
    level: "info",
    hint: "读取你电脑上的一个文件。",
  },
  read_file_sensitive: {
    label: "读取敏感文件",
    icon: "🔒",
    level: "error",
    hint: "尝试读取看起来包含密钥/凭证的文件，请确认是你的本意。",
  },
  write_file: {
    label: "写入文件",
    icon: "✏️",
    level: "warning",
    hint: "在指定路径创建或修改文件。",
  },
  desktop_write: {
    label: "写入桌面",
    icon: "🗂️",
    level: "warning",
    hint: "在你的桌面创建一个文件。",
  },
  shell: {
    label: "执行命令",
    icon: "⚡",
    level: "error",
    hint: "运行 shell 命令 — 请仔细看清楚命令内容再决定。",
  },
  network: {
    label: "网络请求",
    icon: "🌐",
    level: "warning",
    hint: "向某个 URL 发起 HTTP 请求。",
  },
  mcp_call: {
    label: "MCP 调用",
    icon: "🔌",
    level: "warning",
    hint: "调用某个 MCP server 提供的工具。",
  },
  skill_install: {
    label: "安装技能",
    icon: "📦",
    level: "error",
    hint: "从 GitHub 安装第三方技能 — 请确认来源可信。",
  },
};

const DEFAULT_META: CategoryMeta = {
  label: "操作请求",
  icon: "⚙",
  level: "warning",
  hint: "请确认这次操作。",
};

function permissionMeta(request: PermissionRequest["payload"]): CategoryMeta {
  const toolName = String(request.params?.tool_name || "").trim();
  if (toolName === "workflow_spawn") {
    return {
      label: "启动子任务",
      icon: "🧩",
      level: "warning",
      hint: "启动一个受约束的子任务；请确认目标、工作目录和输出要求。",
    };
  }
  return CATEGORY_META[request.category as PermissionCategory] ?? DEFAULT_META;
}

export const PermissionPopup: React.FC<Props> = ({
  request,
  onResolve,
  allowSession = true,
  onStopRun,
  resolving = false,
  resolveError = null,
  portalToBody = false,
}) => {
  const allowOnceRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!request) return;
    // 自动聚焦"允许一次"，回车确认
    allowOnceRef.current?.focus({ preventScroll: true });
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !resolving) {
        e.preventDefault();
        onResolve("deny");
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [request, onResolve, resolving]);

  if (!request) return null;
  const meta = permissionMeta(request);

  // accent border color by level
  const accent =
    meta.level === "error"
      ? tokens.color.danger.bg
      : meta.level === "warning"
        ? tokens.color.warning.bg
        : tokens.color.info.bg;

  const popup = (
    <div
      className="sh-PermissionPopup-1"
      role="dialog"
      aria-modal="true"
      aria-labelledby="permission-popup-title"
    >
      <div
        style={{
          ...surfaceModal,
          width: 460,
          maxWidth: "92vw",
          maxHeight: "92vh",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          animation: `bp-pop-in ${tokens.duration.base}ms ${tokens.easing.out}`,
          borderTop: `4px solid ${accent}`,
        }}
      >
        {/* Header */}
        <div className="sh-PermissionPopup-2">
          <span
            aria-hidden
            style={{
              fontSize: 22,
              lineHeight: 1,
              filter:
                meta.level === "error"
                  ? `drop-shadow(0 0 6px ${tokens.color.danger.bg}55)`
                  : "none",
            }}
          >
            {meta.icon}
          </span>
          <div className="sh-PermissionPopup-3">
            <div className="sh-PermissionPopup-4">权限请求</div>
            <h3 id="permission-popup-title" className="sh-PermissionPopup-5">
              {meta.label}
            </h3>
          </div>
        </div>

        {/* Summary + hint */}
        <div className="sh-PermissionPopup-6">
          <div className="sh-PermissionPopup-7">{request.summary}</div>
          <div
            style={{
              ...bannerStyle(meta.level),
              fontSize: tokens.text.sm.size,
            }}
            role="status"
          >
            {meta.hint}
          </div>
        </div>

        {/* Params details (collapsible) */}
        {Object.keys(request.params || {}).length > 0 && (
          <div className="sh-PermissionPopup-8">
            <details>
              <summary className="sh-PermissionPopup-9">查看详细参数</summary>
              <pre className="sh-PermissionPopup-10">{JSON.stringify(request.params, null, 2)}</pre>
            </details>
          </div>
        )}

        {/* Footer / actions */}
        <div className="sh-PermissionPopup-11">
          {request.run_id && onStopRun && (
            <button
              type="button"
              disabled={resolving}
              className="bp-btn-secondary sh-PermissionPopup-12"
              onClick={onStopRun}
            >
              停止当前任务
            </button>
          )}
          <button
            type="button"
            disabled={resolving}
            className="bp-btn-secondary sh-PermissionPopup-13"
            onClick={() => onResolve("deny")}
          >
            拒绝<span className="sh-PermissionPopup-14">Esc</span>
          </button>
          {allowSession && (
            <button
              type="button"
              disabled={resolving}
              className="bp-btn-secondary sh-PermissionPopup-13"
              onClick={() => onResolve("allow_session")}
              title="本会话内同类操作不再询问"
            >
              本会话始终允许
            </button>
          )}
          <button
            ref={allowOnceRef}
            type="button"
            disabled={resolving}
            className={meta.level === "error" ? "bp-btn-danger" : "bp-btn-primary"}
            onClick={() => onResolve("allow")}
            style={buttonStyle(meta.level === "error" ? "danger" : "primary", "md")}
          >
            {resolving ? "提交中…" : "允许一次"}
          </button>
        </div>
        {resolveError && (
          <div role="alert" className="sh-PermissionPopup-15">
            {resolveError}
          </div>
        )}
      </div>
    </div>
  );
  return portalToBody ? createPortal(popup, document.body) : popup;
};

export default PermissionPopup;
