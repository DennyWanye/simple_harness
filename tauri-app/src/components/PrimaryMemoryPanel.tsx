// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 认知记忆面板（2026-09-09 改版：右侧抽屉宿主，宽 420）。
 *
 * 版式：面板顶栏（标题 + 刷新/关闭 幽灵按钮）→ 下划线 tab（选中态
 * 2px 强调下划线）→ 可滚动内容区。空态与加载态都有设计（细线图标 +
 * 一句提示）。
 *
 * 无障碍名称锁：tab「记忆列表 / 关系图 / 操作记录 / 任务」必须保持
 * `aria-pressed`（macOS AX 映射为 AXCheckBox），按钮「刷新 / 关闭 /
 * 忘记这条记忆」文案不得改。
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { PrimaryMemoryGraph } from "./PrimaryMemoryGraph";
import { PrimaryAuditPanel } from "./PrimaryAuditPanel";
import { PrimaryTaskPanel } from "./PrimaryTaskPanel";
import { Icon } from "./Icon";
import { CognitiveRequests } from "../primary/cognitiveRequests";
import type { PrimaryPort } from "../primary/controller";
import {
  INTERACTIVE_CLASS,
  buttonStyle,
  dark,
  darkPanelHeader,
  emptyState,
  metaText,
  segGroup,
  segTab,
  textLinkStyle,
  titleText,
} from "../theme/components";
import { tokens } from "../theme/tokens";

export interface PrimaryMemoryPanelProps {
  port: PrimaryPort;
  primaryRef: string;
  /** Derived from the actual bound owner, not global identity_status. */
  verifiedOwnerKey: string | null;
  ready: boolean;
  /** Main may retain this instance across panel close/reopen for unresolved action IDs. */
  requests?: CognitiveRequests;
  /** Clear old history/detail synchronously, then start a fresh authenticated read. */
  onForgotten?: () => void;
  onClose?: () => void;
}

const TABS = [
  { key: "list", label: "记忆列表" },
  { key: "graph", label: "关系图" },
  { key: "audit", label: "操作记录" },
  { key: "task", label: "任务" },
] as const;

export function PrimaryMemoryPanel({ port, primaryRef, verifiedOwnerKey, ready, requests, onForgotten, onClose }: PrimaryMemoryPanelProps) {
  const [tab, setTab] = useState<"list" | "graph" | "audit" | "task">("list");
  // Layout-only state survives the owner-keyed graph remount; no data is retained.
  const viewportShown = useRef(false);
  const claimInitialReveal = useCallback(() => {
    const first = !viewportShown.current;
    viewportShown.current = true;
    return first;
  }, []);
  const [local] = useState(() => new CognitiveRequests());
  const client = requests ?? local;
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  useEffect(() => onForgotten ? client.onForgotten(onForgotten) : undefined, [client, onForgotten]);
  useEffect(() => client.connect(port, primaryRef, verifiedOwnerKey, ready), [client, port, primaryRef, verifiedOwnerKey, ready]);
  return <section aria-label="认知记忆" style={{
    display: "flex", flexDirection: "column", flex: 1, minHeight: 0,
    color: dark.text, fontFamily: tokens.font.ui,
  }}>
    <header style={darkPanelHeader}>
      <h2 style={{ ...titleText, fontSize: tokens.text.lg.size, flex: 1 }}>认知记忆</h2>
      <button className={INTERACTIVE_CLASS} style={buttonStyle("ghost", "sm")}
        disabled={!state.ready || state.loading} onClick={() => void client.refresh()}>刷新</button>
      {onClose && <button className={INTERACTIVE_CLASS} style={buttonStyle("ghost", "sm")} onClick={onClose}>关闭</button>}
    </header>

    <nav aria-label="记忆视图" style={{ ...segGroup, display: "flex", padding: `0 ${tokens.space.lg}px`, flexShrink: 0 }}>
      {TABS.map((item) => <button key={item.key} className={`${INTERACTIVE_CLASS} bp-tab`}
        style={segTab(tab === item.key)} aria-pressed={tab === item.key}
        onClick={() => { if (item.key === "graph" && tab !== "graph") viewportShown.current = false; setTab(item.key); }}>
        {item.label}
      </button>)}
    </nav>

    <div className="sh-prose" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: tokens.space.lg }}>
      {tab === "graph" && <PrimaryMemoryGraph key={`${primaryRef}:${verifiedOwnerKey ?? "unbound"}`} port={port} primaryRef={primaryRef} verifiedOwnerKey={verifiedOwnerKey} ready={ready} cognitive={client} claimInitialReveal={claimInitialReveal} />}
      {tab === "task" && <PrimaryTaskPanel port={port} primaryRef={primaryRef} verifiedOwnerKey={verifiedOwnerKey} ready={ready} />}
      {tab === "audit" && <PrimaryAuditPanel port={port} primaryRef={primaryRef} verifiedOwnerKey={verifiedOwnerKey} ready={ready} />}
      {tab === "list" && <>
      <p style={{ ...metaText, margin: `0 0 ${tokens.space.lg}px` }}>这里展示当前认知记忆。忘记会禁止该记忆继续使用，原始历史档案保留；不会自动撤销。</p>
      {!state.ready && <p role="status" style={metaText}>等待当前连接身份确认</p>}
      {state.notice && <p role="status" style={{ ...metaText, color: dark.accentText }}>{state.notice}</p>}
      {state.error && <p role="alert" style={{ ...metaText, color: dark.danger }}>{state.error}</p>}
      {state.ready && <>
        {state.loading && <p role="status" style={metaText}>正在读取…</p>}
        {/* One malformed item degrades to a visible notice; the rest of the page still renders. */}
        {!state.loading && state.skipped > 0 && <p role="alert" style={{ ...metaText, color: dark.warning }}>有 {state.skipped} 条记忆条目无效，已跳过；其余条目仍可操作。</p>}
        {!state.loading && !state.error && state.items.length === 0 && <div style={emptyState}>
          <Icon name="archive" size={26} strokeWidth={1.2} style={{ color: dark.textFaint }} />
          <p style={{ margin: 0 }}>当前页没有可展示的认知记忆。</p>
        </div>}
        <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: tokens.space.sm }}>
          {state.items.map((item) => <li key={item.memory_id} className="bp-card" style={{
            padding: tokens.space.md, background: dark.card,
            border: `1px solid ${dark.cardBorder}`, borderRadius: tokens.radius.lg,
            overflowWrap: "anywhere",
          }}>
            <p style={{ margin: `0 0 ${tokens.space.sm}px`, fontSize: tokens.text.base.size, lineHeight: tokens.text.base.lh }}>{item.label}</p>
            <div><button className={INTERACTIVE_CLASS} style={buttonStyle("ghost", "sm")} disabled={!item.can_forget || state.writing}
              onClick={() => void client.forget(item)}>忘记这条记忆</button></div>
          </li>)}
        </ul>
        {state.nextCursor && <div style={{ marginTop: tokens.space.md }}>
          <button className={INTERACTIVE_CLASS} style={textLinkStyle(state.loading)} disabled={state.loading}
            onClick={() => void client.refresh(state.nextCursor)}>下一页</button>
        </div>}
        {state.pending.map((action) => <div key={action.action_id} style={{
          marginTop: tokens.space.md, padding: tokens.space.md,
          border: `1px solid ${dark.borderStrong}`, borderLeft: `2px solid ${dark.warning}`,
          borderRadius: tokens.radius.md,
        }}>
          <p style={{ ...metaText, margin: `0 0 ${tokens.space.sm}px` }}>有一项忘记操作尚未确认。</p>
          <button className={INTERACTIVE_CLASS} style={buttonStyle("ghost", "sm")} disabled={state.writing}
            onClick={() => void client.retry(action.action_id)}>重试同一忘记操作</button>
        </div>)}
      </>}
      </>}
    </div>
  </section>;
}
