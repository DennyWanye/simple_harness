// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * message bubble + tool-call card renderer.
 *
 * Three flavours:
 *   1. user / assistant text  → markdown via react-markdown
 *   2. tool_call               → collapsible card (tool name, args)
 *   3. tool_result             → expandable result, syntax-highlighted
 *   4. error                   → red banner
 */
import { useMemo, useState, type ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { Message } from "../stores/sessionTypes";
import { useSessionsStore } from "../stores/sessionsStore";
import { CopyMessageButton } from "../components/CopyMessageButton";
import { MarkdownMessage } from "../components/MarkdownMessage";
import { ArtifactCard } from "./ArtifactCard";
import { extractArtifactsFromResult } from "./artifactExtract";
import { controlWS } from "./controlWs";
import { PPTOutlineCard } from "./PPTOutlineCard";
import { splitThinkBlocks, splitToolError } from "./messageSplit";
import "./MessageBubble.styles.css";

const LOCAL_FILE_EXTENSIONS = /\.(pptx?|pptm|xlsx?|docx?|pdf|png|jpe?g|gif|md|txt|csv)$/i;
const isLocalFileLink = (href?: string) => {
  if (!href) return false;
  if (/^https?:\/\//i.test(href)) return false;

  const normalizedHref = href.replace(/\\/g, "/");
  const hrefWithoutQuery = normalizedHref.split(/[?#]/)[0];

  return (
    /^file:\/\//i.test(href) ||
    /^[a-zA-Z]:[\\/]/.test(href) ||
    LOCAL_FILE_EXTENSIONS.test(hrefWithoutQuery)
  );
};
const localFilePathFromHref = (href: string) => {
  const withoutScheme = href.replace(/^file:\/\//i, "");
  const withoutLeadingWindowsSlash = withoutScheme.replace(/^\/([a-zA-Z]:[\\/])/, "$1");

  try {
    return decodeURIComponent(withoutLeadingWindowsSlash);
  } catch {
    return withoutLeadingWindowsSlash;
  }
};
const openLocalFileLink = (href: string) => {
  invoke("artifact_open", { path: localFilePathFromHref(href) }).catch((error) => {
    console.warn("Failed to open local markdown link", error);
  });
};
declare global {
  interface Window {
    __deskpetLocalMarkdownLinkHandlerInstalled?: boolean;
  }
}
if (typeof window !== "undefined" && !window.__deskpetLocalMarkdownLinkHandlerInstalled) {
  window.__deskpetLocalMarkdownLinkHandlerInstalled = true;

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;

    const anchor = event.target.closest<HTMLAnchorElement>("a[href]");
    const href = anchor?.getAttribute("href");
    if (!href || !isLocalFileLink(href)) return;

    event.preventDefault();
    openLocalFileLink(href);
  });

  document.addEventListener("mouseover", (event) => {
    if (!(event.target instanceof Element)) return;

    const anchor = event.target.closest<HTMLAnchorElement>("a[href]");
    const href = anchor?.getAttribute("href");
    if (!anchor || !href || !isLocalFileLink(href) || anchor.title) return;

    anchor.title = "用系统默认程序打开";
  });
}
interface Props {
  msg: Message;
}
export function MessageBubble({ msg }: Props) {
  switch (msg.role) {
    case "user":
      return <UserBubble text={msg.text ?? ""} />;
    case "assistant":
      // render with <think>...</think> stripped → collapsed
      // reasoning bubble + the rest as normal markdown. Some thinking-
      // mode models (deepseek-v4-pro) leak chain-of-thought into the
      // visible content stream as `<think>...</think>` instead of the
      // reasoning_content field. Without this split, the user sees the
      // raw think tag and (worse) a hanging streaming cursor when the
      // tag is unclosed.
      return <AssistantBubbleWithThink text={msg.text ?? ""} streaming={false} />;
    case "assistant_delta":
      // streaming partial bubble. Same render as final but
      // with a soft pulsing cursor — UNLESS the only content so far is
      // a still-open <think>, in which case we let AssistantBubbleWithThink
      // keep the reasoning bubble streaming (no main-bubble cursor).
      return <AssistantBubbleWithThink text={msg.text ?? ""} streaming={true} />;
    case "reasoning_delta":
      // thinking-mode chain-of-thought, rendered faded so
      // it's clear it's not the final answer.
      return <ReasoningBubble text={msg.text ?? ""} />;
    case "plan":
      return (
        <PlanCard
          rationale={msg.plan_rationale ?? ""}
          steps={msg.plan_steps ?? []}
          targetDirectory={msg.plan_target_directory}
          actionCategories={msg.plan_action_categories ?? []}
          autoConfirmed={!!msg.plan_auto_confirmed}
          awaiting={!!msg.plan_awaiting_confirm}
          msgId={msg.id}
          planSid={msg.plan_sid}
          runId={msg.plan_run_id}
          decisionId={msg.plan_decision_id}
          nonce={msg.plan_nonce}
          version={msg.plan_version}
        />
      );
    case "ppt_outline":
      return (
        <div className="sh-MessageBubble-1">
          <PPTOutlineCard
            outlineId={msg.outline_id ?? ""}
            topic={msg.topic ?? ""}
            outlineMd={msg.outline_md ?? ""}
            sourcesCount={msg.sources_count ?? 0}
            noResearch={!!msg.no_research}
            history={msg.history ?? []}
            awaiting={!!msg.ppt_outline_awaiting}
            decisionStatus={msg.ppt_outline_decision_status}
            sessionId={msg.outline_id ? findMessageSession(msg.outline_id) : undefined}
            runId={msg.ppt_run_id}
            decisionId={msg.ppt_decision_id}
            nonce={msg.ppt_nonce}
            version={msg.ppt_version}
          />
          <CopyMessageButton
            text={[msg.topic, msg.outline_md].filter(Boolean).join("\n\n")}
            tone="dark"
          />
        </div>
      );
    case "tool_call":
      return <ToolCallCard name={msg.tool_name ?? "(unknown)"} args={msg.tool_args ?? {}} />;
    case "tool_result":
      return (
        <ToolResultCard
          name={msg.tool_name ?? "(unknown)"}
          ok={msg.tool_ok ?? true}
          result={msg.tool_result ?? ""}
        />
      );
    case "slash_result":
      // /help /goal /prefs /skill 结果（ws.ts 已格式化成纯文本）。
      return <SlashResultBubble text={msg.text ?? ""} />;
    case "error":
      return <ErrorBanner text={msg.text ?? "(unknown error)"} />;
    default:
      return null;
  }
}
function findMessageSession(outlineId: string) {
  const sessions = useSessionsStore.getState().sessions;
  for (const [sid, session] of Object.entries(sessions)) {
    if (session.messages.some((m) => m.role === "ppt_outline" && m.outline_id === outlineId)) {
      return sid;
    }
  }
  return undefined;
}
function UserBubble({ text }: { text: string }) {
  return (
    <div className="sh-MessageBubble-2">
      <div data-bp-selectable="" className="sh-MessageBubble-3">
        {text}
      </div>
      <CopyMessageButton text={text} align="right" tone="blue" />
    </div>
  );
}
function AssistantBubbleWithThink({ text, streaming }: { text: string; streaming: boolean }) {
  const segments = splitThinkBlocks(text);
  const hasNormal = segments.some((s) => s.kind === "normal" && s.text.trim());
  const hasOpenThink = segments.some((s) => s.kind === "think" && !s.closed);
  // Cursor decision: only show in the main bubble if we have visible
  // content actively streaming. If the only thing streaming right now
  // is an open <think> tag, the cursor lives in that reasoning bubble
  // — not the main one. Prevents the "blank bubble + ghost cursor"
  // ambiguity that made earlier turns look stuck.
  const showMainCursor = streaming && hasNormal && !hasOpenThink;

  return (
    <>
      {segments.map((seg, idx) => {
        if (seg.kind === "think") {
          // Open think during streaming → animate; closed/finalized → static.
          const suffix = !seg.closed && streaming ? " ▍" : "";
          return <ReasoningBubble key={idx} text={seg.text + suffix} />;
        }
        return (
          <AssistantBubble
            key={idx}
            text={seg.text + (showMainCursor && idx === segments.length - 1 ? " ▍" : "")}
          />
        );
      })}
    </>
  );
}
function AssistantBubble({ text }: { text: string }) {
  return (
    <div className="sh-MessageBubble-4">
      <div data-bp-selectable="" className="sh-MessageBubble-5">
        <MarkdownMessage>{text}</MarkdownMessage>
      </div>
      <CopyMessageButton text={text} tone="dark" />
    </div>
  );
}
export function ToolCallCard({ name, args }: { name: string; args: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const arg_summary = Object.entries(args)
    .filter(([k]) => !k.startsWith("_")) // hide injected _project_root etc.
    .map(([k, v]) => {
      const s = typeof v === "string" ? v : JSON.stringify(v);
      const trimmed = s.length > 60 ? s.slice(0, 57) + "…" : s;
      return `${k}=${trimmed}`;
    })
    .join("  ");
  return (
    <ToolCard
      header={
        <>
          <span className="sh-MessageBubble-6">▶ {name}</span>
          {arg_summary && <span className="sh-MessageBubble-7">{arg_summary}</span>}
        </>
      }
      open={open}
      onToggle={() => setOpen((v) => !v)}
    >
      <pre data-bp-selectable="" className="sh-MessageBubble-8">
        {JSON.stringify(args, null, 2)}
      </pre>
    </ToolCard>
  );
}
export function ToolResultCard({
  name,
  ok,
  result,
}: {
  name: string;
  ok: boolean;
  result: string;
}) {
  // 优先走 ArtifactCard 分发（PRD §3 D2）。
  // 仅在 ok=true 且 result 含 artifacts[] 时启用 — 失败仍走旧路径
  // 显示错误细节。`artifacts` 字段不在 → 字节级回落旧渲染（TG-5 T5-5）。
  const artifacts = useMemo(() => (ok ? extractArtifactsFromResult(result) : []), [ok, result]);
  // Raw tool results are useful evidence, but noisy. Keep them hidden until
  // the user explicitly asks to inspect the result. 声明在分支前：同一张卡可能在
  // 产物卡与普通结果之间切换，hooks 数量必须不变。
  const [open, setOpen] = useState(false);
  if (ok && artifacts.length > 0) {
    return (
      <ToolCard
        header={
          <>
            <span className="sh-MessageBubble-9">✓ ok</span>
            <span className="sh-MessageBubble-10">← {name}</span>
            <span className="sh-MessageBubble-11">{artifacts.length} 个产物</span>
          </>
        }
        open={true}
        onToggle={() => {
          /* artifact 卡片本身可折叠 */
        }}
      >
        <div data-testid="artifact-card-list" className="sh-MessageBubble-12">
          {artifacts.map((a, i) => (
            <ArtifactCard key={i} artifact={a} toolName={name} />
          ))}
        </div>
      </ToolCard>
    );
  }
  // 走 splitToolError 提取 hint；保留原 pretty-print fallback。
  const { body: display, hint } = splitToolError(result);
  const lineCount = display.split("\n").length;
  const status = ok ? "✓ ok" : "✗ failed";
  const statusColor = ok ? "#86efac" : "#fca5a5";
  // 有 hint = 后端给了"修这个错误"的可读建议，整张卡加金黄色描边吸引注意。
  const hasHint = !!hint;
  return (
    <ToolCard
      header={
        <>
          <span style={{ color: statusColor }}>{status}</span>
          <span className="sh-MessageBubble-10">← {name}</span>
          {!open && <span className="sh-MessageBubble-11">{lineCount} 行 — 点击展开</span>}
          {hasHint && <span className="sh-MessageBubble-13">💡 有修复建议</span>}
        </>
      }
      open={open}
      onToggle={() => setOpen((v) => !v)}
      borderColor={hasHint ? "#f59e0b" : undefined}
    >
      {hasHint && (
        <div data-bp-selectable="" data-testid="tool-result-hint" className="sh-MessageBubble-14">
          💡 {hint}
        </div>
      )}
      <pre data-bp-selectable="" className="sh-MessageBubble-8">
        {display}
      </pre>
    </ToolCard>
  );
}
const ACTION_CATEGORY_LABELS: Record<string, string> = {
  filesystem_read: "读取文件",
  filesystem_write: "写入文件",
  shell: "执行命令",
  process: "管理进程",
  application: "启动/控制应用",
  network: "访问网络",
  desktop: "桌面操作",
  capability_manage: "安装/管理能力",
};
export function PlanCard({
  rationale,
  steps,
  targetDirectory,
  actionCategories,
  autoConfirmed = false,
  awaiting = false,
  msgId,
  planSid,
  runId,
  decisionId,
  nonce,
  version,
}: {
  rationale: string;
  steps: { title: string; detail: string }[];
  targetDirectory?: string;
  actionCategories: string[];
  autoConfirmed?: boolean;
  // superpowers 决策2 plan-confirm 硬门
  awaiting?: boolean;
  msgId?: string;
  planSid?: string;
  runId?: string;
  decisionId?: string;
  nonce?: string;
  version?: number;
}) {
  const resolve_plan = useSessionsStore((s) => s.resolve_plan);
  const decide = (decision: "go" | "cancel") => {
    if (planSid) {
      controlWS.send({
        type: "plan_confirm",
        payload: {
          session_id: planSid,
          run_id: runId,
          decision_id: decisionId,
          nonce,
          version,
          decision,
        },
      });
      resolve_plan(planSid, msgId);
    }
  };
  return (
    <div data-bp-selectable="" className="sh-MessageBubble-15">
      <div className="sh-MessageBubble-16">📋 计划 ({steps.length} 步)</div>
      <div data-testid="plan-authorization-state" className="sh-MessageBubble-17">
        {autoConfirmed
          ? "Auto · 自动执行"
          : awaiting
            ? "Manual · 等待一次授权"
            : "Manual · 无需额外授权"}
      </div>
      {targetDirectory && (
        <div data-testid="plan-target-directory" className="sh-MessageBubble-18">
          目标目录：{targetDirectory}
        </div>
      )}
      {actionCategories.length > 0 && (
        <div data-testid="plan-action-categories" className="sh-MessageBubble-19">
          主要动作：
          {actionCategories.map((item) => ACTION_CATEGORY_LABELS[item] ?? item).join("、")}
        </div>
      )}
      {rationale && <div className="sh-MessageBubble-20">{rationale}</div>}
      <ol className="sh-MessageBubble-21">
        {steps.map((s, i) => (
          <li key={i} className="sh-MessageBubble-22">
            <strong className="sh-MessageBubble-23">{s.title}</strong>
            {s.detail && <span className="sh-MessageBubble-24"> — {s.detail}</span>}
          </li>
        ))}
      </ol>
      {awaiting && (
        <div data-testid="plan-confirm-bar" className="sh-MessageBubble-25">
          <button
            type="button"
            data-testid="plan-confirm-go"
            onClick={() => decide("go")}
            className="sh-MessageBubble-26"
          >
            ▶ 执行
          </button>
          <button
            type="button"
            data-testid="plan-confirm-cancel"
            onClick={() => decide("cancel")}
            className="sh-MessageBubble-27"
          >
            取消
          </button>
        </div>
      )}
      <CopyMessageButton
        text={[
          rationale,
          targetDirectory ? `目标目录：${targetDirectory}` : "",
          actionCategories.length
            ? `主要动作：${actionCategories.map((item) => ACTION_CATEGORY_LABELS[item] ?? item).join("、")}`
            : "",
          ...steps.map((s, i) => `${i + 1}. ${s.title}${s.detail ? ` — ${s.detail}` : ""}`),
        ]
          .filter(Boolean)
          .join("\n")}
        tone="dark"
      />
    </div>
  );
}
function ReasoningBubble({ text }: { text: string }) {
  return (
    <div className="sh-MessageBubble-28">
      <div data-bp-selectable="" className="sh-MessageBubble-29">
        💭 {text}
      </div>
      <CopyMessageButton text={text} tone="dark" />
    </div>
  );
}
function SlashResultBubble({ text }: { text: string }) {
  return (
    <div className="sh-MessageBubble-4">
      <div data-bp-selectable="" data-testid="slash-result-bubble" className="sh-MessageBubble-30">
        {text}
      </div>
      <CopyMessageButton text={text} tone="dark" />
    </div>
  );
}
function ErrorBanner({ text }: { text: string }) {
  return (
    <div className="sh-MessageBubble-1">
      <div data-bp-selectable="" className="sh-MessageBubble-31">
        ⚠ {text}
      </div>
      <CopyMessageButton text={text} tone="dark" />
    </div>
  );
}
// ---- shared tool-card chrome ------------------------------------------

function ToolCard({
  header,
  open,
  onToggle,
  children,
  borderColor,
}: {
  header: ReactNode;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
  /** optional emphasis border (e.g. golden when hint present). */
  borderColor?: string;
}) {
  const accent = borderColor ?? "rgba(148, 163, 184, 0.22)";
  return (
    <div
      style={{
        margin: "8px 0",
        // 整张卡加同色 outline，让 hint 高亮在折叠/展开两种状态下都能被看见
        ...(borderColor
          ? { outline: `1px solid ${borderColor}`, outlineOffset: 0, borderRadius: 6 }
          : {}),
      }}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        style={{
          width: "100%",
          textAlign: "left",
          padding: "5px 10px",
          background: "rgba(30, 41, 59, 0.6)",
          border: `1px solid ${accent}`,
          color: "#e2e8f0",
          fontSize: 12,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
          borderRadius: open ? "6px 6px 0 0" : 6,
          cursor: "pointer",
        }}
      >
        {header}
      </button>
      {open && children}
    </div>
  );
}
