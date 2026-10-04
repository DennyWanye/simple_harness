// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import React, { useEffect, useRef, useState } from "react";
import { asList, asRecord, asText, newRequestKey, type MissionsChannel } from "../stores/missionsStore";

type Json = Record<string, unknown>;
type Props = { missionId: string; channel: MissionsChannel | null };
const channelKeys = new WeakMap<object, number>();
let nextChannelKey = 0;

export const MissionDiagnostics: React.FC<Props> = (props) => {
  const { channel, missionId } = props;
  if (channel && !channelKeys.has(channel)) channelKeys.set(channel, ++nextChannelKey);
  // Replace the entire read session on selection/connection-object changes. This also
  // prevents a previous Mission's report flashing during an effect-driven reset.
  return <DiagnosticsSession key={`${missionId}:${channel ? channelKeys.get(channel) : "none"}`} {...props} />;
};

/** Selected-Mission read/export only. Never asks to run or reevaluate work. */
const DiagnosticsSession: React.FC<Props> = ({ missionId, channel }) => {
  const [report, setReport] = useState<Json | null>(null);
  const [receipt, setReceipt] = useState<Json | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [stale, setStale] = useState(false);
  const pending = useRef<{ id: string; type: string; changed: boolean } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    pending.current = null;
    const clear = () => {
      pending.current = null; setBusy(false);
      if (timer.current) clearTimeout(timer.current);
    };
    const off = channel?.onMessage((raw) => {
      const message = raw as unknown as { type?: string; payload?: unknown };
      const body = asRecord(message.payload);
      if (message.type === "mission_changed" && body.mission_id === missionId) {
        if (pending.current) pending.current.changed = true;
        setStale(true); return;
      }
      const request = pending.current;
      if (!request || body.request_id !== request.id || message.type !== `${request.type}_response`) return;
      const data = asRecord(body.data);
      if (body.ok === true && data.mission_id !== missionId) return;
      clear();
      if (body.ok !== true) {
        setError(asText(body.error) || "任务诊断请求失败"); return;
      }
      setError("");
      if (request.type === "mission_diagnostics") { setReport(data); setStale(request.changed); }
      else setReceipt(data);
    });
    const offState = channel?.onStateChange?.(() => {
      clear(); setReport(null); setReceipt(null); setError("连接已变化，请重新读取当前任务");
    });
    return () => { off?.(); offState?.(); pending.current = null; if (timer.current) clearTimeout(timer.current); };
  }, [channel, missionId]);

  const request = (type: string) => {
    if (!channel || pending.current) return;
    const id = newRequestKey(); pending.current = { id, type, changed: false }; setBusy(true); setError("");
    if (!channel.send({ type, request_id: id, payload: { mission_id: missionId } })) {
      pending.current = null; setBusy(false); setError("连接不可用，请求未发送"); return;
    }
    timer.current = setTimeout(() => {
      if (pending.current?.id === id) {
        pending.current = null; setBusy(false); setError("读取超时，可重试当前任务；本操作不会重新执行任务");
      }
    }, 30000);
  };
  const replay = asRecord(report?.replay);
  const counts = asRecord(replay.counts);
  const count = (key: string) => (typeof counts[key] === "number" ? counts[key] : 0) as number;
  const failures = asList(report?.failure_timeline);
  const attribution = asRecord(report?.attribution);
  const cost = asRecord(attribution.cost);
  const total = asRecord(cost.total);
  const usage = asRecord(asRecord(report?.costs).usage);
  const ledger = asRecord(cost.ledger);
  const unknownRows = typeof cost.unknown_usage_rows === "number" ? cost.unknown_usage_rows : null;
  const incomplete = cost.reconciled !== true || unknownRows !== 0 ||
    usage.reserved_tokens !== 0 || ledger.unsettled_usage_tokens !== 0;
  return <section aria-label="任务回放与支持报告" style={{ marginBlock: 16 }}>
    <h3>任务回放与支持报告</h3>
    <p>只读取当前任务的历史，不调用模型或重新执行任务。支持报告保存在本机。</p>
    <button type="button" disabled={busy || !channel} onClick={() => request("mission_diagnostics")}>查看回放与贡献</button>{" "}
    <button type="button" disabled={busy || !channel} onClick={() => request("mission_support_export")}>生成脱敏支持报告</button>
    {busy && <p role="status">正在读取当前任务…</p>}
    {error && <p role="alert">{error}</p>}
    {report && <div>
      {stale && <p>任务已有新事件，请重新读取诊断快照。</p>}
      <p>任务状态：{asText(attribution.mission_status) || "未知"}</p>
      {replay.status === "OUT_OF_SCOPE"
        ? <p>重建结果：这个任务是按旧口径建的，不在重建核对范围内。</p>
        : <p>重建结果：一致 {count("CONSISTENT")} 张表 · 不一致 {count("INCONSISTENT")} 张。</p>}
      <p>证据链缺口 {asList(attribution.breaks).length} 项。重建一致不代表任务交付成功。</p>
      <p>记录用量：{asText(total.tokens) || "未知"} tokens</p>
      <p>预留：{usage.reserved_tokens == null ? "未知" : asText(usage.reserved_tokens)} tokens · 未知用量记录（已入账）：{unknownRows == null ? "未知" : unknownRows} · 账本核对：{cost.reconciled === true ? "一致" : "未对齐或不可用"}</p>
      <p>待结算用量：{ledger.unsettled_usage_tokens == null ? "未知" : asText(ledger.unsettled_usage_tokens)} tokens。{incomplete ? "记录不完整，以上已记录用量不能作为最终总消耗。" : "仅表示当前账本记录，不是供应商独立账单。"}</p>
      <p>未知记录数只统计已入账数据，不包含尚未入账的在途调用。</p>
      <h4>尝试与贡献</h4>
      {asList(attribution.attempts).map((attempt) => <div key={asText(attempt.attempt_id)} style={{ marginBlock: 8 }}>
        <div>{asText(attempt.task_id)} / {asText(attempt.attempt_id)}</div>
        <div>{asText(attempt.role)} · {asText(attempt.model)} · {asText(attempt.status)} · {attempt.on_success_path === true ? "进入交付路径" : "探索或未采用"}</div>
        <div>执行 {asText(asRecord(attempt.work).tokens)} tokens · 验证 {asText(asRecord(attempt.verification).tokens)} tokens</div>
      </div>)}
      <h4>失败过程</h4>
      {failures.length === 0 ? <p>没有记录到失败过程。</p> : failures.map((event) => <details key={`${asText(event.seq)}:${asText(event.type)}`}>
        <summary>#{asText(event.seq)} {asText(event.type)} · {asText(event.task_id)}</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(event.detail, null, 2)}</pre>
      </details>)}
      <details><summary>查看完整诊断与覆盖范围</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 400, overflow: "auto" }}>{JSON.stringify(report, null, 2)}</pre></details>
    </div>}
    {receipt && <div aria-label="本地支持报告回执" style={{ overflowWrap: "anywhere" }}>
      <p>已保存：{asText(receipt.path)}</p>
      <p>SHA-256：{asText(receipt.sha256)} · {asText(receipt.size_bytes)} bytes</p>
    </div>}
  </section>;
};
