// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/** A Mission's reference material: drafts for creation, and the registered versions
 * with their register / supersede / revoke requests.  Source text is data, never an
 * instruction, and is rendered only as React text.
 *
 * 2026-10-02 (strict citation option A): the document panel's citation review is gone;
 * what remains is the source lifecycle every Mission with material needs.
 */
import React, { useEffect, useRef, useState } from "react";
import { asText, newRequestKey, type MissionsChannel } from "../stores/missionsStore";
import { dark } from "../theme/components";
import { tokens } from "../theme/tokens";

type Json = Record<string, unknown>;
type Channel = MissionsChannel | null;
export interface SourceDraft { path: string; content: string; kind: string }
const panel: React.CSSProperties = { padding: 12, borderRadius: 8, border: `1px solid ${tokens.color.surface.hairline}`, background: dark.inset, overflowWrap: "anywhere" };
const field: React.CSSProperties = { width: "100%", boxSizing: "border-box", background: dark.inset, color: dark.text, border: `1px solid ${tokens.color.surface.hairline}`, padding: 8, borderRadius: 6 };
const button: React.CSSProperties = { background: dark.panel, color: dark.text, border: `1px solid ${tokens.color.surface.hairline}`, borderRadius: 6, padding: "6px 10px", cursor: "pointer" };
const lifecycle = (source: Json) => source.revoked === true ? "已撤销" : source.superseded_by ? "已被新版本替代" : "当前版本";

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

export function MissionSources({ missionId, sources, channel, onChanged }: {
  missionId: string; sources: Json[]; channel: Channel; onChanged: () => void;
}) {
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
  const sourceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setOperation(null); setDrafts([]); setReason(""); setExpected(""); setSourceError(null); setSourceNotice(null);
    sourceRetry.current = null;
  }, [missionId]);

  useEffect(() => {
    const disconnect = () => {
      if (sourceRequest.current) setSourceError("连接已变化，资料操作结果未知；刷新后可用原请求重试");
      if (sourceTimer.current) clearTimeout(sourceTimer.current);
      sourceRequest.current = null; setSourcePending(false);
    };
    disconnect();
    if (!channel) return;
    const off = channel.onMessage((incoming) => {
      const message = incoming as unknown as { type: string; payload: unknown };
      const payload = (message.payload ?? {}) as Json;
      const id = asText(payload.request_id);
      if (sourceRequest.current?.id === id && message.type === `${sourceRequest.current.type}_response`) {
        sourceRequest.current = null;
        if (sourceTimer.current) clearTimeout(sourceTimer.current);
        setSourcePending(false);
        if (payload.ok === true) {
          setOperation(null); setSourceError(null); setSourceNotice("资料变更已提交，请在下方查看版本与审批记录。"); sourceRetry.current = null;
          changed.current();
        } else setSourceError(`${asText(payload.error_code) || "request_failed"}：${asText(payload.error)}`);
      }
    });
    const offState = channel.onStateChange?.(disconnect);
    return () => { off(); offState?.(); if (sourceTimer.current) clearTimeout(sourceTimer.current); };
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
      sourceRequest.current = null; setSourcePending(false); setSourceError("连接不可用，资料请求未发送"); return;
    }
    sourceTimer.current = setTimeout(() => {
      if (sourceRequest.current?.id === request.id) { sourceRequest.current = null; setSourcePending(false); setSourceError("资料操作超时，结果未知；刷新后可用原请求重试"); }
    }, 30000);
  };
  const OPERATION_LABEL = { register: "登记新资料", supersede: "替换为新版本", revoke: "撤销资料" } as const;

  return <section aria-label="参考资料" style={panel}>
    <h3>参考资料</h3>
    {sources.map((source, i) => <div key={i} style={{ ...panel, marginBottom: 8 }}>
      <strong>{asText(source.path)}</strong>
      <div>{lifecycle(source)} · 第 {asText(source.revision)} 版 · 版本 {asText(source.version_hash).slice(0, 12)}</div>
      {source.revoked !== true && !source.superseded_by && <div>
        <button type="button" style={button} disabled={!channel || sourcePending} onClick={() => startSource("supersede", source)}>替换 {asText(source.path)}</button>
        <button type="button" style={button} disabled={!channel || sourcePending} onClick={() => startSource("revoke", source)}>撤销 {asText(source.path)}</button>
      </div>}
    </div>)}
    <button type="button" style={button} disabled={!channel || sourcePending} onClick={() => startSource("register")}>登记新资料</button>
    {sourceError && <div role="alert">{sourceError}</div>}
    {sourceNotice && <div role="status">{sourceNotice}</div>}
    {operation && <div style={{ marginTop: 12 }}>
      <div>{OPERATION_LABEL[operation]}{expected ? ` · 原版本 ${expected.slice(0, 12)}` : ""}</div>
      {operation === "revoke" ? <label>撤销理由<textarea aria-label="撤销理由" style={field} value={reason} disabled={sourcePending} onChange={(e) => setReason(e.target.value)} /></label> :
        <SourceDrafts sources={drafts} onChange={setDrafts} fixedPath={operation === "supersede"} disabled={sourcePending} single onBusy={setImporting} />}
      <button type="button" style={button} disabled={sourcePending || importing || !channel || !drafts[0]?.path || drafts[0]?.path === "sources/" || (operation === "revoke" ? !reason.trim() : !drafts[0]?.content) || drafts.length !== 1} onClick={submitSource}>提交</button>
      <button type="button" style={button} disabled={sourcePending} onClick={() => setOperation(null)}>取消</button>
      {sourcePending && <div role="status">资料请求处理中…</div>}
    </div>}
  </section>;
}
