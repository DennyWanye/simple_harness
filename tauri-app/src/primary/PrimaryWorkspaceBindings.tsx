import { useEffect, useState } from "react";
import type { PrimaryPort } from "./controller";
import { PrimaryRequests, record } from "./requests";

const identityKeys = ["primary_ref", "run_ref", "sdk_run_ref", "generation", "effect_ref",
  "challenge_ref", "challenge_hash", "scope_ref", "proposal_hash"] as const;
type Binding = Record<(typeof identityKeys)[number], string | number> & {
  challenge_ref: string; scope_ref: string; root_path: string; state: string;
  can_decide: boolean; expires_at_millis: number; binding_receipt_ref: string | null;
};
function identity(item: Binding) { return Object.fromEntries(identityKeys.map(key => [key, item[key]])); }
function readItems(value: Record<string, unknown>, primaryRef: string): Binding[] {
  if (value.primary_ref !== primaryRef || value.truncated !== false || !Array.isArray(value.items) || value.items.length > 32) throw Error("目录授权读取未确认");
  return value.items.map(raw => {
    const item = record(raw);
    if (item.primary_ref !== primaryRef || identityKeys.some(key => key !== "generation" && (typeof item[key] !== "string" || !item[key])) ||
        typeof item.generation !== "number" || !Number.isSafeInteger(item.generation) || item.generation < 1 || typeof item.root_path !== "string" ||
        typeof item.state !== "string" || typeof item.can_decide !== "boolean" || typeof item.expires_at_millis !== "number" || !Number.isSafeInteger(item.expires_at_millis) ||
        (item.binding_receipt_ref !== null && typeof item.binding_receipt_ref !== "string")) throw Error("目录授权身份不完整");
    return item as Binding;
  });
}
const labels: Record<string, string> = { pending: "等待本次目录授权", expired: "本次目录授权已过期",
  denied: "已拒绝", bound: "新任务已绑定此目录", allow_recorded: "允许已记录，目录绑定尚未完成",
  root_changed: "目录身份已变化，不能批准", binding_changed: "绑定版本已变化，不能批准" };

export function PrimaryWorkspaceBindings({ port, primaryRef, ownerKey, ready, refreshVersion = 0 }: {
  port: PrimaryPort; primaryRef: string; ownerKey: string | null; ready: boolean; refreshVersion?: number;
}) {
  const [items, setItems] = useState<Binding[]>([]);
  const [error, setError] = useState("");
  const [decisionError, setDecisionError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [actions, setActions] = useState<{ refresh: () => void; decide: (item: Binding, decision: string) => void } | null>(null);
  useEffect(() => {
    setItems([]); setError(""); setDecisionError(""); setBusy(null); setActions(null);
    if (!ready || !ownerKey) return;
    const requests = new PrimaryRequests(port);
    let live = true, readVersion = 0, submitting = false, reading = false, readAgain = false;
    const refresh = async () => {
      if (!live || port.state() !== "connected") return;
      if (reading || submitting) { readAgain = true; return; }
      reading = true;
      const version = ++readVersion;
      try {
        const result = await requests.request("primary.bindings.pending", { primary_ref: primaryRef });
        if (!live || version !== readVersion) return;
        setItems(readItems(result, primaryRef)); setError("");
      } catch (e) { if (live && version === readVersion) { setItems([]); setError(e instanceof Error ? e.message : "目录授权读取未确认"); } }
      finally { reading = false; if (live && readAgain) { readAgain = false; void refresh(); } }
    };
    const decide = async (item: Binding, decision: string) => {
      if (submitting || !item.can_decide) return;
      submitting = true; setBusy(item.challenge_ref); ++readVersion;
      try {
        const result = await requests.request("primary.bindings.decide", { ...identity(item), decision });
        if (!live) return;
        if (identityKeys.some(key => result[key] !== item[key]) || !["bound", "denied"].includes(String(result.state)) ||
            (result.state === "bound" && typeof result.binding_receipt_ref !== "string")) throw Error("目录授权结果未确认");
        setItems(old => old.map(row => row.challenge_ref === item.challenge_ref ?
          { ...row, state: String(result.state), can_decide: false, binding_receipt_ref: typeof result.binding_receipt_ref === "string" ? result.binding_receipt_ref : null } : row));
        setDecisionError("");
      } catch (e) {
        // No retry of allow on timeout. Only read the original durable state;
        // allow_recorded exposes an explicit retry of that SAME decision.
        if (live) setDecisionError(e instanceof Error ? e.message : "决定响应未确认，请以重新读取的状态为准");
      } finally { submitting = false; readAgain = false; if (live) { setBusy(null); void refresh(); } }
    };
    const focus = () => { void refresh(); };
    const off = port.on_state_change(state => {
      requests.invalidate(); ++readVersion; setItems([]); setError("连接变化，目录授权结果未确认");
      if (state === "connected") void refresh();
    });
    const messages = port.on_message(raw => { if (record(raw).type === "human_memory_changed") focus(); });
    window.addEventListener("focus", focus);
    setActions({ refresh: focus, decide: (item, decision) => { void decide(item, decision); } });
    return () => { live = false; ++readVersion; requests.dispose(); off(); messages(); window.removeEventListener("focus", focus); };
  }, [port, primaryRef, ownerKey, ready]);
  useEffect(() => { actions?.refresh(); }, [refreshVersion, actions]);
  if (!ready || !ownerKey) return null;
  return <section aria-label="项目目录授权">
    {error && <p role="alert">{error}<button onClick={() => actions?.refresh()}>重新读取目录授权</button></p>}
    {decisionError && <p role="alert">{decisionError}<button onClick={() => actions?.refresh()}>核对决定状态</button></p>}
    {items.map(item => <article key={item.challenge_ref}>
      <strong>{labels[item.state] ?? "目录授权状态未确认"}</strong>
      <p>新任务：{item.scope_ref}</p><p>目录：{item.root_path}</p>
      {item.state === "bound" && <p>旧任务不会重新打开。继续编辑时，在新的消息中选择此新任务；实际执行仍需任务路由。</p>}
      {item.can_decide && <>
        <button disabled={busy !== null} onClick={() => actions?.decide(item, "allow")}>
          {item.state === "allow_recorded" ? "重试完成已允许的绑定" : "允许本次绑定"}</button>
        {item.state === "pending" && <button disabled={busy !== null} onClick={() => actions?.decide(item, "deny")}>拒绝</button>}
      </>}
    </article>)}
  </section>;
}
