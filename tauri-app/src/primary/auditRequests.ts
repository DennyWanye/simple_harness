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
interface Action { operation: string; request: Record<string, unknown> }
interface Snapshot {
  ready: boolean; busy: boolean; grant: Grant | null; page: AuditPage | null;
  pending: boolean; notice: string; bindingKey: string;
}
const id = (v: unknown): v is string => typeof v === "string" && /^[A-Za-z0-9_:.-]{1,128}$/.test(v);
const hash = (v: unknown): v is string => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const text = (v: unknown): v is string => typeof v === "string" && v.length <= 256;
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;
const invalid = () => new Error("响应未通过核对；本次结果尚未确认。");

/** Explicit HUMAN metadata access. No auto grant, page fetch, cursor replay or polling. */
export class AuditRequests {
  private value: Snapshot = { ready: false, busy: false, grant: null, page: null, pending: false, notice: "", bindingKey: "" };
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
    this.update({ ready, busy: false, grant: null, page: null, notice });
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
      this.update({ ready: false, busy: false, page: null, notice: this.action ? "查看已隐藏；上次请求结果尚未确认。" : "" });
    };
  }
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
    this.update({ busy: false, page: null });
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
    this.update({ busy: true, page: null, notice: "正在核对本次请求…" });
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
        this.update({ grant: null });
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
      if (generation === this.generation) this.update({ page: null, notice: "本次结果尚未确认；重试会保持原请求，不会另开一次查看。" });
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
