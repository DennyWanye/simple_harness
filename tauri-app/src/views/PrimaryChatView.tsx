// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { ControlChannel } from "../ws/ControlChannel";
import { InputBar } from "../code-panel/InputBar";
import { MarkdownMessage } from "../components/MarkdownMessage";
import { dark, buttonStyle } from "../theme/components";
import { tokens } from "../theme/tokens";
import { boundPrimaryPort } from "../primary/boundPort";
import { PrimaryController, type PrimaryMessage, type PrimaryPort } from "../primary/controller";
import { PrimaryRunPanel } from "../primary/PrimaryRunPanel";
import { PrimaryWorkspaceBindings } from "../primary/PrimaryWorkspaceBindings";
import { PrimaryBindingRecovery } from "../primary/bindingRecovery";
import { PrimaryMemoryPanel } from "../components/PrimaryMemoryPanel";
import { CognitiveRequests } from "../primary/cognitiveRequests";

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
export function PrimaryChatView({ channel, onOpenSettings, active = true }: PrimaryChatViewProps) {
  const [controller] = useState(() => new PrimaryController(disconnected));
  const [bindingRecovery] = useState(() => new PrimaryBindingRecovery());
  const [memoryRequests] = useState(() => new CognitiveRequests());
  const [memoryOpen, setMemoryOpen] = useState(false);
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
  return <section data-testid="view-chat" aria-label="主对话" style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0, color: dark.text }}>
    <header data-testid="chat-header" style={{ display: "flex", alignItems: "center", gap: 12, padding: 16, borderBottom: `1px solid ${dark.hairline}` }}>
      <strong data-testid="chat-title" style={{ flex: 1 }}>主对话</strong>
      <button style={buttonStyle("secondary", "sm")} disabled={!canSend}
        aria-expanded={memoryOpen} onClick={() => setMemoryOpen((open) => !open)}>记忆</button>
      <button style={buttonStyle("secondary", "sm")} onClick={onOpenSettings} title="前台任务使用全局 Provider 模型与参数">模型与设置</button>
      <button style={buttonStyle("secondary", "sm")} disabled={!snapshot.ready || snapshot.loading} onClick={() => {
        setDecisionRefresh((value) => value + 1);
        void controller.refreshLatest();
      }}>刷新状态</button>
    </header>
    <div role="status" aria-live="polite" style={{ padding: "8px 16px" }}>{status}{snapshot.loading ? " · 正在读取" : ""}</div>
    {snapshot.notice && <p style={{ margin: "0 16px 8px", color: dark.textMuted }}>{snapshot.notice}</p>}
    {snapshot.error && <p role="alert" style={{ margin: "0 16px 8px", color: "#fca5a5" }}>{snapshot.error}</p>}
    {memoryOpen && active && snapshot.primaryRef && <div style={{ maxHeight: "50%", overflowY: "auto", flexShrink: 1 }}>
      <PrimaryMemoryPanel port={primaryPort} primaryRef={snapshot.primaryRef}
        verifiedOwnerKey={snapshot.verifiedOwnerKey} ready={snapshot.ready}
        requests={memoryRequests} onForgotten={controller.refreshLatest} onClose={() => setMemoryOpen(false)} />
    </div>}
    <div aria-label="主对话历史" style={{ overflowY: "auto", flex: 1, minHeight: 0, padding: "0 16px" }}>
      {snapshot.nextCursor && <button style={{ ...buttonStyle("secondary", "sm"), margin: "8px 0" }}
        disabled={snapshot.loading} onClick={() => void controller.loadOlder()}>查看更早消息</button>}
      {snapshot.messages.length === 0 && canSend && !snapshot.loading && <p>继续在这里交流，历史由主对话保存。</p>}
      {snapshot.messages.map((message) => <PrimaryMessageRow key={`${snapshot.viewEpoch}:${message.message_ref}`} message={message} controller={controller} />)}
    </div>
    {active && snapshot.primaryRef && <PrimaryWorkspaceBindings port={primaryPort} primaryRef={snapshot.primaryRef}
      ownerKey={snapshot.verifiedOwnerKey} ready={snapshot.ready} recovery={bindingRecovery} refreshVersion={decisionRefresh + bindingRefresh} />}
    {snapshot.ready && snapshot.state && run?.execution_session_ref && run.sdk_run_ref && <div style={{ padding: "0 16px", maxHeight: "35%", overflowY: "auto" }}>
      <PrimaryRunPanel key={`${snapshot.draftEpoch}:${run.run_ref}:${run.generation}:${run.execution_session_ref}:${run.sdk_run_ref}`}
        run={run} port={primaryPort} primaryRef={snapshot.state.primary_ref} visible={active} refreshVersion={decisionRefresh} onToolResult={refreshBindings} onStop={() => void controller.control("stop", run)} />
    </div>}
    {run && (!run.execution_session_ref || !run.sdk_run_ref) && <p style={{ margin: "4px 16px" }}>正在等待执行绑定；权限与工具详情将在绑定后恢复。</p>}
    <InputBar key={snapshot.draftEpoch} disabled={!canSend} placeholder="输入消息，Enter 发送…"
      primary={{ submit: controller.submit, status, stop: run && snapshot.ready ? () => void controller.control("stop", run) : undefined }} />
  </section>;
}
const roleLabels: Record<PrimaryMessage["role"], string> = {
  user: "你", assistant: "助手", tool: "工具", artifact: "产物", reminder: "提醒",
};
/**
 * 三种角色必须一眼分得开（UAT 2026-09-09：「我分不清哪句话是我说的，哪句话是
 * AI 回答的」）。用户靠右、蓝色底；助手靠左、卡片底；工具灰底等宽、默认折叠。
 */
const roleSkins: Record<PrimaryMessage["role"], { align: "flex-end" | "flex-start"; background: string; border: string; accent: string }> = {
  user: { align: "flex-end", background: "rgba(79,147,255,0.16)", border: "rgba(79,147,255,0.38)", accent: dark.accent },
  assistant: { align: "flex-start", background: dark.card, border: dark.cardBorder, accent: dark.text },
  tool: { align: "flex-start", background: dark.inset, border: dark.insetBorder, accent: dark.textMuted },
  artifact: { align: "flex-start", background: dark.inset, border: dark.insetBorder, accent: dark.textMuted },
  reminder: { align: "flex-start", background: dark.inset, border: dark.insetBorder, accent: dark.textMuted },
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
  const tool = isTool ? toolHeadline(message.text) : null;
  const label = isTool ? `工具 · ${tool!.name || "未命名"}` : roleLabels[message.role];
  const body = <>
    {message.has_more && <button style={{ ...buttonStyle("ghost", "sm"), marginTop: 6 }} disabled={busy}
      onClick={() => void load(0)}>读取完整消息（{message.total_chars} 字符）</button>}
    {detail && <div aria-label="消息详情" style={{ marginTop: 6 }}><MarkdownMessage>{detail.text}</MarkdownMessage>
      {detail.offset !== null && <button style={buttonStyle("ghost", "sm")} disabled={busy} onClick={() => void load(detail.offset!)}>下一段</button>}
      <button style={buttonStyle("ghost", "sm")} onClick={() => { setDetail(null); setError(""); }}>关闭详情</button>
    </div>}
    {error && <p role="alert" style={{ color: "#fca5a5" }}>{error}</p>}
  </>;
  return <div style={{ display: "flex", justifyContent: skin.align, marginBottom: 12 }}>
    <article data-testid={`primary-message-${message.role}`} data-role={message.role}
      style={{ padding: "10px 14px", borderRadius: 12, maxWidth: isTool ? "100%" : "82%",
        width: isTool ? "100%" : undefined, background: skin.background, border: `1px solid ${skin.border}`,
        overflowWrap: "anywhere", fontFamily: isTool ? tokens.font.mono : tokens.font.ui,
        textAlign: message.role === "user" ? "right" : "left" }}>
      {isTool
        ? <button aria-expanded={toolOpen} onClick={() => setToolOpen((open) => !open)}
            style={{ ...buttonStyle("ghost", "sm"), width: "100%", justifyContent: "flex-start",
              fontFamily: tokens.font.mono, color: skin.accent }}>
            {toolOpen ? "▾ " : "▸ "}{label}
          </button>
        : <div style={{ color: skin.accent, fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{label}</div>}
      {(!isTool || toolOpen) && <MarkdownMessage>{isTool ? tool!.body : message.text}</MarkdownMessage>}
      {(!isTool || toolOpen) && body}
    </article>
  </div>;
}
