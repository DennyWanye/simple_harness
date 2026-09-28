// SPDX-License-Identifier: BUSL-1.1
import { useEffect, useRef, useState } from "react";
import { asList, asRecord, asText, newRequestKey, type MissionsChannel } from "../stores/missionsStore";

type Json = Record<string, unknown>;
type Effect = { key: string; criteria: string[]; obligation: string; milestone: string };

/** Explicit user confirmation; all authority and stale checks stay in the SDK. */
/** 完成要求里 `action:<操作>:<文件>` 行的目标文件名。 */
function actionTargets(statements: string[]): Set<string> {
  const out = new Set<string>();
  for (const line of statements) {
    const match = /^action:\S+:([^:\s]+)$/.exec(line.trim());
    if (match) out.add(fileName(match[1]));
  }
  return out;
}

function fileName(path: string): string { return path.split("/").pop() ?? path; }

function isAction(criterion: Json): boolean { return asText(criterion.statement).trim().startsWith("action:"); }

function defaultContent(w: Json): string[] {
  if (w.editable !== true) return [];
  return asList(w.criteria).filter(c => !isAction(c)).map(c => asText(c.id));
}

function defaultEffects(w: Json): Effect[] {
  const milestones = asList(w.milestones), obligations = asList(w.obligations);
  if (w.editable !== true || !milestones.length) return [];
  const hash = milestones.find(m => asText(m.label).includes("哈希")) ?? (milestones.length === 1 ? milestones[0] : undefined);
  return asList(w.criteria).filter(isAction).map(c => ({
    key: `effect-${newRequestKey()}`, criteria: [asText(c.id)],
    obligation: obligations.length === 1 ? asText(obligations[0].id) : "",
    milestone: hash ? asText(hash.id) : "",
  }));
}

/** 系统准备的发布申请走到了哪一步（operation_intent_status 的 state）。 */
const INTENT_STATE: Record<string, string> = {
  AWAITING_REVIEW: "系统已准备好申请，审阅员检查中",
  REVIEW_FAILED: "审阅没能完成",
  ACCEPT: "审阅通过，正在生成批准请求",
  REWORK: "审阅员要求修改这份申请",
  INCONCLUSIVE: "审阅员无法判断这份申请",
  REJECTED: "没有通过（被拒绝）",
  PROPOSED: "正在生成批准请求",
  AWAITING_APPROVAL: "等你批准（请在批准卡片上点「批准」）",
  APPROVED: "已批准，正在发布",
  HANDED_OFF: "正在发布",
  UNKNOWN: "发布结果待核对",
  SUCCEEDED: "已发布，正在核对",
  FAILED: "发布失败",
  REFUSED: "被拒绝执行",
  REVOKED: "已撤回", EXPIRED: "已过期", CANCELLED: "已取消", SUPERSEDED: "已被新申请替代",
};

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
  // 2026-09-29 真机（对话任务卡片）：原来要逐条勾选、逐个加效果，确认一次点十几下。改为默认预填：
  // 普通要求默认算"内容交付"，每条 action: 要求默认各配一个效果（唯一的目标自动选上，完成标准
  // 默认"内容哈希一致"）；人看一眼点确认即可，仍可改。
  const [content, setContent] = useState<string[]>(() => defaultContent(w));
  const [effects, setEffects] = useState<Effect[]>(() => defaultEffects(w));
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
            {/* NEXT-TG-1.0 §9：单选按钮代替下拉框（键盘 Tab/方向键即可选；只有一个选项时已自动选好） */}
            <fieldset role="radiogroup" aria-label="效果所属目标"><legend>所属目标</legend>
              {obligations.map(o => <label key={asText(o.id)} style={{ display: "block" }}>
                <input type="radio" name={`obligation-${e.key}`} value={asText(o.id)} checked={e.obligation === asText(o.id)}
                  onChange={() => patch(e.key, "obligation", asText(o.id))} />{asText(o.label) || asText(o.id)}
              </label>)}
            </fieldset>
            <fieldset role="radiogroup" aria-label="效果完成标准"><legend>完成标准</legend>
              {milestones.map(m => <label key={asText(m.id)} style={{ display: "block" }}>
                <input type="radio" name={`milestone-${e.key}`} value={asText(m.id)} checked={e.milestone === asText(m.id)}
                  onChange={() => patch(e.key, "milestone", asText(m.id))} />{asText(m.label)}
              </label>)}
            </fieldset>
            <button type="button" onClick={() => setEffects(rows => rows.filter(row => row.key !== e.key))}>移除此效果</button>
          </div>)}
          <button type="button" disabled={!milestones.length || effects.length >= 64}
            onClick={() => setEffects(rows => [...rows, { key: `effect-${newRequestKey()}`, criteria: [],
              obligation: obligations.length === 1 ? asText(obligations[0].id) : "",
              milestone: milestones.length === 1 ? asText(milestones[0].id) : "" }])}>添加必须完成的效果</button>
          {!milestones.length && <p>当前还不能验证真实操作，只能确认交付内容类的要求。</p>}
          <p>这里确认的是"怎样算完成"，不会立刻执行任何操作。</p>
          <button type="button" disabled={!mappingValid} onClick={confirm}>{pending ? "正在确认…" : "确认上述完成要求"}</button>
        </fieldset>
      </>}
      {approved && asList(spec.effects).map(effect => {
        // 2026-09-29：发布申请由系统按这里确认的效果自动准备（不再挑候选、点提交），
        // 这里只显示每个效果走到了哪一步；批准仍在批准卡片上点。
        const key = asText(effect.effect_key);
        const heads = asList(w.intents).filter(i => i.effect_key === key && i.spec_hash === w.spec_hash && i.current === true);
        const statements = criteria.filter(c => (effect.criterion_ids as string[])?.includes(asText(c.id))).map(c => asText(c.statement));
        const targets = [...actionTargets(statements)];
        const state = heads.length ? asText(heads[heads.length - 1].state) : "";
        return <div key={key} style={{ marginTop: 12 }}>
          <p>操作要求：{statements.join("；")}</p>
          <p>{state ? `进度：${INTENT_STATE[state] ?? state}`
            : `进度：等内容全部通过后，系统会自动准备${targets.length ? "发布 " + targets.join("、") + " 的" : ""}申请，审阅后请你批准。`}</p>
        </div>;
      })}
    </>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
