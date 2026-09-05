// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { ControlChannel } from "../ws/ControlChannel";
import { InputBar } from "../code-panel/InputBar";
import { MarkdownMessage } from "../components/MarkdownMessage";
import { dark, buttonStyle } from "../theme/components";
import { tokens } from "../theme/tokens";
import { boundPrimaryPort } from "../primary/boundPort";
import { PrimaryController, type PrimaryMessage, type PrimaryPort } from "../primary/controller";
import { PrimaryRunPanel } from "../primary/PrimaryRunPanel";
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
  const [memoryRequests] = useState(() => new CognitiveRequests());
  const [memoryOpen, setMemoryOpen] = useState(false);
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
      <button style={buttonStyle("secondary", "sm")} disabled={!snapshot.ready || snapshot.loading} onClick={() => void controller.refreshLatest()}>刷新状态</button>
    </header>
    <div role="status" aria-live="polite" style={{ padding: "8px 16px" }}>{status}{snapshot.loading ? " · 正在读取" : ""}</div>
    {snapshot.notice && <p style={{ margin: "0 16px 8px", color: dark.textMuted }}>{snapshot.notice}</p>}
    {snapshot.error && <p role="alert" style={{ margin: "0 16px 8px", color: "#fca5a5" }}>{snapshot.error}</p>}
    {memoryOpen && active && snapshot.state && <div style={{ maxHeight: "50%", overflowY: "auto", flexShrink: 1 }}>
      <PrimaryMemoryPanel port={primaryPort} primaryRef={snapshot.state.primary_ref}
        verifiedOwnerKey={snapshot.verifiedOwnerKey} ready={canSend}
        requests={memoryRequests} onForgotten={controller.refreshLatest} onClose={() => setMemoryOpen(false)} />
    </div>}
    <div aria-label="主对话历史" style={{ overflowY: "auto", flex: 1, minHeight: 0, padding: "0 16px" }}>
      {snapshot.nextCursor && <button disabled={snapshot.loading} onClick={() => void controller.refresh(snapshot.nextCursor)}>查看更早消息</button>}
      {snapshot.messages.length === 0 && canSend && !snapshot.loading && <p>继续在这里交流，历史由主对话保存。</p>}
      {snapshot.messages.map((message) => <PrimaryMessageRow key={`${snapshot.viewEpoch}:${message.message_ref}`} message={message} controller={controller} />)}
    </div>
    {snapshot.ready && snapshot.state && run?.execution_session_ref && run.sdk_run_ref && <div style={{ padding: "0 16px", maxHeight: "35%", overflowY: "auto" }}>
      <PrimaryRunPanel key={`${snapshot.draftEpoch}:${run.run_ref}:${run.generation}:${run.execution_session_ref}:${run.sdk_run_ref}`}
        run={run} port={primaryPort} primaryRef={snapshot.state.primary_ref} visible={active} onStop={() => void controller.control("stop", run)} />
    </div>}
    {run && (!run.execution_session_ref || !run.sdk_run_ref) && <p style={{ margin: "4px 16px" }}>正在等待执行绑定；权限与工具详情将在绑定后恢复。</p>}
    <InputBar key={snapshot.draftEpoch} disabled={!canSend} placeholder="输入消息，Enter 发送…"
      primary={{ submit: controller.submit, status, stop: run && snapshot.ready ? () => void controller.control("stop", run) : undefined }} />
  </section>;
}
function PrimaryMessageRow({ message, controller }: { message: PrimaryMessage; controller: PrimaryController }) {
  const [detail, setDetail] = useState<{ text: string; offset: number | null } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
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
  return <article style={{ padding: "12px 16px", marginBottom: 12, borderRadius: 12, background: dark.card, overflowWrap: "anywhere", fontFamily: tokens.font.ui }}>
    <div style={{ color: dark.textMuted, fontSize: 12 }}>{({ user: "你", assistant: "助手", tool: "工具", artifact: "产物" })[message.role]}</div>
    <MarkdownMessage>{message.text}</MarkdownMessage>
    {message.has_more && <button disabled={busy} onClick={() => void load(0)}>读取完整消息（{message.total_chars} 字符）</button>}
    {detail && <div aria-label="消息详情"><MarkdownMessage>{detail.text}</MarkdownMessage>
      {detail.offset !== null && <button disabled={busy} onClick={() => void load(detail.offset!)}>下一段</button>}
      <button onClick={() => { setDetail(null); setError(""); }}>关闭详情</button>
    </div>}
    {error && <p role="alert">{error}</p>}
  </article>;
}
