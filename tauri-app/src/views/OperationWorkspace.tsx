// SPDX-License-Identifier: BUSL-1.1
import { useEffect, useRef, useState } from "react";
import { asList, asRecord, asText, newRequestKey, type MissionsChannel } from "../stores/missionsStore";

type Json = Record<string, unknown>;
type Effect = { key: string; criteria: string[]; obligation: string; milestone: string };

/** Explicit user confirmation; all authority and stale checks stay in the SDK. */
export function OperationWorkspace({ value, channel, onChanged }: {
  value: unknown; channel: MissionsChannel | null; onChanged: () => void;
}) {
  const workspace = asRecord(value);
  if (!workspace.mission_id) return null;
  return <Workspace key={`${workspace.mission_id}:${asRecord(workspace.requirements_ref).content_hash}`}
    workspace={workspace} channel={channel} onChanged={onChanged} />;
}

function Workspace({ workspace: w, channel, onChanged }: {
  workspace: Json; channel: MissionsChannel | null; onChanged: () => void;
}) {
  const [content, setContent] = useState<string[]>([]);
  const [effects, setEffects] = useState<Effect[]>([]);
  const [selection, setSelection] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const flight = useRef<{ id: string; type: string } | null>(null);
  const changed = useRef(onChanged);
  useEffect(() => { changed.current = onChanged; }, [onChanged]);
  // A lost response retries the same command body and identity, even after re-render.
  const commands = useRef(new Map<string, string>());
  useEffect(() => {
    flight.current = null;
    setPending(false);
    if (!channel) return;
    return channel.onMessage(incoming => {
      const message = incoming as unknown as { type?: string; payload?: unknown };
      const payload = asRecord(message.payload);
      if (!flight.current || message.type !== `${flight.current.type}_response`
          || payload.request_id !== flight.current.id) return;
      flight.current = null;
      setPending(false);
      if (payload.ok === true) { setError(""); changed.current(); }
      else setError(asText(payload.error) || "请求未完成，请检查后重试");
    });
  }, [channel]);
  useEffect(() => {
    if (!pending || !flight.current) return;
    const requestId = flight.current.id;
    const timer = setTimeout(() => {
      if (flight.current?.id !== requestId) return;
      flight.current = null;
      setPending(false);
      setError("等待响应超时，结果尚未确认；可使用原请求重试");
    }, 30000);
    return () => clearTimeout(timer);
  }, [pending]);
  const send = (type: string, body: Json, identity: string) => {
    if (!channel || pending) return;
    const fingerprint = JSON.stringify([type, body]);
    let command = commands.current.get(fingerprint);
    if (!command) { command = newRequestKey(); commands.current.set(fingerprint, command); }
    const requestId = newRequestKey();
    flight.current = { id: requestId, type };
    setPending(true); setError("");
    try {
      if (channel.send({ type, request_id: requestId, payload: { ...body, [identity]: command } })) return;
    } catch { /* Preserve command identity for a retry. */ }
    flight.current = null; setPending(false); setError("连接不可用，结果尚未确认；请重试");
  };
  const criteria = asList(w.criteria), milestones = asList(w.milestones), obligations = asList(w.obligations);
  const spec = asRecord(w.spec);
  const approved = w.state === "APPROVED";
  const disabled = pending || !channel || w.editable !== true;
  const patch = (key: string, field: keyof Effect, value: string | string[]) =>
    setEffects(rows => rows.map(row => row.key === key ? { ...row, [field]: value } : row));
  const mappingValid = content.length + effects.length > 0
    && effects.every(e => e.criteria.length && e.obligation && e.milestone && e.criteria.every(id => !content.includes(id)))
    && criteria.filter(c => c.required === true).every(c => content.includes(asText(c.id)) || effects.some(e => e.criteria.includes(asText(c.id))));
  const confirm = () => {
    if (!mappingValid) return;
    const ref = asRecord(w.requirements_ref);
    send("mission_operation_completion_approve", { mission_id: w.mission_id,
      expected_requirements_ref: ref, proposal: { schema_version: 1, mission_id: w.mission_id,
        requirements_ref: { id: ref.id, revision: ref.revision, content_hash: ref.content_hash },
        mode: effects.length ? "REQUIRED_EFFECTS" : "CONTENT_ONLY", content_criterion_ids: content,
        effects: effects.map(e => {
          const milestone = milestones.find(m => m.id === e.milestone)!;
          return { effect_key: e.key, source_slot_key: e.key, obligation_id: e.obligation,
            criterion_ids: e.criteria, required_milestone: e.milestone,
            milestone_policy_ref: milestone.milestone_policy_ref, evidence_policy_ref: milestone.evidence_policy_ref };
        }) } }, "command_id");
  };
  return <section aria-label="完成要求与操作" style={{ margin: "16px 0", overflowWrap: "anywhere" }}>
    <h3>完成要求与操作</h3>
    {w.state === "WAITING_REQUIREMENTS" ? <p>等待任务生成明确的完成要求。</p> : <>
      {approved ? <>
        <p>{spec.mode === "CONTENT_ONLY" ? "已确认：交付符合要求的内容。" : "已确认：必须取得以下实际效果的验证结果。"}</p>
        <ul>{criteria.map(c => <li key={asText(c.id)}>{asText(c.statement)}</li>)}</ul>
        {asList(spec.effects).map(e => <p key={asText(e.effect_key)}>
          {criteria.filter(c => (e.criterion_ids as string[])?.includes(asText(c.id))).map(c => asText(c.statement)).join("；")} —
          {asText(milestones.find(m => m.id === e.required_milestone)?.label) || asText(e.required_milestone)}
        </p>)}
      </> : <>
        <p>勾选只需要交付内容（文件、文字）就算完成的要求，然后点「确认上述完成要求」。需要真实操作的要求（如发送邮件）请放到「必须验证的实际效果」。</p>
        <fieldset disabled={disabled}>
          <legend>仅以内容交付验收的要求</legend>
          {criteria.length > 1 && (() => {
            // 全选只选没放进"实际效果"的要求（2026-09-25 真机点击：8 条要求要逐个勾）。
            const free = criteria.map(c => asText(c.id)).filter(id => !effects.some(e => e.criteria.includes(id)));
            const all = free.length > 0 && free.every(id => content.includes(id));
            return <button type="button" style={{ marginBottom: 4 }}
              onClick={() => setContent(all ? [] : free)}>{all ? "全部取消" : "全选"}</button>;
          })()}
          {criteria.map(c => <label key={asText(c.id)} style={{ display: "block" }}>
            <input type="checkbox" checked={content.includes(asText(c.id))}
              onChange={event => setContent(ids => event.target.checked ? [...ids, asText(c.id)] : ids.filter(id => id !== c.id))} />
            {asText(c.statement)}{c.required === true ? "（必需）" : "（可选）"}
          </label>)}
          <p>必须验证的实际效果</p>
          {effects.map(e => <div key={e.key}>
            <fieldset><legend>这一次效果覆盖的要求（可多选）</legend>
              {criteria.map(c => <label key={asText(c.id)} style={{ display: "block" }}>
                <input type="checkbox" checked={e.criteria.includes(asText(c.id))}
                  onChange={event => patch(e.key, "criteria", event.target.checked
                    ? [...e.criteria, asText(c.id)] : e.criteria.filter(id => id !== c.id))} />{asText(c.statement)}
              </label>)}
            </fieldset>
            <select aria-label="效果所属目标" value={e.obligation} onChange={event => patch(e.key, "obligation", event.target.value)}>
              <option value="">选择所属目标</option>{obligations.map(o => <option key={asText(o.id)} value={asText(o.id)}>{asText(o.label)} · {asText(o.id)}</option>)}
            </select>
            <select aria-label="效果完成标准" value={e.milestone} onChange={event => patch(e.key, "milestone", event.target.value)}>
              <option value="">选择完成标准</option>{milestones.map(m => <option key={asText(m.id)} value={asText(m.id)}>{asText(m.label)}</option>)}
            </select>
            <button type="button" onClick={() => setEffects(rows => rows.filter(row => row.key !== e.key))}>移除此效果</button>
          </div>)}
          <button type="button" disabled={!milestones.length || effects.length >= 64}
            onClick={() => setEffects(rows => [...rows, { key: `effect-${newRequestKey()}`, criteria: [], obligation: "", milestone: "" }])}>添加必须完成的效果</button>
          {!milestones.length && <p>当前还不能验证真实操作，只能确认交付内容类的要求。</p>}
          <p>这里确认的是"怎样算完成"，不会立刻执行任何操作。</p>
          <button type="button" disabled={!mappingValid} onClick={confirm}>确认上述完成要求</button>
        </fieldset>
      </>}
      {approved && asList(spec.effects).map(effect => {
        const key = asText(effect.effect_key);
        const previous = asList(w.intents).filter(i => i.effect_key === key && i.spec_hash === w.spec_hash);
        const heads = previous.filter(i => i.current === true);
        const predecessor = heads.length === 1 ? heads[0] : null;
        const canSubmit = !previous.length || (predecessor?.can_replace === true);
        const candidates = asList(w.candidates);
        const selected = candidates.find(c => asRecord(c.candidate_artifact_ref).id === selection[key]);
        return <div key={key} style={{ marginTop: 12 }}>
          <p>操作要求：{criteria.filter(c => (effect.criterion_ids as string[])?.includes(asText(c.id))).map(c => asText(c.statement)).join("；")}</p>
          {previous.map(i => <p key={asText(i.intent_id)}>请求状态：{asText(i.state)}{i.current === true ? "（当前）" : "（已被修订替代）"}</p>)}
          {!canSubmit ? <p>已有操作进入执行链，需等待审查或核对结果，不能重复提交。</p> : <>
            <select aria-label="选择已接受的操作准备产物" disabled={disabled} value={selection[key] || ""}
              onChange={event => setSelection(values => ({ ...values, [key]: event.target.value }))}>
              <option value="">选择已接受的准备产物</option>
              {candidates.map(c => <option key={asText(asRecord(c.candidate_artifact_ref).id)} value={asText(asRecord(c.candidate_artifact_ref).id)}>{asText(c.artifact_path)}</option>)}
            </select>
            {selected && <div>
              <p>{asText(asRecord(selected.candidate).operation)} → {asText(asRecord(selected.candidate).target)}</p>
              <p>{asText(asRecord(selected.candidate).reason)}</p>
              <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(asRecord(selected.candidate).params, null, 2)}</pre>
            </div>}
            <button type="button" disabled={disabled || !selected} onClick={() => selected && send("mission_operation_intent_submit", {
              schema_version: 2, mission_id: w.mission_id, intent_source: { kind: "USER_COMMAND" },
              supersedes_intent_id: predecessor?.intent_id ?? null, candidate_artifact_ref: selected.candidate_artifact_ref,
              prepared_acceptance_refs: selected.prepared_acceptance_refs,
              completion_slot: { spec_hash: w.spec_hash, effect_key: key },
            }, "idempotency_key")}>{predecessor ? "提交修订版并替代原请求" : "提交此操作供审查"}</button>
          </>}
        </div>;
      })}
    </>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
