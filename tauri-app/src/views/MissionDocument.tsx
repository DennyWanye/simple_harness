// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/** Document projection only. Formal Claim/assessment records are supplied by Host;
 * neither Worker prose nor source contents can manufacture a system conclusion.
 */
import React, { useEffect, useRef, useState } from "react";
import { asList, asRecord, asText, newRequestKey, type MissionsChannel } from "../stores/missionsStore";
import { dark } from "../theme/components";
import { tokens } from "../theme/tokens";

type Json = Record<string, unknown>;
type Channel = MissionsChannel | null;
export interface SourceDraft { path: string; content: string; kind: string }
const panel: React.CSSProperties = { padding: 12, borderRadius: 8, border: `1px solid ${tokens.color.surface.hairline}`, background: dark.inset, overflowWrap: "anywhere" };
const field: React.CSSProperties = { width: "100%", boxSizing: "border-box", background: dark.inset, color: dark.text, border: `1px solid ${tokens.color.surface.hairline}`, padding: 8, borderRadius: 6 };
const button: React.CSSProperties = { background: dark.panel, color: dark.text, border: `1px solid ${tokens.color.surface.hairline}`, borderRadius: 6, padding: "6px 10px", cursor: "pointer" };
const pre: React.CSSProperties = { whiteSpace: "pre-wrap", overflowWrap: "anywhere", margin: "6px 0", maxHeight: 400, overflow: "auto" };
const show = (value: unknown): string => value == null ? "未提供" : typeof value === "object" ? JSON.stringify(value, null, 2) : asText(value);
const Words = ({ value }: { value: unknown }) => <pre style={pre}>{show(value)}</pre>;
const LABELS: Record<string, string> = {
  PASS: "通过", FAIL: "未通过", INCONCLUSIVE: "证据不足", INSUFFICIENT: "证据不足", ERROR: "核验错误",
  VERIFIED: "已核验", SUPPORTED: "有依据支持", DISPUTED: "有争议", UNDER_REVIEW: "待核验", REJECTED: "未采纳",
  untrusted_external: "不可信外部来源", scope_limited_to_source: "范围限于来源", source_citation: "来源引用",
  path: "路径", version: "版本", kind: "类型", catalog: "范围", criterion: "条件", binding: "核验方式",
  source_path: "来源路径", literal: "逐字核对", task: "Task", mission: "Mission",
  stale_source: "来源已失效", revoked: "已撤销", superseded: "已替代", not_current: "非当前版本",
  verification_passed: "成功条件已判定满足", mission_criteria_unmet: "成功条件未全部满足",
  STRUCTURAL: "结构或执行条件，需实际检查判定", rule_check: "规则检查", document_coverage: "文档覆盖核验",
};
const label = (value: unknown) => value == null ? "尚未判定" : LABELS[asText(value)] ? `${LABELS[asText(value)]}（${asText(value)}）` : asText(value);
const readable = (value: unknown): string => {
  if (value == null) return "未提供";
  if (Array.isArray(value)) return value.length ? value.map(readable).join("；") : "无";
  if (typeof value === "object") return Object.entries(asRecord(value)).map(([key, item]) => `${LABELS[key] ?? key}：${readable(item)}`).join(" · ") || "未提供";
  if (typeof value === "boolean") return value ? "是" : "否";
  return LABELS[asText(value)] ? label(value) : asText(value);
};
const lines = (value: unknown) => {
  const span = asRecord(value);
  return span.start_line == null ? "定位未提供" : `第 ${asText(span.start_line)}–${asText(span.end_line)} 行`;
};
const lifecycle = (value: unknown) => {
  const state = asRecord(value);
  return state.revoked === true ? "已撤销" : state.superseded_by ? `已由 ${asText(state.superseded_by)} 替代` :
    state.registered === false ? "未登记" : state.revoked === false ? "当前版本" : "状态未提供";
};
const RecordDetails = ({ title, value, testId }: { title: string; value: unknown; testId?: string }) =>
  <details data-testid={testId} style={{ marginTop: 6 }}><summary style={{ cursor: "pointer", color: dark.textMuted }}>{title}</summary><Words value={value} /></details>;

/** React text nodes everywhere: never innerHTML, even for markdown or SVG sources. */
export function SourceDrafts({ sources, onChange, fixedPath = false, disabled = false, single = false, onBusy }: {
  sources: SourceDraft[]; onChange: (sources: SourceDraft[]) => void; fixedPath?: boolean; disabled?: boolean;
  single?: boolean; onBusy?: (busy: boolean) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);
  const latest = useRef(sources);
  const busyCallback = useRef(onBusy);
  busyCallback.current = onBusy;
  latest.current = sources;
  useEffect(() => () => { generation.current += 1; busyCallback.current?.(false); }, []);
  const update = (index: number, patch: Partial<SourceDraft>) => onChange(sources.map((item, i) => i === index ? { ...item, ...patch } : item));
  const importFiles = async (files: File[]) => {
    const run = ++generation.current;
    setError(null);
    setLoading(true);
    busyCallback.current?.(true);
    try {
      const imported = await Promise.all(files.map(async (file) => {
        const content = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result));
          reader.onerror = () => reject(new Error(`无法读取 ${file.name}`));
          reader.readAsText(file, "UTF-8");
        });
        return { path: `sources/${file.name}`, content, kind: /\.md$/i.test(file.name) ? "markdown" : "text" };
      }));
      if (run === generation.current) onChange(single ? imported.slice(0, 1) : [...latest.current, ...imported]);
    } catch (err) {
      if (run === generation.current) setError(String(err));
    } finally {
      if (run === generation.current) { setLoading(false); busyCallback.current?.(false); }
    }
  };
  return <section aria-label="来源资料" style={{ display: "grid", gap: 8 }}>
    {!fixedPath && <div>
      {!single && <button type="button" style={button} disabled={disabled || loading} onClick={() => onChange([...sources, { path: "sources/", content: "", kind: "markdown" }])}>添加来源</button>}
      <label style={{ marginLeft: 12 }}>导入来源文件 <input aria-label="导入来源文件" type="file" multiple={!single} accept=".md,.txt,.csv,.json,.html,.xml,.yaml,.yml,.log" disabled={disabled || loading} onChange={(event) => {
        const files = Array.from(event.target.files ?? []);
        event.target.value = "";
        void importFiles(files);
      }} /></label>
    </div>}
    {loading && <div role="status">正在导入来源…</div>}
    {error && <div role="alert">{error}</div>}
    {sources.map((source, index) => <fieldset key={index} style={panel} disabled={disabled}>
      <legend>来源 {index + 1}</legend>
      <label>来源路径 {index + 1}<input aria-label={`来源路径 ${index + 1}`} style={field} value={source.path} readOnly={fixedPath} onChange={(e) => update(index, { path: e.target.value })} /></label>
      <label>来源格式 {index + 1}<select aria-label={`来源格式 ${index + 1}`} style={field} value={source.kind} onChange={(e) => update(index, { kind: e.target.value })}>
        <option value="markdown">Markdown</option><option value="text">文本</option>
      </select></label>
      <label>来源正文 {index + 1}<textarea aria-label={`来源正文 ${index + 1}`} style={{ ...field, minHeight: 140 }} value={source.content} onChange={(e) => update(index, { content: e.target.value })} /></label>
      {!fixedPath && !single && <button type="button" style={button} onClick={() => onChange(sources.filter((_, i) => i !== index))}>移除来源 {index + 1}</button>}
    </fieldset>)}
  </section>;
}

interface Reading {
  citation: Json; requestId: string; offset: number; total?: number; metadata?: string;
  content: string; loading: boolean; complete: boolean; error: string | null; page?: Json;
}
const citationIdentity = (c: Json) => JSON.stringify([c.mission_id, c.result_id, c.receipt_id, c.citation_index]);
const immutableMetadata = (c: Json) => JSON.stringify([c.path, c.version, asRecord(c.locator).start_line, asRecord(c.locator).end_line]);
const blockMetadata = (c: Json) => JSON.stringify([
  c.version_hash, c.block_id, asRecord(c.display_block).start_line, asRecord(c.display_block).end_line,
  asList(c.parent_headings).map((h) => [h.level, h.start_line, h.end_line, h.text]),
  c.historical_verdict, c.source_trust, c.scope_limited_to_source, c.trust_marker, c.scope_marker,
]);
const validCitation = (c: Json, missionId: string) => c.mission_id === missionId && !!asText(c.citation_id) && !!asText(c.result_id) && !!asText(c.receipt_id) && Number.isInteger(c.citation_index) && Number(c.citation_index) >= 0;

export function MissionDocument({ missionId, document, channel, onChanged }: {
  missionId: string; document: Json; channel: Channel; onChanged: () => void;
}) {
  const [reading, setReading] = useState<Reading | null>(null);
  const current = useRef<Reading | null>(null);
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const [operation, setOperation] = useState<"register" | "supersede" | "revoke" | null>(null);
  const [drafts, setDrafts] = useState<SourceDraft[]>([]);
  const [expected, setExpected] = useState("");
  const [reason, setReason] = useState("");
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [sourceNotice, setSourceNotice] = useState<string | null>(null);
  const [sourcePending, setSourcePending] = useState(false);
  const [importing, setImporting] = useState(false);
  const sourceRequest = useRef<{ id: string; type: string } | null>(null);
  const sourceRetry = useRef<{ fingerprint: string; key: string } | null>(null);
  const readTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sourceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const publish = (next: Reading | null) => { current.current = next; setReading(next); };
  const clearReadTimer = () => { if (readTimer.current) clearTimeout(readTimer.current); readTimer.current = null; };
  const requestPage = (next: Reading) => {
    clearReadTimer();
    const c = next.citation;
    next = { ...next, requestId: newRequestKey(), loading: true };
    publish(next);
    const accepted = channel?.send({ type: "mission_citation_read", request_id: next.requestId, payload: {
      mission_id: missionId, result_id: c.result_id, receipt_id: c.receipt_id, citation_index: c.citation_index,
      offset: next.offset, limit: 65536,
    } });
    if (!accepted) { publish({ ...next, loading: false, error: "连接不可用，请重读引用" }); return; }
    const id = next.requestId;
    readTimer.current = setTimeout(() => {
      if (current.current?.requestId === id) publish({ ...current.current, requestId: "", loading: false, error: "引用读取超时，请重读" });
    }, 30000);
  };
  const openCitation = (citation: Json) => {
    if (!validCitation(citation, missionId)) return;
    requestPage({ citation, requestId: "", offset: 0, content: "", loading: true, complete: false, error: null });
  };

  useEffect(() => {
    publish(null);
    setOperation(null); setDrafts([]); setReason(""); setExpected(""); setSourceError(null); setSourceNotice(null);
    sourceRetry.current = null;
  }, [missionId]);

  useEffect(() => {
    const disconnect = () => {
      clearReadTimer();
      if (current.current) publish({ ...current.current, requestId: "", loading: false, complete: false, error: "连接已变化，请重读引用" });
      if (sourceRequest.current) setSourceError("连接已变化，来源操作结果未知；刷新后可用原请求键重试");
      if (sourceTimer.current) clearTimeout(sourceTimer.current);
      sourceRequest.current = null; setSourcePending(false);
    };
    disconnect();
    if (!channel) return;
    const off = channel.onMessage((incoming) => {
      const message = incoming as unknown as { type: string; payload: unknown };
      const payload = asRecord(message.payload);
      const id = asText(payload.request_id);
      if (message.type === "mission_citation_read_response") {
        const previous = current.current;
        if (!previous || !previous.loading || previous.requestId !== id || previous.citation.mission_id !== missionId) return;
        clearReadTimer();
        if (payload.ok !== true) { publish({ ...previous, requestId: "", loading: false, error: `${asText(payload.error_code) || "read_failed"}：${asText(payload.error)}` }); return; }
        const page = asRecord(payload.data);
        const fail = () => publish({ ...previous, requestId: "", loading: false, error: "引用分页身份或完整性不一致" });
        if (page.citation_id !== previous.citation.citation_id || citationIdentity(page) !== citationIdentity(previous.citation) ||
            immutableMetadata(page) !== immutableMetadata(previous.citation) ||
            page.version_hash !== page.version || !asText(page.block_id) ||
            !Number.isInteger(asRecord(page.display_block).start_line) || !Number.isInteger(asRecord(page.display_block).end_line) ||
            (previous.metadata !== undefined && previous.metadata !== blockMetadata(page))) { fail(); return; }
        const total = page.total_chars;
        const next = page.next_offset;
        const pageLength = typeof page.text === "string" ? Array.from(page.text).length : -1;
        const end = previous.offset + pageLength;
        if (page.offset !== previous.offset || !Number.isInteger(total) || Number(total) < 0 ||
            (previous.total !== undefined && total !== previous.total) || pageLength < 0 || end > Number(total) ||
            (next === null ? end !== total : !Number.isInteger(next) || next !== end || Number(next) <= previous.offset || Number(next) >= Number(total))) { fail(); return; }
        const updated: Reading = { ...previous, page, metadata: blockMetadata(page), total: Number(total),
          content: previous.content + asText(page.text), offset: end, loading: false, complete: next === null, error: null };
        if (next === null) publish(updated);
        else requestPage(updated);
      } else if (sourceRequest.current?.id === id && message.type === `${sourceRequest.current.type}_response`) {
        sourceRequest.current = null;
        if (sourceTimer.current) clearTimeout(sourceTimer.current);
        setSourcePending(false);
        if (payload.ok === true) {
          setOperation(null); setSourceError(null); setSourceNotice("来源请求已处理，请查看来源版本与审批记录。"); sourceRetry.current = null;
          changed.current();
        } else setSourceError(`${asText(payload.error_code) || "request_failed"}：${asText(payload.error)}`);
      }
    });
    const offState = channel.onStateChange?.(disconnect);
    return () => { off(); offState?.(); clearReadTimer(); if (sourceTimer.current) clearTimeout(sourceTimer.current); };
    // The subscription owns the channel/mission lifetime. Mutable read/request state
    // lives in refs; parent projection refreshes must not restart an in-flight read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel, missionId]);

  const startSource = (op: "register" | "supersede" | "revoke", source?: Json) => {
    setOperation(op); setReason(""); setSourceError(null); setSourceNotice(null);
    setExpected(asText(source?.version_hash));
    setDrafts([{ path: asText(source?.path) || "sources/", content: "", kind: asText(source?.kind) || "markdown" }]);
  };
  const submitSource = () => {
    if (!operation || sourcePending || !channel || !drafts[0]) return;
    const source = drafts[0];
    const payload: Json = { mission_id: missionId, path: source.path, ...(operation === "revoke" ? { reason } : { content: source.content, kind: source.kind }),
      ...(operation !== "register" ? { expected_version_hash: expected } : {}) };
    const fingerprint = JSON.stringify([operation, payload]);
    if (sourceRetry.current?.fingerprint !== fingerprint) sourceRetry.current = { fingerprint, key: newRequestKey() };
    payload.idempotency_key = sourceRetry.current.key;
    const request = { id: newRequestKey(), type: `mission_source_${operation}` };
    sourceRequest.current = request; setSourcePending(true); setSourceError(null);
    if (!channel.send({ type: request.type, request_id: request.id, payload })) {
      sourceRequest.current = null; setSourcePending(false); setSourceError("连接不可用，来源请求未发送"); return;
    }
    sourceTimer.current = setTimeout(() => {
      if (sourceRequest.current?.id === request.id) { sourceRequest.current = null; setSourcePending(false); setSourceError("来源操作超时，结果未知；刷新后可用原请求键重试"); }
    }, 30000);
  };

  const claims = asList(document.claims);
  const reviews = asList(document.reviews);
  return <section aria-label="文档报告" style={{ display: "grid", gap: 12 }}>
    <div style={panel}>
      <strong>文档任务</strong> · {asText(asRecord(document.domain).id)} · 版本 {asText(asRecord(document.domain).version)}
      <div data-testid="document-result">Mission 判定：{label(document.result)}</div>
      <h3>成功条件与实际覆盖</h3>
      {asList(document.criteria).map((criterion, index) => <div key={index} style={{ marginTop: 8 }}>
        <strong>{asText(criterion.text)}</strong><div>覆盖归类：{label(criterion.verdict)}</div>
        {typeof asRecord(criterion.final_judgment).met === "boolean" && <div>
          实际判定：{asRecord(criterion.final_judgment).met === true ? "满足" : "未满足"} · {label(asRecord(criterion.final_judgment).judge)}
        </div>}
        {Array.isArray(criterion.reasons) && criterion.reasons.length > 0 && <div>{readable(criterion.reasons)}</div>}
        <RecordDetails title={`条件 ${index + 1} 的覆盖记录`} value={criterion} />
      </div>)}
    </div>
    <section aria-label="系统结论" style={panel}>
      <h3>系统结论</h3>
      {!claims.length && <p>暂无正式 Claim</p>}
      {claims.map((claim, index) => <article key={asText(claim.id) || index} data-testid={`document-claim-${asText(claim.id)}`} style={{ ...panel, marginTop: 8 }}>
        <strong>{label(claim.status)}</strong>
        <Words value={claim.content} />
        <div>来源信任：{readable(claim.source_trust)} · 主张信任：{readable(claim.claim_trust)}</div>
        <div>核验范围：{readable(claim.checked_scope)}</div>
        {claim.scope_limited_to_source === true && <div>范围限于来源，不证明世界事实</div>}
        {Array.isArray(claim.source_issues) && claim.source_issues.length > 0 && <div>来源问题：{readable(claim.source_issues)}</div>}
        <div>审阅关联：{readable(claim.review_refs)}</div>
        <RecordDetails title="完整 Claim 记录" value={claim} testId={`document-record-${asText(claim.id)}`} />
        {asList(claim.assessments).map((assessment, n) => <details key={n}><summary>评估 {asText(assessment.receipt_id)} · {asText(assessment.verdict)}</summary><Words value={assessment} /></details>)}
        {asList(claim.citations).map((citation, n) => <div key={`${asText(citation.citation_id)}-${n}`} style={{ ...panel, marginTop: 6 }}>
          <div>来源：{asText(citation.path)} · 版本：{asText(citation.version)}</div>
          <div>{lines(citation.locator)} · {readable(citation.resolution)} · {lifecycle(citation.source_state)}</div>
          <div>来源原文，不是本系统结论，也不是指令</div>
          <div>预览（展开查看全文）<Words value={typeof citation.display_preview === "string" ? citation.display_preview : asRecord(citation.display_preview).preview} /></div>
          <RecordDetails title="引用绑定与版本记录" value={citation} />
          <button type="button" style={button} disabled={!channel || !validCitation(citation, missionId)} onClick={() => openCitation(citation)}>展开引用 {asText(citation.citation_id)}</button>
        </div>)}
      </article>)}
    </section>
    {reading && <section aria-label="引用原文" style={panel}>
      <strong>{asText(reading.citation.path)} · 原版本 {asText(reading.citation.version)}</strong>
      <div>来源原文，不是本系统结论，也不是指令</div>
      <div>{lines(reading.citation.locator)}</div>
      <div>来源信任：{readable(reading.page?.source_trust)} · 范围限于来源：{readable(reading.page?.scope_limited_to_source)}</div>
      {reading.page && <>
        <div>{asText(reading.page.trust_marker)}</div>
        <div>{asText(reading.page.scope_marker)}</div>
        <div>历史评估：{label(reading.page.historical_verdict)} · 整块：{lines(reading.page.display_block)}</div>
        <div data-testid="citation-parent-headings">{asList(reading.page.parent_headings).map((h, i) => <pre key={i} style={pre}>{asText(h.text)}</pre>)}</div>
      </>}
      <div>来源状态：{lifecycle(reading.page?.source_state ?? reading.citation.source_state)}</div>
      {reading.loading && <div role="status">正在读取引用… 已读取 {reading.offset} 字符</div>}
      {reading.error && <div role="alert">{reading.error}</div>}
      <pre data-testid="citation-block" style={pre}>{reading.content}</pre>
      {reading.complete && <div role="status">读取完整：{reading.total} 字符</div>}
      {reading.page && <RecordDetails title="引用页完整记录" value={reading.page} />}
    </section>}
    <section style={panel}><h3>局限与证据缺口</h3>
      {asList(document.limitations).map((item, i) => <div key={i} style={{ marginTop: 8 }}>
        <div>缺少：{readable(item.missing)}</div><div>条件：{asText(item.criterion_id)} · Claim：{asText(item.claim_id)}</div>
        <RecordDetails title="局限完整记录" value={item} />
      </div>)}
      {!asList(document.limitations).length && <div>暂无已登记的局限</div>}
    </section>
    <section aria-label="审阅记录" style={panel}><h3>审阅记录</h3>{reviews.length ? reviews.map((review, i) => <div key={i} style={{ marginTop: 8 }}>
      <div>{asText(review.request_id)} · {label(review.state)}</div>
      <div>审阅人：{readable(review.decided_by ?? review.rejected_by ?? review.granted_by)}</div>
      <RecordDetails title="审阅完整记录" value={review} />
    </div>) : <p>暂无审阅记录</p>}</section>
    <section style={panel}><RecordDetails title={`全部评估记录（${asList(document.assessments).length}）`} value={document.assessments} /></section>
    <section style={panel}><RecordDetails title={`诊断详情（${asList(document.diagnostics).length}）`} value={document.diagnostics} testId="document-diagnostics" /></section>
    <section aria-label="来源版本" style={panel}>
      <h3>来源版本与生命周期</h3>
      {asList(document.sources).map((source, i) => <div key={i} style={{ ...panel, marginBottom: 8 }}>
        <strong>{asText(source.path)}</strong><div>版本：{asText(source.version_hash)}</div>
        <div>{lifecycle(source)} · {readable(source.trust)} · 修订 {asText(source.revision)}</div>
        <RecordDetails title="来源完整记录" value={source} />
        {source.revoked !== true && !source.superseded_by && <div>
          <button type="button" style={button} disabled={!channel || sourcePending} onClick={() => startSource("supersede", source)}>替代来源 {asText(source.path)} {asText(source.version_hash)}</button>
          <button type="button" style={button} disabled={!channel || sourcePending} onClick={() => startSource("revoke", source)}>撤销来源 {asText(source.path)} {asText(source.version_hash)}</button>
        </div>}
      </div>)}
      <button type="button" style={button} disabled={!channel || sourcePending} onClick={() => startSource("register")}>登记新来源</button>
      {sourceError && <div role="alert">{sourceError}</div>}
      {sourceNotice && <div role="status">{sourceNotice}</div>}
      {operation && <div style={{ marginTop: 12 }}>
        <div>{operation} · 绑定旧版本：{expected || "新来源"}</div>
        {operation === "revoke" ? <label>撤销来源理由<textarea aria-label="撤销来源理由" style={field} value={reason} disabled={sourcePending} onChange={(e) => setReason(e.target.value)} /></label> :
          <SourceDrafts sources={drafts} onChange={setDrafts} fixedPath={operation === "supersede"} disabled={sourcePending} single onBusy={setImporting} />}
        <button type="button" style={button} disabled={sourcePending || importing || !channel || !drafts[0]?.path || drafts[0]?.path === "sources/" || (operation === "revoke" ? !reason.trim() : !drafts[0]?.content) || drafts.length !== 1} onClick={submitSource}>提交来源变更</button>
        <button type="button" style={button} disabled={sourcePending} onClick={() => setOperation(null)}>取消来源变更</button>
        {sourcePending && <div role="status">来源请求处理中…</div>}
      </div>}
    </section>
  </section>;
}
