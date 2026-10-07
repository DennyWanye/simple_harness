// SPDX-License-Identifier: BUSL-1.1
import { useEffect, useRef, useState } from "react";
import { asRecord, newRequestKey, type MissionsChannel } from "../stores/missionsStore";
import { errorMessage, KIND_LABELS, USE_LABELS, type AssuranceError,
  type AssuranceItem, type AssuranceReview, type AssuranceSnapshot, type AssuranceUseCheck } from "../stores/assuranceStore";
import "./MissionTaskGraph.css";

type Props = { missionId: string; channel: MissionsChannel | null };
type Kind = "mission_assurance_snapshot" | "mission_assurance_review" | "mission_assurance_use_check";
type Pending = { id: string; kind: Kind; seq: number | null; reviewKey?: string; append: boolean; changed: boolean };
const channelIds = new WeakMap<object, number>();
let lastChannelId = 0;

export function MissionAssurance(props: Props) {
  if (props.channel && !channelIds.has(props.channel)) channelIds.set(props.channel, ++lastChannelId);
  return <AssuranceSession key={props.missionId + ":" + (props.channel ? channelIds.get(props.channel) : "none")} {...props} />;
}

function AssuranceSession({ missionId, channel }: Props) {
  const [page, setPage] = useState<AssuranceSnapshot | null>(null);
  const [items, setItems] = useState<AssuranceItem[]>([]);
  const [review, setReview] = useState<AssuranceReview | null>(null);
  const [useCheck, setUseCheck] = useState<AssuranceUseCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [busy, setBusy] = useState(false);
  const [seqText, setSeqText] = useState("");
  const [subjectId, setSubjectId] = useState("");
  const [subjectRevision, setSubjectRevision] = useState("");
  const [subjectHash, setSubjectHash] = useState("");
  const pending = useRef<Pending | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shownItems = useRef(items);
  shownItems.current = items;
  const shownPage = useRef(page);
  shownPage.current = page;
  const clear = () => { pending.current = null; setBusy(false); if (timer.current) clearTimeout(timer.current); timer.current = null; };
  const reset = () => { clear(); setPage(null); setItems([]); setReview(null); setUseCheck(null); setError(null); setStale(false); };

  useEffect(() => {
    const off = channel?.onMessage(raw => {
      const message = raw as unknown as { type?: string; payload?: unknown };
      const body = asRecord(message.payload);
      if (message.type === "mission_changed" && body.mission_id === missionId) {
        if (pending.current) pending.current.changed = true;
        setStale(true); return;
      }
      const request = pending.current;
      if (!request || body.request_id !== request.id || message.type !== request.kind + "_response") return;
      clear();
      // 形状已由控制通道按公开合同核过（ws/orchestrationContracts.ts）；不合合同的回复到这里
      // 已是 ok:false 的协议错，下面只核"是不是这次请求的回复"，并保留上次画面。
      if (body.ok !== true) {
        let text = typeof body.error === "string" ? body.error : "读取失败，请重试";
        if (body.assurance_error !== undefined) {
          const wire = body.assurance_error as AssuranceError;
          if (wire.request_id !== request.id) text = "错误回执对应了其他请求，请重新读取";
          else {
            text = errorMessage(wire);
            if (wire.code === "SNAPSHOT_CHANGED") { setPage(null); setItems([]); }
          }
        }
        setError(text); return;
      }
      try {
        if (asRecord(body.data).mission_id !== missionId) throw new Error("返回了其他任务的 Assurance 数据");
        if (request.kind === "mission_assurance_snapshot") {
          const value = body.data as AssuranceSnapshot;
          if (value.request_id !== request.id) throw new Error("返回了其他请求的快照");
          if (request.seq === null ? value.view !== "CURRENT" : value.view !== "HISTORY" || value.snapshot_seq !== request.seq) {
            throw new Error("返回的视图与请求不符");
          }
          if (request.append && shownPage.current && value.snapshot_seq !== shownPage.current.snapshot_seq) throw new Error("分页跨越了不同状态，请从第一页重新读取");
          const merged = request.append ? [...shownItems.current, ...value.items] : value.items;
          const keys = merged.map(item => item.kind + ":" + item.id);
          if (new Set(keys).size !== keys.length) throw new Error("分页出现重复条目，请从第一页重新读取");
          setPage(value); setItems(merged); setError(null);
          if (value.view === "CURRENT") setStale(request.changed);
        } else if (request.kind === "mission_assurance_review") {
          const value = body.data as AssuranceReview;
          if (value.review_key !== request.reviewKey) throw new Error("返回了其他审阅的详情");
          setReview(value); setError(null);
          if (request.changed) setStale(true);
        } else {
          const value = body.data as AssuranceUseCheck;
          if (value.request_id !== request.id) throw new Error("返回了其他请求的核查结果");
          setUseCheck(value); setError(null);
          if (request.changed) setStale(true);
        }
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Assurance 响应无效");
      }
    });
    const offState = channel?.onStateChange?.(() => { reset(); });
    return () => { off?.(); offState?.(); pending.current = null; if (timer.current) clearTimeout(timer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel, missionId]);

  function send(kind: Kind, payload: Record<string, unknown>, detail: Partial<Pending> = {}) {
    if (!channel || pending.current) return;
    const id = newRequestKey();
    pending.current = { id, kind, seq: null, append: false, changed: false, ...detail }; setBusy(true);
    if (!channel.send({ type: kind, request_id: id, payload: { schema_version: 1, request_id: id, mission_id: missionId, ...payload } })) {
      clear(); setError("连接不可用，请求未发送"); return;
    }
    timer.current = setTimeout(() => {
      if (pending.current?.id === id) { clear(); setError("读取超时，已保留上次画面"); }
    }, 30000);
  }
  function seqOf(value: string): number | null {
    if (!/^\d+$/.test(value.trim())) return null;
    const result = Number(value); return Number.isSafeInteger(result) && result >= 0 ? result : null;
  }
  function readSnapshot(cursor: string | null) {
    const seq = seqText.trim() ? seqOf(seqText) : null;
    if (seqText.trim() && seq === null) { setError("请输入有效的事件序号"); return; }
    send("mission_assurance_snapshot", { view: seq === null ? "CURRENT" : "HISTORY", at_event_seq: seq, cursor, limit: 100 },
      { seq, append: cursor !== null });
  }
  function readReview(reviewKey: string) {
    send("mission_assurance_review", { review_key: reviewKey, cursor: null, limit: 100 }, { reviewKey });
  }
  function checkUse() {
    const revision = seqOf(subjectRevision);
    if (!subjectId.trim() || revision === null || !/^[a-f0-9]{64}$/.test(subjectHash.trim())) {
      setError("请填写结果编号、版本号和 64 位十六进制内容摘要"); return;
    }
    const seq = seqText.trim() ? seqOf(seqText) : null;
    send("mission_assurance_use_check", {
      subject_ref: { kind: "result", pin: { id: subjectId.trim(), revision, content_hash: subjectHash.trim() } },
      view: seq === null ? "CURRENT" : "HISTORY", at_event_seq: seq,
    }, { seq });
  }
  const historical = page?.view === "HISTORY";
  return <section className="taskgraph assurance" aria-label="Assurance 保证视图">
    <div className="tg-heading"><h3>保证状态</h3><span>只读 · 不发放使用许可</span></div>
    <div className="tg-toolbar">
      <label>历史事件序号 <input aria-label="Assurance 历史事件序号" value={seqText} onChange={e => setSeqText(e.target.value)} placeholder="留空查看当前" inputMode="numeric" /></label>
      <button disabled={busy || !channel} onClick={() => readSnapshot(null)}>{busy ? "读取中…" : "读取保证状态"}</button>
      {page?.next_cursor && <button disabled={busy || !channel} onClick={() => readSnapshot(page.next_cursor)}>下一页</button>}
    </div>
    {error && <p role="alert">{error}</p>}
    {stale && <p role="status">任务已变化，画面尚未更新。刷新后再判断当前可用性。</p>}
    {page && <>
      <p>{(historical ? "历史视图（钉在事件 " + page.snapshot_seq + "）· 当前可用性按现行授权判断" : "当前视图 · 事件 " + page.snapshot_seq)
        + " · 根 " + page.root_incarnation_id + (page.truncated ? " · 尚有后续页" : "")}</p>
      <table><thead><tr><th>类别</th><th>对象</th><th>历史状态</th><th>当前可用性</th><th>原因</th><th>证据</th></tr></thead>
        <tbody>{items.map(item => <tr key={item.kind + ":" + item.id}>
          <td>{KIND_LABELS[item.kind] || item.kind}</td>
          <td>{item.kind === "REVIEW"
            ? <button disabled={busy || !channel} onClick={() => readReview(item.id)}>{item.id}</button>
            : item.id}{item.artifact_ref && <><br /><small>{"产物 " + item.artifact_ref.pin.id + " @ " + item.artifact_ref.pin.revision}</small></>}</td>
          <td>{item.history_state}</td>
          <td>{USE_LABELS[item.current_use] || item.current_use}</td>
          <td>{item.reason_codes.join(" · ")}</td>
          <td>{item.evidence_count}</td>
        </tr>)}</tbody></table>
      {!items.length && <p>本次读取中没有条目。</p>}
    </>}
    {review && <div className="tg-explanation">
      <strong>{"审阅 " + review.review_key + " · " + review.purpose + " · " + review.official_status
        + (review.verdict ? " · 判定 " + review.verdict : "") + " · " + (USE_LABELS[review.current_use] || review.current_use)}</strong>
      <p>{"审阅包 " + review.package_ref.pin.id + " @ " + review.package_ref.pin.revision
        + (review.record_ref ? " · 记录 " + review.record_ref.pin.id + " @ " + review.record_ref.pin.revision : " · 尚无正式记录")}</p>
      {!review.assessments.length && <p>该审阅没有可显示的评估条目。</p>}
      {review.assessments.length > 0 && <table><thead><tr><th>准则</th><th>模型评分</th><th>生效评分</th><th>检查门</th><th>原因</th><th>证据</th></tr></thead>
        <tbody>{review.assessments.map(a => <tr key={a.criterion_id}><td>{a.criterion_id}</td><td>{a.model_grade}</td><td>{a.effective_grade}</td>
          <td>{a.check_gate}</td><td>{a.reason_codes.join(" · ")}</td><td>{a.evidence_labels.length}</td></tr>)}</tbody></table>}
      {review.truncated && <p>评估条目超过一页，此处只显示第一页。</p>}
    </div>}
    <details><summary>核查一项结果的当前可用性（仅诊断）</summary><div className="tg-toolbar">
      <input aria-label="结果编号" value={subjectId} onChange={e => setSubjectId(e.target.value)} placeholder="结果编号" />
      <input aria-label="结果版本" value={subjectRevision} onChange={e => setSubjectRevision(e.target.value)} placeholder="版本" inputMode="numeric" />
      <input aria-label="结果内容摘要" value={subjectHash} onChange={e => setSubjectHash(e.target.value)} placeholder="SHA-256 内容摘要" />
      <button disabled={busy || !channel} onClick={checkUse}>核查</button>
    </div>
    {useCheck && <p>{(USE_LABELS[useCheck.decision] || useCheck.decision) + " · 用途 " + useCheck.purpose + " · 覆盖 " + useCheck.coverage
      + (useCheck.reason_codes.length ? " · " + useCheck.reason_codes.join(" · ") : "")
      + (useCheck.expires_at_ms !== null ? " · 有效至 " + new Date(useCheck.expires_at_ms).toLocaleString() : "")
      + " · 仅诊断，不构成使用许可"}</p>}
    </details>
  </section>;
}
