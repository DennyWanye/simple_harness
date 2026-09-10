// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 主对话视图（2026-09-09「克制的高级感」改版）。
 *
 * 版式：顶栏（标题 20/600 + 幽灵按钮）→ 单行 meta 状态 →
 * 消息区（最大宽 760 居中；用户右对齐强调色极淡填充 + 细描边，助手左
 * 对齐带小头像点，工具记录为可折叠等宽灰条）→ 悬浮输入卡片。
 * 记忆面板改为右侧抽屉（宽 420 + 遮罩淡入 160ms）。
 *
 * 无障碍名称锁（自动化脚本依赖，不得改动）：按钮「记忆 / 模型与设置 /
 * 刷新状态 / 查看更早消息」，状态文本「空闲 · 排队 N」「等待主对话就绪」，
 * 工具折叠条「▸ 工具 · 名称」，`data-testid="primary-message-<role>"`。
 */
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { ControlChannel } from "../ws/ControlChannel";
import { InputBar } from "../code-panel/InputBar";
import { MarkdownMessage } from "../components/MarkdownMessage";
import { Icon } from "../components/Icon";
import {
  INTERACTIVE_CLASS,
  MESSAGE_MAX_WIDTH,
  buttonStyle,
  dark,
  emptyState,
  metaText,
  textLinkStyle,
  titleText,
  viewHeader,
} from "../theme/components";
import { tokens } from "../theme/tokens";
import { boundPrimaryPort } from "../primary/boundPort";
import { PrimaryController, type PrimaryMessage, type PrimaryPort } from "../primary/controller";
import { PrimaryRunPanel } from "../primary/PrimaryRunPanel";
import { PrimaryWorkspaceBindings } from "../primary/PrimaryWorkspaceBindings";
import { PrimaryBindingRecovery } from "../primary/bindingRecovery";

const disconnected: PrimaryPort = {
  state: () => "disconnected", send_command: () => false,
  on_message: () => () => {}, on_state_change: () => () => {},
};
export interface PrimaryChatViewProps {
  channel?: ControlChannel | null;
  onOpenSettings?: () => void;
  active?: boolean;
}
const runLabels: Record<string, string> = {
  CLAIMED: "已领取，等待执行进展", RUNNING: "执行中", WAITING: "等待处理", PAUSED: "已暂停",
  SUCCEEDED: "执行已结束", FAILED: "执行失败", CANCELLED: "已取消", STOPPED: "已停止",
};

/** 消息列 —— 最大宽 760 居中，是整个视图的版心。 */
const column = {
  width: "100%",
  maxWidth: MESSAGE_MAX_WIDTH,
  marginLeft: "auto",
  marginRight: "auto",
  boxSizing: "border-box",
} as const;

export function PrimaryChatView({ channel, onOpenSettings, active = true }: PrimaryChatViewProps) {
  const [controller] = useState(() => new PrimaryController(disconnected));
  const [bindingRecovery] = useState(() => new PrimaryBindingRecovery());
  const [decisionRefresh, setDecisionRefresh] = useState(0);
  const [bindingRefresh, setBindingRefresh] = useState(0);
  const refreshBindings = useCallback(() => setBindingRefresh(value => value + 1), []);
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  const primaryPort = useMemo(() => channel ? boundPrimaryPort(channel) : disconnected, [channel]);
  useEffect(() => controller.start(primaryPort), [controller, primaryPort]);
  useEffect(() => {
    const focus = () => { void controller.refreshLatest(); };
    window.addEventListener("focus", focus);
    return () => window.removeEventListener("focus", focus);
  }, [controller]);
  const run = snapshot.state?.current_run;
  const runLabel = run ? runLabels[run.state.toUpperCase()] ?? run.state : "空闲";
  const canSend = snapshot.ready && snapshot.state !== null;
  const status = canSend ? `${runLabel} · 排队 ${snapshot.state?.queued_count_truncated ? "至少 " : ""}${snapshot.state?.queued_count ?? 0}` : "等待主对话就绪";
  return <section data-testid="view-chat" aria-label="主对话" style={{
    position: "relative", display: "flex", flexDirection: "column", flex: 1, minHeight: 0,
    background: dark.bgSolid, color: dark.text, fontFamily: tokens.font.ui,
  }}>
    <header data-testid="chat-header" style={viewHeader}>
      <h1 data-testid="chat-title" style={{ ...titleText, flex: 1 }}>主对话</h1>
      <button className={INTERACTIVE_CLASS} style={buttonStyle("ghost", "md")} onClick={onOpenSettings}
        title="前台任务使用全局 Provider 模型与参数">模型与设置</button>
      <button className={INTERACTIVE_CLASS} style={buttonStyle("ghost", "md")}
        disabled={!snapshot.ready || snapshot.loading} onClick={() => {
          setDecisionRefresh((value) => value + 1);
          void controller.refreshLatest();
        }}>刷新状态</button>
    </header>

    {/* 状态与通知：低调的单行 meta，不再是抢眼的横条。 */}
    <div style={{ ...column, padding: `${tokens.space.md}px ${tokens.space.xl}px 0` }}>
      <div role="status" aria-live="polite" style={metaText}>
        {status}{snapshot.loading ? " · 正在读取" : ""}
      </div>
      {snapshot.notice && <p style={{ ...metaText, margin: `${tokens.space.xs}px 0 0`, color: dark.textFaint }}>{snapshot.notice}</p>}
      {snapshot.error && <p role="alert" style={{ ...metaText, margin: `${tokens.space.xs}px 0 0`, color: dark.danger }}>{snapshot.error}</p>}
    </div>

    <div aria-label="主对话历史" style={{
      overflowY: "auto", flex: 1, minHeight: 0,
      padding: `${tokens.space.lg}px ${tokens.space.xl}px 0`,
    }}>
      <div style={column}>
        {/* 「查看更早消息」是消息区顶部的细文字链接，不是按钮块。 */}
        {snapshot.nextCursor && <div style={{ textAlign: "center", marginBottom: tokens.space.lg }}>
          <button className={INTERACTIVE_CLASS} style={textLinkStyle(snapshot.loading)}
            disabled={snapshot.loading} onClick={() => void controller.loadOlder()}>查看更早消息</button>
        </div>}
        {snapshot.messages.length === 0 && canSend && !snapshot.loading && <div style={emptyState}>
          <Icon name="message" size={28} strokeWidth={1.2} style={{ color: dark.textFaint }} />
          <p style={{ margin: 0 }}>继续在这里交流，历史由主对话保存。</p>
        </div>}
        {snapshot.messages.map((message) => <PrimaryMessageRow key={`${snapshot.viewEpoch}:${message.message_ref}`} message={message} controller={controller} />)}
      </div>
    </div>

    {active && snapshot.primaryRef && <div style={{ ...column, padding: `0 ${tokens.space.xl}px` }}>
      <PrimaryWorkspaceBindings port={primaryPort} primaryRef={snapshot.primaryRef}
        ownerKey={snapshot.verifiedOwnerKey} ready={snapshot.ready} recovery={bindingRecovery} refreshVersion={decisionRefresh + bindingRefresh} />
    </div>}
    {snapshot.ready && snapshot.state && run?.execution_session_ref && run.sdk_run_ref && <div style={{ ...column, padding: `0 ${tokens.space.xl}px`, maxHeight: "35%", overflowY: "auto" }}>
      <PrimaryRunPanel key={`${snapshot.draftEpoch}:${run.run_ref}:${run.generation}:${run.execution_session_ref}:${run.sdk_run_ref}`}
        run={run} port={primaryPort} primaryRef={snapshot.state.primary_ref} visible={active} refreshVersion={decisionRefresh} onToolResult={refreshBindings} onStop={() => void controller.control("stop", run)} />
    </div>}
    {run && (!run.execution_session_ref || !run.sdk_run_ref) && <div style={{ ...column, padding: `0 ${tokens.space.xl}px` }}>
      <p style={{ ...metaText, margin: `${tokens.space.xs}px 0` }}>正在等待执行绑定；权限与工具详情将在绑定后恢复。</p>
    </div>}

    <div style={{ ...column, padding: `${tokens.space.md}px ${tokens.space.xl}px ${tokens.space.lg}px`, flexShrink: 0 }}>
      <InputBar key={snapshot.draftEpoch} disabled={!canSend} placeholder="输入消息，Enter 发送…"
        primary={{ submit: controller.submit, status, stop: run && snapshot.ready ? () => void controller.control("stop", run) : undefined }} />
    </div>

    {/* 2026-09-10：认知记忆抽屉（记忆列表 / 关系图 / 记忆审计）随
        simple-harness-memory-sdk 一并下线。 */}
  </section>;
}
const roleLabels: Record<PrimaryMessage["role"], string> = {
  user: "你", assistant: "助手", tool: "工具", artifact: "产物", reminder: "提醒",
};
/**
 * 三种角色必须一眼分得开（UAT 2026-09-09：「我分不清哪句话是我说的，哪句话是
 * AI 回答的」）。用户靠右、强调色极淡填充 + 细描边；助手靠左、面色卡片带小
 * 头像点；工具灰底等宽、整行可点折叠。
 */
const roleSkins: Record<PrimaryMessage["role"], { align: "flex-end" | "flex-start"; background: string; border: string; accent: string }> = {
  user: { align: "flex-end", background: dark.accentSoft, border: dark.accentBorder, accent: dark.accentText },
  assistant: { align: "flex-start", background: dark.card, border: dark.cardBorder, accent: dark.text },
  tool: { align: "flex-start", background: dark.raised, border: dark.insetBorder, accent: dark.textMuted },
  artifact: { align: "flex-start", background: dark.card, border: dark.insetBorder, accent: dark.textMuted },
  reminder: { align: "flex-start", background: dark.card, border: dark.insetBorder, accent: dark.textMuted },
};
/** 工具正文首行是 Host 拼上的 `{"call_id":…,"name":…}`；拆出来做折叠标题。 */
export function toolHeadline(text: string): { name: string; body: string } {
  const cut = text.indexOf("\n");
  const head = cut === -1 ? text : text.slice(0, cut);
  try {
    const meta = JSON.parse(head) as Record<string, unknown>;
    if (meta && typeof meta === "object" && typeof meta.name === "string" && meta.name) {
      return { name: meta.name, body: cut === -1 ? "" : text.slice(cut + 1) };
    }
  } catch { /* 不是元数据首行就整体当正文 */ }
  return { name: "", body: text };
}
function PrimaryMessageRow({ message, controller }: { message: PrimaryMessage; controller: PrimaryController }) {
  const [detail, setDetail] = useState<{ text: string; offset: number | null } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [toolOpen, setToolOpen] = useState(false);
  const load = async (offset: number) => {
    if (busy) return;
    setBusy(true);
    setDetail(null);
    try {
      const chunk = await controller.detail(message.message_ref, offset);
      setDetail({ text: chunk.text, offset: chunk.next_offset }); setError("");
    } catch (err) { setError(err instanceof Error ? err.message : "详情读取失败"); }
    finally { setBusy(false); }
  };
  const skin = roleSkins[message.role];
  const isTool = message.role === "tool";
  const isUser = message.role === "user";
  const isAssistant = message.role === "assistant";
  const tool = isTool ? toolHeadline(message.text) : null;
  const label = isTool ? `工具 · ${tool!.name || "未命名"}` : roleLabels[message.role];
  const body = <>
    {message.has_more && <button className={INTERACTIVE_CLASS} style={{ ...textLinkStyle(busy), marginTop: tokens.space.sm, paddingLeft: 0 }} disabled={busy}
      onClick={() => void load(0)}>读取完整消息（{message.total_chars} 字符）</button>}
    {detail && <div aria-label="消息详情" style={{ marginTop: tokens.space.sm }}><MarkdownMessage>{detail.text}</MarkdownMessage>
      {detail.offset !== null && <button className={INTERACTIVE_CLASS} style={textLinkStyle(busy)} disabled={busy} onClick={() => void load(detail.offset!)}>下一段</button>}
      <button className={INTERACTIVE_CLASS} style={textLinkStyle()} onClick={() => { setDetail(null); setError(""); }}>关闭详情</button>
    </div>}
    {error && <p role="alert" style={{ ...metaText, color: dark.danger, margin: `${tokens.space.xs}px 0 0` }}>{error}</p>}
  </>;
  return <div style={{ display: "flex", justifyContent: skin.align, alignItems: "flex-start", gap: tokens.space.sm, marginBottom: tokens.space.lg }}>
    {/* 助手的小头像点 —— 唯一强调色，直径 8，与首行文字对齐。 */}
    {isAssistant && <span aria-hidden style={{
      width: 8, height: 8, borderRadius: "50%", background: dark.accent,
      marginTop: 14, flexShrink: 0,
    }} />}
    <article data-testid={`primary-message-${message.role}`} data-role={message.role}
      style={{
        padding: isTool ? `${tokens.space.xs}px ${tokens.space.md}px` : `${tokens.space.md}px ${tokens.space.lg}px`,
        borderRadius: isTool ? tokens.radius.md : tokens.radius.bubble,
        maxWidth: isTool ? "100%" : "82%",
        width: isTool ? "100%" : undefined,
        background: skin.background,
        border: `1px solid ${skin.border}`,
        overflowWrap: "anywhere",
        fontFamily: isTool ? tokens.font.mono : tokens.font.ui,
        fontSize: isTool ? tokens.text.sm.size : tokens.text.md.size,
        lineHeight: tokens.text.md.lh,
        color: dark.text,
        textAlign: isUser ? "right" : "left",
        boxSizing: "border-box",
      }}>
      {isTool
        ? <button aria-expanded={toolOpen} onClick={() => setToolOpen((open) => !open)}
            className={`${INTERACTIVE_CLASS} sh-tool-row`}
            style={{
              display: "flex", alignItems: "center", gap: tokens.space.xs,
              width: "100%", height: 28, padding: 0, border: 0, background: "transparent",
              fontFamily: tokens.font.mono, fontSize: tokens.text.sm.size,
              color: skin.accent, justifyContent: "flex-start",
            }}>
            {toolOpen ? "▾ " : "▸ "}{label}
          </button>
        : isUser || isAssistant
          ? null
          : <div style={{ ...metaText, color: skin.accent, fontWeight: tokens.weight.semibold, marginBottom: tokens.space.xs }}>{label}</div>}
      {/* 气泡整体右对齐，但气泡内正文始终左对齐 —— 右对齐的多行中文
          会出现锯齿状左边缘，正是"难看"的来源。 */}
      {(!isTool || toolOpen) && <div style={{ textAlign: "left" }}>
        <MarkdownMessage>{isTool ? tool!.body : message.text}</MarkdownMessage>
        {body}
      </div>}
    </article>
  </div>;
}
