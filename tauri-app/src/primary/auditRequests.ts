import type { PrimaryPort } from "./controller";
import { PrimaryRequests, record } from "./requests";

interface Grant {
  audit_ref: string; open_action_id: string; expires_at: number;
}
export interface AuditItem {
  family: string; event_kind: string; outcome: string; occurred_at: number;
  cognitive_effect: string; operation_ref_hash: string; item_hash: string;
}
export interface AuditPage {
  items: AuditItem[]; snapshot_hash: string; page_hash: string;
  next_cursor_ref: string | null; enumeration_complete: boolean;
  reads_used: number; expires_at: number;
  coverage: { family: string; row_count: number; unresolved_count: number; missing_count: number; exclusions: string[]; exclusions_truncated: boolean }[];
}
export type HostSection = "runs" | "run_operations" | "memory_calls";
export const HOST_SECTIONS: HostSection[] = ["runs", "run_operations", "memory_calls"];
export const HOST_PAGE_LIMIT: Record<HostSection, number> = { runs: 20, run_operations: 100, memory_calls: 50 };
/** Mirrors HOST_MAX_READS in deskpet/operation_audit/human_access.py; a different budget fails the check. */
export const HOST_MAX_READS = 32;
export interface HostRunItem {
  job_ref: string; run_ref: string; host_run_ref: string; terminal_state: string; status: string;
  last_code: string | null; rule_version: string; total_operations: number | null; processed_operations: number;
  total_pages: number | null; pages_committed: number; created_at: number; updated_at: number;
  attempts: Record<string, number>; findings: Record<string, number>;
}
export interface HostOperationItem {
  operation_id: string; kind: string; record_type: string; operation_name: string | null; state: string | null;
  error_code: string | null; created_at: number | null; settled_at: number | null;
  handoff_to_settlement_seconds: number | null; parent_operation_id: string | null; effect_id: string | null;
  provider_invocation_id: string | null; request_hash: string | null; result_hash: string | null;
  source_hash: string | null; usage: Record<string, number> | null;
}
export interface HostCallItem {
  attempt_ref: string; request_ref: string; caller: string; state: string; observation_status: string;
  started_at: number; settled_at: number | null; context_run_ref_hash: string | null; context_hash: string | null;
  plan_hash: string | null; result_hash: string | null; decision_hash: string | null; observation_hash: string | null;
  finding_reason: string | null;
}
export interface HostPage {
  section: HostSection; target_ref: string | null; items: (HostRunItem | HostOperationItem | HostCallItem)[];
  snapshot_hash: string; page_hash: string; next_cursor_ref: string | null; enumeration_complete: boolean;
  reads_used: number; expires_at: number; coverage: Record<string, unknown>;
}
export const hostStreamKey = (section: HostSection, target: string | null) => `${section}|${target ?? ""}`;
interface Action { operation: string; request: Record<string, unknown> }
interface Snapshot {
  ready: boolean; busy: boolean; grant: Grant | null; page: AuditPage | null;
  pending: boolean; notice: string; bindingKey: string;
  /** Host run-audit streams under the same grant, keyed by hostStreamKey. */
  hostPages: Record<string, HostPage>; hostView: string | null;
}
const id = (v: unknown): v is string => typeof v === "string" && /^[A-Za-z0-9_:.-]{1,128}$/.test(v);
const hash = (v: unknown): v is string => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const text = (v: unknown): v is string => typeof v === "string" && v.length <= 256;
const optText = (v: unknown): v is string | null => v === null || text(v);
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;
const optFinite = (v: unknown): v is number | null => v === null || finite(v);
const count = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) >= 0;
const optCount = (v: unknown): v is number | null => v === null || count(v);
const counts = (v: unknown): v is Record<string, number> =>
  typeof v === "object" && v !== null && !Array.isArray(v) && Object.keys(v).length <= 64 &&
  Object.entries(v).every(([k, n]) => text(k) && count(n));
const invalid = () => new Error("响应未通过核对；本次结果尚未确认。");
function hostItem(section: HostSection, value: unknown): HostRunItem | HostOperationItem | HostCallItem {
  const i = record(value);
  if (section === "runs") {
    if (![i.job_ref, i.run_ref, i.host_run_ref, i.terminal_state, i.status, i.rule_version].every(text) || !optText(i.last_code) ||
        !optCount(i.total_operations) || !count(i.processed_operations) || !optCount(i.total_pages) || !count(i.pages_committed) ||
        !finite(i.created_at) || !finite(i.updated_at) || !counts(i.attempts) || !counts(i.findings)) throw invalid();
    return i as unknown as HostRunItem;
  }
  if (section === "run_operations") {
    if (![i.operation_id, i.kind, i.record_type].every(text) ||
        ![i.operation_name, i.state, i.error_code, i.parent_operation_id, i.effect_id, i.provider_invocation_id,
          i.request_hash, i.result_hash, i.source_hash].every(optText) ||
        ![i.created_at, i.settled_at, i.handoff_to_settlement_seconds].every(optFinite) ||
        !(i.usage === null || (typeof i.usage === "object" && !Array.isArray(i.usage) &&
          Object.entries(record(i.usage)).every(([k, n]) => text(k) && typeof n === "number" && Number.isFinite(n))))) throw invalid();
    return i as unknown as HostOperationItem;
  }
  if (![i.attempt_ref, i.request_ref, i.caller, i.state, i.observation_status].every(text) || !finite(i.started_at) ||
      !optFinite(i.settled_at) || ![i.context_run_ref_hash, i.context_hash, i.plan_hash, i.result_hash, i.decision_hash,
        i.observation_hash, i.finding_reason].every(optText)) throw invalid();
  return i as unknown as HostCallItem;
}

/** Explicit HUMAN metadata access. No auto grant, page fetch, cursor replay or polling. */
export class AuditRequests {
  private value: Snapshot = { ready: false, busy: false, grant: null, page: null, pending: false, notice: "", bindingKey: "", hostPages: {}, hostView: null };
  private listeners = new Set<() => void>();
  private client?: PrimaryRequests;
  private action: Action | null = null;
  private primary = "";
  private owner = "";
  private generation = 0;
  private expiry?: ReturnType<typeof setTimeout>;
  private snapshotHash: string | null = null;
  private readsUsed = 0;
  private boundReceipt: unknown = null;
  getSnapshot = () => this.value;
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  private update(patch: Partial<Snapshot>) {
    this.value = { ...this.value, ...patch, pending: this.action !== null };
    this.listeners.forEach(fn => fn());
  }
  private revoke(notice: string, ready = false) {
    ++this.generation;
    this.client?.invalidate();
    clearTimeout(this.expiry);
    this.action = null;
    this.snapshotHash = null; this.readsUsed = 0;
    this.update({ ready, busy: false, grant: null, page: null, notice, hostPages: {}, hostView: null });
  }
  connect(port: PrimaryPort, primary: string, owner: string | null, ready: boolean) {
    if (owner !== this.owner || primary !== this.primary) {
      this.revoke(""); this.boundReceipt = null;
    }
    this.owner = owner ?? ""; this.primary = primary;
    this.client = new PrimaryRequests(port);
    this.update({ ready: Boolean(ready && owner && primary && port.state() === "connected"), bindingKey: JSON.stringify([primary, owner]) });
    if (this.value.grant) {
      if (this.value.grant.expires_at * 1000 <= Date.now()) this.revoke("本次查看授权已到期。", this.value.ready);
      else this.armExpiry(this.value.grant.expires_at);
    }
    const offState = port.on_state_change(state => {
      if (state !== "connected") this.revoke("连接已变更，旧查看授权不再使用。请在身份恢复后重新授权查看。");
    });
    const off = port.on_message(raw => {
      const message = record(raw), payload = record(message.payload);
      if (message.type === "companion_profile_bound") {
        const matches = Boolean(payload.profile_id && payload.profile_generation &&
          `${String(payload.profile_id)}:${String(payload.profile_generation)}` === this.owner);
        if (!matches) {
          this.revoke("连接身份已变更，旧查看授权不再使用。");
          return;
        }
        // boundPrimaryPort replays the exact ControlChannel cached object at
        // subscription time. That replay is not a new authorization event.
        // A new wire frame is a different object, including same-owner binds
        // whose profile generation/identity epoch did not change.
        if (this.boundReceipt === raw) return;
        const replacement = this.boundReceipt !== null;
        this.boundReceipt = raw;
        if (replacement) this.revoke("连接已重新绑定，旧查看授权不再使用。", ready && port.state() === "connected");
        return;
      }
      if (["companion_control_rechallenge", "companion_profile_unbound", "companion_identity_unready"].includes(String(message.type)) ||
          (message.type === "companion_identity_status" && (payload.ready === false || payload.status === "unready" ||
            (payload.profile_id && `${String(payload.profile_id)}:${String(payload.profile_generation ?? "")}` !== this.owner)))) {
        this.revoke("连接身份已变更，旧查看授权不再使用。");
      }
      // A global ready broadcast cannot authorize this socket. Metadata pages
      // are explicit audit snapshots; business change events never auto-read.
    });
    return () => {
      off(); offState();
      // A known capability can be closed even while its page ACK is unknown.
      // This is best effort; only a matched close ACK would confirm closure.
      const grant = this.value.grant;
      if (grant && this.value.ready) {
        void this.client?.request("primary.audit.close", { primary_ref: primary, audit_ref: grant.audit_ref }).catch(() => {});
      }
      ++this.generation;
      this.client?.dispose(); this.client = undefined;
      clearTimeout(this.expiry);
      // Retain an unknown open action in this parent-owned instance; it cannot
      // create another grant implicitly when this view is shown again.
      if (grant) this.action = { operation: "primary.audit.close", request: { primary_ref: primary, audit_ref: grant.audit_ref } };
      this.update({ ready: false, busy: false, page: null, hostPages: {}, hostView: null, notice: this.action ? "查看已隐藏；上次请求结果尚未确认。" : "" });
    };
  }
  /** Explicit Host run-audit read (G6): first page of a stream, or its next page. */
  readHost = async (section: HostSection, targetRef: string | null = null) => {
    if (this.action || !this.value.grant || !HOST_SECTIONS.includes(section) ||
        (section === "run_operations") !== (targetRef !== null) || (targetRef !== null && !id(targetRef))) return;
    const key = hostStreamKey(section, targetRef);
    const current = this.value.hostPages[key];
    if (current && (current.next_cursor_ref === null || current.reads_used >= HOST_MAX_READS)) { this.update({ hostView: key }); return; }
    await this.perform({ operation: "primary.audit.host.page", request: {
      primary_ref: this.primary, audit_ref: this.value.grant.audit_ref, page_action_id: crypto.randomUUID(),
      section, cursor_ref: current?.next_cursor_ref ?? null, target_ref: targetRef,
    } });
  };
  showHost = (key: string | null) => { if (key === null || this.value.hostPages[key]) this.update({ hostView: key }); };
  open = async () => {
    if (this.action || this.value.grant) return;
    await this.perform({ operation: "primary.audit.open", request: {
      primary_ref: this.primary, open_action_id: crypto.randomUUID(),
    } });
  };
  next = async () => {
    if (this.action || !this.value.grant || this.value.page?.next_cursor_ref === null ||
        (this.value.page?.reads_used ?? 0) >= 32) return;
    await this.perform({ operation: "primary.audit.page", request: {
      primary_ref: this.primary, audit_ref: this.value.grant.audit_ref,
      page_action_id: crypto.randomUUID(), cursor_ref: this.value.page?.next_cursor_ref ?? null,
    } });
  };
  retry = async () => { if (this.action) await this.perform(this.action); };
  close = async () => {
    const grant = this.value.grant;
    if (!grant) return;
    ++this.generation; this.client?.invalidate();
    this.update({ busy: false, page: null, hostPages: {}, hostView: null });
    await this.perform({ operation: "primary.audit.close", request: { primary_ref: this.primary, audit_ref: grant.audit_ref } });
  };
  abandon = () => {
    if (this.value.busy || this.value.grant) return;
    // Explicitly abandon only a metadata read/open, not a business operation.
    // Host archives retain the unknown; no statement about SDK execution.
    this.revoke("已放弃本地等待；旧请求结果仍未确认，可能存在的授权会自行到期。", this.value.ready);
  };
  private armExpiry(expires: number) {
    clearTimeout(this.expiry);
    const ms = expires * 1000 - Date.now();
    if (ms <= 0) throw invalid();
    this.expiry = setTimeout(() => this.revoke("本次查看授权已到期。", this.value.ready), Math.min(ms, 300_000));
  }
  private async perform(action: Action) {
    if (!this.value.ready || !this.client || this.value.busy) return;
    const generation = this.generation;
    this.action = action;
    const host = action.operation === "primary.audit.host.page";
    // A Host stream read keeps the SDK page and the other streams visible.
    this.update({ busy: true, ...(host ? {} : { page: null }), notice: "正在核对本次请求…" });
    try {
      const r = await this.client.request(action.operation, action.request);
      if (generation !== this.generation) return;
      if (r.primary_ref !== this.primary) throw invalid();
      if (action.operation === "primary.audit.open") {
        if (!id(r.audit_ref) || r.open_action_id !== action.request.open_action_id ||
            !finite(r.expires_at) || r.max_reads !== 32 || r.page_limit !== 100 || r.purpose !== "operation_metadata") throw invalid();
        this.armExpiry(r.expires_at);
        this.snapshotHash = null; this.readsUsed = 0;
        this.update({ grant: { audit_ref: r.audit_ref, open_action_id: String(r.open_action_id), expires_at: r.expires_at } });
      } else if (action.operation === "primary.audit.close") {
        if (r.audit_ref !== action.request.audit_ref || r.status !== "closed") throw invalid();
        clearTimeout(this.expiry);
        this.snapshotHash = null; this.readsUsed = 0;
        this.update({ grant: null, hostPages: {}, hostView: null });
      } else if (host) {
        const grant = this.value.grant;
        const section = action.request.section as HostSection;
        const key = hostStreamKey(section, (action.request.target_ref as string | null) ?? null);
        const previous = this.value.hostPages[key];
        if (!grant || r.audit_ref !== grant.audit_ref || r.page_action_id !== action.request.page_action_id ||
            r.section !== section || r.target_ref !== action.request.target_ref ||
            !hash(r.snapshot_hash) || !hash(r.page_hash) || r.expires_at !== grant.expires_at || r.max_reads !== HOST_MAX_READS ||
            r.all_operations_recorded !== false || r.reads_used !== (previous?.reads_used ?? 0) + 1 || Number(r.reads_used) > HOST_MAX_READS ||
            (previous && r.snapshot_hash !== previous.snapshot_hash) ||
            !(r.next_cursor_ref === null || id(r.next_cursor_ref)) || typeof r.enumeration_complete !== "boolean" ||
            !Array.isArray(r.items) || r.items.length > HOST_PAGE_LIMIT[section] ||
            typeof r.coverage !== "object" || r.coverage === null || Array.isArray(r.coverage)) throw invalid();
        const items = r.items.map(value => hostItem(section, value));
        this.armExpiry(grant.expires_at);
        const page = { ...r, section, items, coverage: record(r.coverage) } as unknown as HostPage;
        this.update({ hostPages: { ...this.value.hostPages, [key]: page }, hostView: key });
      } else {
        const grant = this.value.grant;
        if (!grant || r.audit_ref !== grant.audit_ref || r.page_action_id !== action.request.page_action_id ||
            !hash(r.snapshot_hash) || !hash(r.page_hash) || !hash(r.access_event_hash) ||
            r.expires_at !== grant.expires_at || r.max_reads !== 32 || r.all_operations_recorded !== false ||
            r.reads_used !== this.readsUsed + 1 || Number(r.reads_used) > 32 ||
            (this.snapshotHash !== null && r.snapshot_hash !== this.snapshotHash) ||
            !(r.next_cursor_ref === null || id(r.next_cursor_ref)) || typeof r.enumeration_complete !== "boolean" ||
            !Array.isArray(r.items) || r.items.length > 100 || !Array.isArray(r.coverage) || r.coverage.length > 32) throw invalid();
        const items = r.items.map(value => {
          const i = record(value);
          if (![i.family, i.event_kind, i.outcome, i.cognitive_effect].every(text) ||
              !finite(i.occurred_at) || !hash(i.operation_ref_hash) || !hash(i.item_hash)) throw invalid();
          return i as unknown as AuditItem;
        });
        const coverage = r.coverage.map(value => {
          const c = record(value);
          if (!text(c.family) || !Number.isSafeInteger(c.row_count) || Number(c.row_count) < 0 ||
              !Number.isSafeInteger(c.unresolved_count) || Number(c.unresolved_count) < 0 ||
              !Number.isSafeInteger(c.missing_count) || Number(c.missing_count) < 0 ||
              !Array.isArray(c.exclusions) || c.exclusions.length > 16 || !c.exclusions.every(text) ||
              typeof c.exclusions_truncated !== "boolean") throw invalid();
          return c as unknown as AuditPage["coverage"][number];
        });
        this.armExpiry(grant.expires_at);
        this.snapshotHash = r.snapshot_hash; this.readsUsed = Number(r.reads_used);
        this.update({ page: { ...r, items, coverage } as unknown as AuditPage });
      }
      this.action = null;
      this.update({ notice: action.operation === "primary.audit.close" ? "本次查看已关闭。" : "" });
    } catch {
      if (generation === this.generation) this.update({ ...(host ? { hostView: null } : { page: null }), notice: "本次结果尚未确认；重试会保持原请求，不会另开一次查看。" });
    } finally {
      if (generation === this.generation) this.update({ busy: false });
    }
  }
}

const byPort = new WeakMap<PrimaryPort, AuditRequests>();
/** Retain unresolved metadata actions across panel hiding, never across owners. */
export function auditRequestsForPort(port: PrimaryPort): AuditRequests {
  let client = byPort.get(port);
  if (!client) { client = new AuditRequests(); byPort.set(port, client); }
  return client;
}
