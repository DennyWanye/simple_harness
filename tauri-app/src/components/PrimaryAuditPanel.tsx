import { useEffect, useSyncExternalStore } from "react";
import { auditRequestsForPort, HOST_MAX_READS } from "../primary/auditRequests";
import type { HostCallItem, HostOperationItem, HostPage, HostRunItem } from "../primary/auditRequests";
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
const hostSectionLabel: Record<HostPage["section"], string> = {
  runs: "终态 Run 审计", run_operations: "单个 Run 的操作", memory_calls: "记忆调用记录",
};
const jobStatusLabel: Record<string, string> = {
  enumerated: "已枚举完成", pending: "待读取", reading: "读取中", unavailable: "不可用",
};
const callStateLabel: Record<string, string> = { returned: "已返回", raised: "抛出异常", interrupted: "已中断", started: "未结算" };
const timeOf = (v: number | null) => (v === null ? "—" : new Date(v * 1000).toLocaleString());
const short = (v: string | null) => (v === null ? "—" : v.length > 24 ? `${v.slice(0, 12)}…${v.slice(-8)}` : v);
function HostItems({ page, onOperations }: { page: HostPage; onOperations: (jobRef: string) => void }) {
  if (page.section === "runs") return <ul>{(page.items as HostRunItem[]).map(item => <li key={item.job_ref} style={{ marginBottom: 12 }}>
    <span>{item.terminal_state} · {jobStatusLabel[item.status] ?? item.status}{item.last_code ? `（${item.last_code}）` : ""}</span>
    <div>{timeOf(item.created_at)} · 操作 {item.processed_operations}/{item.total_operations ?? "?"} · 页 {item.pages_committed}/{item.total_pages ?? "?"}</div>
    {Object.keys(item.findings).length > 0 && <div>发现：{Object.entries(item.findings).map(([k, n]) => `${k}=${n}`).join("；")}</div>}
    <button disabled={item.status !== "enumerated"} onClick={() => onOperations(item.job_ref)}>查看该 Run 的操作</button>
    <details><summary>核对标识</summary><p style={{ overflowWrap: "anywhere" }}>run {item.run_ref}<br />job {item.job_ref}</p></details>
  </li>)}</ul>;
  if (page.section === "run_operations") return <ul>{(page.items as HostOperationItem[]).map(item => <li key={item.operation_id} style={{ marginBottom: 8 }}>
    <span>{item.operation_name ?? item.kind} · {item.state ?? item.record_type}{item.error_code ? `（${item.error_code}）` : ""}</span>
    <div>{timeOf(item.created_at)}{item.usage && "total_tokens" in item.usage ? ` · tokens ${item.usage.total_tokens}` : ""}</div>
    <details><summary>核对标识</summary><p style={{ overflowWrap: "anywhere" }}>{item.operation_id}</p></details>
  </li>)}</ul>;
  return <ul>{(page.items as HostCallItem[]).map(item => <li key={item.attempt_ref} style={{ marginBottom: 8 }}>
    <span>{item.caller} · {callStateLabel[item.state] ?? item.state} · {item.observation_status}{item.finding_reason ? `（${item.finding_reason}）` : ""}</span>
    <div>{timeOf(item.started_at)} → {timeOf(item.settled_at)}</div>
    <details><summary>核对标识</summary><p style={{ overflowWrap: "anywhere" }}>{item.attempt_ref}<br />{short(item.result_hash)}</p></details>
  </li>)}</ul>;
}
export function PrimaryAuditPanel({ port, primaryRef, verifiedOwnerKey, ready }: {
  port: PrimaryPort; primaryRef: string; verifiedOwnerKey: string | null; ready: boolean;
}) {
  const client = auditRequestsForPort(port);
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  useEffect(() => client.connect(port, primaryRef, verifiedOwnerKey, ready), [client, port, primaryRef, verifiedOwnerKey, ready]);
  const bound = ready && verifiedOwnerKey !== null && state.ready && state.bindingKey === JSON.stringify([primaryRef, verifiedOwnerKey]);
  // Render fence applies before effects run when the parent changes identity.
  if (!bound) return <section aria-label="记忆操作记录"><p role="status">等待当前连接身份确认</p></section>;
  const live = state.grant && state.grant.expires_at * 1000 > Date.now() ? state.grant : null;
  const page = live ? state.page : null;
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
    {/* Same expiry fence as the memory page above: a lapsed grant shows nothing. */}
    {live && <section aria-label="本机执行审计">
      <h3>本机执行审计（终态 Run / 记忆调用）</h3>
      <p>同一次授权下读取本机保存的终态 Run 审计页与记忆调用日志，只有标识、状态与哈希，不含参数与结果正文；每个分节最多 32 页，不占用上面的记忆系统页数。</p>
      <div>
        <button disabled={state.busy || state.pending} onClick={() => void client.readHost("runs")}>读取终态 Run 审计</button>
        <button disabled={state.busy || state.pending} onClick={() => void client.readHost("memory_calls")}>读取记忆调用记录</button>
        {Object.entries(state.hostPages).filter(([key]) => key !== state.hostView).map(([key, cached]) =>
          <button key={key} onClick={() => client.showHost(key)}>返回 {hostSectionLabel[cached.section]}{cached.target_ref ? `：${short(cached.target_ref)}` : ""}</button>)}
      </div>
      {state.hostView !== null && state.hostPages[state.hostView] && (() => {
        const hostPage = state.hostPages[state.hostView];
        return <>
          <h4>{hostSectionLabel[hostPage.section]}{hostPage.target_ref ? `：${short(hostPage.target_ref)}` : ""}</h4>
          <HostItems page={hostPage} onOperations={jobRef => void client.readHost("run_operations", jobRef)} />
          {!hostPage.items.length && <p>本页没有记录。</p>}
          <p>已读取 {hostPage.reads_used}/{HOST_MAX_READS} 页。{hostPage.enumeration_complete ? "本次快照已读到末页。" : "本次快照尚未读完。"}</p>
          <button disabled={state.busy || state.pending || hostPage.next_cursor_ref === null || hostPage.reads_used >= HOST_MAX_READS}
            onClick={() => void client.readHost(hostPage.section, hostPage.target_ref)}>下一页（{hostSectionLabel[hostPage.section]}）</button>
          <details><summary>本分节覆盖范围</summary>
            <p style={{ overflowWrap: "anywhere" }}>{Object.entries(hostPage.coverage).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("；")}</p>
            <p style={{ overflowWrap: "anywhere" }}>快照校验值：{hostPage.snapshot_hash}</p>
          </details>
        </>;
      })()}
    </section>}
  </section>;
}
