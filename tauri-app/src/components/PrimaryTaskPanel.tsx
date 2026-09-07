// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { useEffect, useState, useSyncExternalStore } from "react";
import type { PrimaryPort } from "../primary/controller";
import { TaskScopeRequests, type TaskScopeOpen, type TaskScopeViewKind } from "../primary/taskScopeRequests";
import { buttonStyle, dark } from "../theme/components";

const pageInKinds: TaskScopeViewKind[] = ["PLAN", "DECISIONS", "RESUME", "EVIDENCE"];
const pre = { whiteSpace: "pre-wrap", overflowWrap: "anywhere", background: dark.card, padding: 8, borderRadius: 6, maxHeight: 240, overflowY: "auto" } as const;
const short = (v: string) => v.length > 16 ? `${v.slice(0, 8)}…${v.slice(-6)}` : v;
function statusSummary(open: TaskScopeOpen): Record<string, unknown> | null {
  try { const v: unknown = JSON.parse(open.resume_package.read_views.STATUS.content); return v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null; }
  catch { return null; }
}
/** Read-only TaskScope inspect. Candidates grant nothing; exact open shows a projection, never execution authority. */
export function PrimaryTaskPanel({ port, primaryRef, verifiedOwnerKey, ready }: {
  port: PrimaryPort; primaryRef: string; verifiedOwnerKey: string | null; ready: boolean;
}) {
  const [client] = useState(() => new TaskScopeRequests());
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  const [draft, setDraft] = useState("");
  useEffect(() => client.connect(port, primaryRef, verifiedOwnerKey, ready), [client, port, primaryRef, verifiedOwnerKey, ready]);
  if (!ready || verifiedOwnerKey === null || !state.ready) return <section aria-label="任务范围"><p role="status">等待当前连接身份确认</p></section>;
  const open = state.open, status = open ? statusSummary(open) : null, pkg = open?.resume_package;
  return <section aria-label="任务范围">
    <p>只读查看自己的任务范围。搜索结果只是候选，候选不授予任何权限；只有精确打开并通过来源校验后才显示归档视图。这里不提供继续执行、修改任务或绑定目录的操作。</p>
    <form onSubmit={(event) => { event.preventDefault(); void client.search(draft); }} style={{ display: "flex", gap: 8 }}>
      <input aria-label="搜索任务" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="输入任务标题、目标或关键词" style={{ flex: 1 }} />
      <button type="submit" style={buttonStyle("secondary", "sm")} disabled={state.busy || !draft.trim()}>搜索</button>
    </form>
    {state.busy && <p role="status">正在读取…</p>}
    {state.notice && <p role="status">{state.notice}</p>}
    {state.error && <p role="alert">{state.error}</p>}
    {state.search && <>
      <p>候选 {state.search.candidates.length} 项（不授予任何权限）</p>
      {state.search.candidates.length === 0 && <p>没有匹配的任务候选。</p>}
      <ul aria-label="任务候选" style={{ listStyle: "none", padding: 0 }}>{state.search.candidates.map((c) => <li key={c.scope_ref} style={{ padding: 12, marginBottom: 8, background: dark.card, borderRadius: 8, overflowWrap: "anywhere" }}>
        <p><strong>{c.title || "（无标题）"}</strong> · {c.status || "状态未知"}{c.project && ` · ${c.project}`}</p>
        {c.goal && <p>目标：{c.goal}</p>}
        {c.snippet && <p style={{ color: dark.textMuted }}>{c.snippet}</p>}
        <details><summary>任务标识</summary><p>{c.scope_ref}<br />来源 {short(c.source_hash)}</p></details>
        <button style={buttonStyle("secondary", "sm")} disabled={state.busy} onClick={() => void client.open(c)}>精确打开</button>
      </li>)}</ul>
      {state.search.next_cursor && <button disabled={state.busy} onClick={() => void client.search(state.query, state.search?.next_cursor ?? null)}>更多候选</button>}
    </>}
    {open && pkg && <article aria-label="任务详情" style={{ marginTop: 12 }}>
      <h3>已打开的任务（只读投影，不是执行授权）</h3>
      <pre style={pre}>{pkg.read_views.README.content}</pre>
      {status && <ul>
        <li>状态：{String(status.status ?? "未知")}</li>
        <li>目标：{String(status.goal ?? "未设置")}</li>
        <li>事件水位：{String(status.event_watermark ?? "?")} · 检查点序号：{String(status.checkpoint_sequence ?? "?")}</li>
        {status.semantic_closure_pending === true && <li role="status">存在 {String(status.pending_closure_count ?? "")} 项未闭合的语义收尾，当前状态不视为一致。</li>}
      </ul>}
      <details><summary>来源修订与凭证</summary>
        <p style={{ overflowWrap: "anywhere" }}>
          来源 {open.source_ref} · 修订 {pkg.canonical_revision}<br />
          来源哈希 {open.source_hash}<br />
          恢复包哈希 {open.resume_sha256}<br />
          打开凭证 {open.receipt_ref}
        </p>
      </details>
      <p role="status">{open.drift_report === null
        ? "本次打开未附带实时漂移探测（由 Host 决定是否执行），不能作为新鲜度证明。"
        : open.drift_report.drifted
          ? `检查点已漂移：${open.drift_report.changed_fields.join("、") || "未列出字段"}（报告 ${short(open.drift_report.report_hash)}）`
          : `检查点未漂移（报告 ${short(open.drift_report.report_hash)}）`}</p>
      <p>绑定来源（只读）：{pkg.binding_set_revision === 0 ? "尚无工作区绑定。" : `绑定修订 ${pkg.binding_set_revision}，凭证 ${pkg.binding_receipt_hash ? short(pkg.binding_receipt_hash) : "缺失"}。`}
        Manual/Auto 由 Host 按可信 Run 模式记录，界面不提供切换。</p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{pageInKinds.map((kind) => <button key={kind} style={buttonStyle("secondary", "sm")}
        disabled={state.busy} onClick={() => void client.loadView(kind)}>{state.views[kind] ? `重新读取 ${kind}` : `读取 ${kind}`}</button>)}</div>
      {pageInKinds.filter((kind) => state.views[kind]).map((kind) => {
        const view = state.views[kind]!;
        return <section key={kind} aria-label={`${kind} 视图`}>
          <h4>{kind} · 来源 {short(view.source_hash)} · 内容 {short(view.content_sha256)}{kind === "EVIDENCE" && ` · 区块 ${view.block_count}`}</h4>
          <pre style={pre}>{view.content}</pre>
        </section>;
      })}
      {state.views.EVIDENCE && <div>
        <button style={buttonStyle("secondary", "sm")} disabled={state.busy} onClick={() => void client.loadEvidenceGroups()}>列出证据分组</button>
        {state.evidenceGroups && <>
          {state.evidenceGroups.groups.length === 0 && <p>没有证据分组。</p>}
          <ul aria-label="证据分组">{state.evidenceGroups.groups.map((g) => <li key={g.group_ref}>
            分组 {g.logical_group}：事件 {g.first_event_sequence}–{g.last_event_sequence}（{g.event_count} 条）
            <button disabled={state.busy} onClick={() => void client.loadEvidencePage(g)}>读取分组 {g.logical_group}</button>
          </li>)}</ul>
          {state.evidenceGroups.next_cursor && <button disabled={state.busy} onClick={() => void client.loadEvidenceGroups(state.evidenceGroups?.next_cursor ?? null)}>更多分组</button>}
        </>}
        {state.evidencePage && <section aria-label="证据页">
          <p style={{ overflowWrap: "anywhere" }}>页 {state.evidencePage.page.page_id}</p>
          <pre style={pre}>{state.evidencePage.page.content}</pre>
          {state.evidencePage.next_cursor && <button disabled={state.busy} onClick={() => {
            const group = state.evidenceGroups?.groups.find((g) => g.group_ref === state.evidencePage?.group_ref);
            if (group) void client.loadEvidencePage(group, state.evidencePage?.next_cursor ?? null);
          }}>证据下一页</button>}
        </section>}
      </div>}
    </article>}
  </section>;
}
