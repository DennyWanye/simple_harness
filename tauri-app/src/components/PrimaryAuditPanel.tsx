import { useEffect, useSyncExternalStore } from "react";
import { auditRequestsForPort } from "../primary/auditRequests";
import type { PrimaryPort } from "../primary/controller";
import { buttonStyle } from "../theme/components";

const outcomeLabel: Record<string, string> = {
  committed: "已提交", rejected: "已拒绝", started: "已开始", observed: "已观察", unknown: "结果未知",
};
const effectLabel: Record<string, string> = {
  written: "认知记忆已写入", no_mutation: "未修改认知记忆", unverified: "认知写入尚未核验", not_applicable: "不涉及认知写入",
};
const familyLabel: Record<string, string> = {
  suppression: "忘记与禁用", mutation: "记忆变更", typed_recall: "记忆检索",
  analysis: "记忆分析", audit_access: "审计访问", registration: "来源登记",
};
export function PrimaryAuditPanel({ port, primaryRef, verifiedOwnerKey, ready }: {
  port: PrimaryPort; primaryRef: string; verifiedOwnerKey: string | null; ready: boolean;
}) {
  const client = auditRequestsForPort(port);
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  useEffect(() => client.connect(port, primaryRef, verifiedOwnerKey, ready), [client, port, primaryRef, verifiedOwnerKey, ready]);
  const bound = ready && verifiedOwnerKey !== null && state.ready && state.bindingKey === JSON.stringify([primaryRef, verifiedOwnerKey]);
  // Render fence applies before effects run when the parent changes identity.
  if (!bound) return <section aria-label="记忆操作记录"><p role="status">等待当前连接身份确认</p></section>;
  const page = state.grant && state.grant.expires_at * 1000 > Date.now() ? state.page : null;
  return <section aria-label="记忆操作记录">
    <p>仅查看自己的操作元数据，不包含对话正文。一次授权有效 5 分钟，最多读取 32 页。</p>
    <p>这里覆盖当前记忆系统支持的记录，不代表全部 Agent 操作；记录条数不等于模型调用次数，费用未汇总。</p>
    {state.notice && <p role="status">{state.notice}</p>}
    {!state.grant && !state.pending && <button style={buttonStyle("secondary", "sm")} disabled={state.busy}
      onClick={() => void client.open()}>查看我的记忆操作记录（仅元数据）</button>}
    {state.pending && <div>
      <button disabled={state.busy} onClick={() => void client.retry()}>重试同一次请求</button>
      {!state.grant && <button disabled={state.busy} onClick={client.abandon}>放弃未确认的查看</button>}
    </div>}
    {state.grant && <>
      <button disabled={state.busy || state.pending || page?.next_cursor_ref === null || (page?.reads_used ?? 0) >= 32}
        onClick={() => void client.next()}>{page ? "下一页" : "读取记录"}</button>
      <button onClick={() => void client.close()}>结束本次查看</button>
      <p>到期时间：{new Date(state.grant.expires_at * 1000).toLocaleTimeString()}</p>
    </>}
    {page && <>
      <ul>{page.items.map(item => <li key={item.item_hash} style={{ marginBottom: 12 }}>
        <span>{familyLabel[item.family] ?? "记忆操作"} · {outcomeLabel[item.outcome] ?? "状态未识别"}</span>
        <div>{new Date(item.occurred_at * 1000).toLocaleString()} · {effectLabel[item.cognitive_effect] ?? "写入状态未识别"}</div>
        <details><summary>核对标识</summary><p style={{ overflowWrap: "anywhere" }}>{item.event_kind}<br />{item.operation_ref_hash}</p></details>
      </li>)}</ul>
      {!page.items.length && <p>本页没有记录。</p>}
      <p>已读取 {page.reads_used}/32 页。{page.enumeration_complete ? "本次快照已读到末页。" : "本次快照尚未读完。"}</p>
      <details><summary>本次覆盖范围与缺口</summary>
        <ul>{page.coverage.map(c => <li key={c.family}>{familyLabel[c.family] ?? c.family}：{c.row_count} 条记录
          {(c.unresolved_count > 0 || c.missing_count > 0) && <p>未关联来源：{c.unresolved_count}；缺少事件：{c.missing_count}。</p>}
          {c.exclusions.length > 0 && <p>{c.exclusions.join("；")}{c.exclusions_truncated ? "（还有未展开的缺口）" : ""}</p>}
        </li>)}</ul>
        <p style={{ overflowWrap: "anywhere" }}>快照校验值：{page.snapshot_hash}</p>
      </details>
    </>}
  </section>;
}
