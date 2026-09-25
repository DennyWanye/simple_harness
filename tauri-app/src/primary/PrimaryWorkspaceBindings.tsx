import { useEffect, useState } from "react";
import { buttonStyle, cardStyle } from "../theme/components";
import { tokens } from "../theme/tokens";
import type { PrimaryPort } from "./controller";
import { PrimaryRequests, record } from "./requests";

import { bindingIdentityKeys as identityKeys, type BindingIdentity, type PrimaryBindingRecovery } from "./bindingRecovery";

type Binding = BindingIdentity & {
  challenge_ref: string; scope_ref: string; root_path: string; state: string;
  can_decide: boolean; expires_at_millis: number; binding_receipt_ref: string | null;
};
function identity(item: Binding) { return Object.fromEntries(identityKeys.map(key => [key, item[key]])) as BindingIdentity; }
function readItems(value: Record<string, unknown>, primaryRef: string): Binding[] {
  if (value.primary_ref !== primaryRef || value.truncated !== false || !Array.isArray(value.items) || value.items.length > 32 ||
      !(value.next_cursor === null || (typeof value.next_cursor === "string" && value.next_cursor.length > 0))) throw Error("目录授权读取未确认");
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
  root_changed: "目录身份已变化，不能批准", binding_changed: "绑定版本已变化，不能批准",
  policy_changed: "当前权限策略不允许批准此手动绑定" };

export function PrimaryWorkspaceBindings({ port, primaryRef, ownerKey, ready, recovery, refreshVersion = 0 }: {
  port: PrimaryPort; primaryRef: string; ownerKey: string | null; ready: boolean; recovery: PrimaryBindingRecovery; refreshVersion?: number;
}) {
  const [items, setItems] = useState<Binding[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [paged, setPaged] = useState(false);
  const [error, setError] = useState("");
  const [decisionError, setDecisionError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [actions, setActions] = useState<{ refresh: () => void; page: (cursor?: string) => void; decide: (item: Binding, decision: string) => void } | null>(null);
  useEffect(() => {
    setItems([]); setNextCursor(null); setPaged(false); setError(""); setDecisionError(""); setBusy(null); setActions(null);
    const owner = ownerKey;
    if (!ready || !owner) return;
    const requests = new PrimaryRequests(port);
    let live = true, epoch = 0, readVersion = 0, submitting = false, reading = false, readAgain = false;
    let pageCursor: string | undefined;
    let lastDecision = recovery.read(owner, primaryRef);
    const refresh = async () => {
      if (!live || port.state() !== "connected") return;
      if (reading || submitting) { readAgain = true; return; }
      reading = true;
      const version = ++readVersion, connection = epoch;
      const current = () => live && connection === epoch && version === readVersion;
      try {
        const result = await requests.request("primary.bindings.pending", {
          primary_ref: primaryRef, ...(pageCursor ? { cursor: pageCursor } : {}),
        });
        if (!current()) return;
        const page = readItems(result, primaryRef);
        // Absence from pending is never a decision ACK. Re-read the ONE
        // original attempted challenge, including after a lost response.
        if (lastDecision) {
          try {
            const exact = await requests.request("primary.bindings.status", {
              primary_ref: primaryRef, challenge_ref: lastDecision.challenge_ref,
            });
            if (!current()) return;
            const rows = readItems(exact, primaryRef);
            if (rows.length !== 1 || identityKeys.some(key => rows[0][key] !== lastDecision?.[key])) throw Error("原目录决定状态未确认");
            const found = page.findIndex(row => row.challenge_ref === rows[0].challenge_ref);
            if (found >= 0) page[found] = rows[0]; else page.unshift(rows[0]);
            if (["bound", "denied"].includes(rows[0].state)) setDecisionError("");
          } catch (e) {
            if (!current()) return;
            // Unknown original decision cannot suppress other verified pending
            // entries, nor be rendered as a completed grant.
            setDecisionError(e instanceof Error ? e.message : "原目录决定状态未确认");
          }
        }
        setItems(page); setNextCursor(result.next_cursor as string | null); setPaged(Boolean(pageCursor)); setError("");
      } catch (e) {
        if (current()) { setItems([]); setNextCursor(null); setError(e instanceof Error ? e.message : "目录授权读取未确认"); }
      } finally { reading = false; if (live && readAgain) { readAgain = false; void refresh(); } }
    };
    const decide = async (item: Binding, decision: string) => {
      if (submitting || !item.can_decide || port.state() !== "connected") return;
      const connection = epoch;
      recovery.remember(owner, identity(item));
      lastDecision = recovery.read(owner, primaryRef);
      submitting = true; setBusy(item.challenge_ref); ++readVersion;
      try {
        const result = await requests.request("primary.bindings.decide", { ...identity(item), decision });
        if (!live || connection !== epoch) return;
        if (identityKeys.some(key => result[key] !== item[key]) || !["bound", "denied"].includes(String(result.state)) ||
            (result.state === "bound" && (typeof result.binding_receipt_ref !== "string" || !result.binding_receipt_ref))) throw Error("目录授权结果未确认");
        setItems(old => old.map(row => row.challenge_ref === item.challenge_ref ?
          { ...row, state: String(result.state), can_decide: false, binding_receipt_ref: typeof result.binding_receipt_ref === "string" ? result.binding_receipt_ref : null } : row));
        setDecisionError("");
      } catch (e) {
        // No automatic allow replay, even when its response is lost.
        if (live && connection === epoch) setDecisionError(e instanceof Error ? e.message : "决定响应未确认，请以重新读取的状态为准");
      } finally { submitting = false; readAgain = false; if (live) { setBusy(null); void refresh(); } }
    };
    const focus = () => { void refresh(); };
    const off = port.on_state_change(state => {
      ++epoch; requests.invalidate(); ++readVersion; pageCursor = undefined;
      setItems([]); setNextCursor(null); setPaged(false); setError("连接变化，目录授权结果未确认");
      if (state === "connected") void refresh();
    });
    const messages = port.on_message(raw => { if (record(raw).type === "human_memory_changed") focus(); });
    window.addEventListener("focus", focus);
    setActions({ refresh: focus, page: cursor => { pageCursor = cursor; focus(); },
      decide: (item, decision) => { void decide(item, decision); } });
    return () => { live = false; ++epoch; ++readVersion; requests.dispose(); off(); messages(); window.removeEventListener("focus", focus); };
  }, [port, primaryRef, ownerKey, ready, recovery]);
  useEffect(() => { actions?.refresh(); }, [refreshVersion, actions]);
  if (!ready || !ownerKey) return null;
  return <section aria-label="项目目录授权">
    {error && <p role="alert">{error}<button onClick={() => actions?.refresh()}>重新读取目录授权</button></p>}
    {decisionError && <p role="alert">{decisionError}<button onClick={() => actions?.refresh()}>核对决定状态</button></p>}
    {paged && <button disabled={busy !== null} onClick={() => actions?.page()}>返回最新目录授权</button>}
    {nextCursor && <button disabled={busy !== null} onClick={() => actions?.page(nextCursor)}>读取下一页目录授权</button>}
    {items.map(item => <article key={item.challenge_ref} style={{ ...cardStyle, margin: `${tokens.space.sm}px 0` }}>
      <strong>{labels[item.state] ?? "目录授权状态未确认"}</strong>
      <p>新任务：{item.scope_ref}</p><p>目录：{item.root_path}</p>
      {item.state === "bound" && <p>旧任务不会重新打开。继续编辑时，在新的消息中选择此新任务；实际执行仍需任务路由。</p>}
      {item.can_decide && <>
        <button style={buttonStyle("primary", "sm", busy !== null)} disabled={busy !== null} onClick={() => actions?.decide(item, "allow")}>
          {item.state === "allow_recorded" ? "重试完成已允许的绑定" : "允许本次绑定"}</button>
        {item.state === "pending" && <button style={{ ...buttonStyle("secondary", "sm", busy !== null), marginLeft: tokens.space.sm }} disabled={busy !== null} onClick={() => actions?.decide(item, "deny")}>拒绝</button>}
      </>}
    </article>)}
  </section>;
}
