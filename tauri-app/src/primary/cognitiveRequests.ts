import { PrimaryRequestError, PrimaryRequests, record } from "./requests";
import type { PrimaryPort } from "./controller";

export interface CognitiveMemoryItem {
  memory_id: string; revision: number; label: string; status: string;
  can_forget: boolean; content_hash: string;
}
interface Action {
  action_id: string; memory_id: string; expected_revision: number; expected_content_hash: string;
}
export interface CognitiveSnapshot {
  ready: boolean; loading: boolean; writing: boolean; items: CognitiveMemoryItem[];
  nextCursor: string | null; pending: Action[]; notice: string; error: string;
}
const hash = (v: unknown): v is string => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const id = (v: unknown): v is string => typeof v === "string" && v.length > 0 && v.length <= 512 && v.trim() === v;
function page(raw: unknown, primary: string): { items: CognitiveMemoryItem[]; nextCursor: string | null } {
  const p = record(raw);
  if (p.primary_ref !== primary || !Array.isArray(p.items) || p.items.length > 50 ||
      !(p.next_cursor === null || id(p.next_cursor))) throw new Error("认知记忆响应无效");
  const seen = new Set<string>();
  const items = p.items.map((value) => {
    const item = record(value);
    if (!id(item.memory_id) || seen.has(item.memory_id) || !Number.isSafeInteger(item.revision) || Number(item.revision) < 1 ||
        typeof item.label !== "string" || Array.from(item.label).length > 512 ||
        typeof item.status !== "string" || Array.from(item.status).length > 64 || typeof item.can_forget !== "boolean" || !hash(item.content_hash)) {
      throw new Error("认知记忆条目无效");
    }
    seen.add(item.memory_id);
    return item as unknown as CognitiveMemoryItem;
  });
  if (p.next_cursor !== null && (items.length === 0 || p.next_cursor !== items.at(-1)?.memory_id)) throw new Error("记忆分页无效");
  return { items, nextCursor: p.next_cursor as string | null };
}

/** Main passes a verified owner key and its actual bound PrimaryPort. No legacy socket. */
export class CognitiveRequests {
  private value: CognitiveSnapshot = { ready: false, loading: false, writing: false, items: [], nextCursor: null, pending: [], notice: "", error: "" };
  private listeners = new Set<() => void>();
  private forgottenListeners = new Set<() => void>();
  private reads?: PrimaryRequests;
  private writes?: PrimaryRequests;
  private owner = "";
  private primary = "";
  private epoch = 0;
  private lifetime = 0;
  private authorizationEpoch = 0;
  private actions = new Map<string, Action>();
  private uncertain = new Set<string>();
  getSnapshot = () => this.value;
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  onForgotten = (fn: () => void) => { this.forgottenListeners.add(fn); return () => { this.forgottenListeners.delete(fn); }; };
  private update(patch: Partial<CognitiveSnapshot>) {
    this.value = { ...this.value, ...patch, pending: [...this.actions.values()] };
    this.listeners.forEach((fn) => fn());
  }
  private clearRead() {
    ++this.epoch;
    this.reads?.invalidate();
    this.update({ items: [], nextCursor: null, loading: false });
  }
  connect(port: PrimaryPort, primaryRef: string, verifiedOwnerKey: string | null, ready: boolean) {
    const life = ++this.lifetime;
    this.clearRead();
    this.reads = new PrimaryRequests(port);
    this.writes = new PrimaryRequests(port);
    if (ready && verifiedOwnerKey) {
      if (this.owner !== verifiedOwnerKey || this.primary !== primaryRef) { this.actions.clear(); this.uncertain.clear(); }
      this.owner = verifiedOwnerKey; this.primary = primaryRef;
    }
    this.update({ ready: Boolean(ready && verifiedOwnerKey && primaryRef && port.state() === "connected"), writing: false, error: "", notice: "" });
    const revoke = () => {
      ++this.authorizationEpoch;
      this.clearRead(); this.writes?.invalidate();
      this.update({ ready: false, writing: false, error: "", notice: "等待当前连接身份恢复" });
    };
    const offState = port.on_state_change((state) => { if (state !== "connected") revoke(); });
    const off = port.on_message((raw) => {
      const message = record(raw);
      const payload = record(message.payload);
      if (message.type === "companion_identity_status" && (payload.ready === false || payload.status === "unready")) revoke();
      if (["companion_control_rechallenge", "companion_profile_unbound", "companion_identity_unready"].includes(String(message.type))) revoke();
      if (["companion_profile_bound", "companion_identity_status"].includes(String(message.type)) && payload.profile_id &&
          `${String(payload.profile_id)}:${String(payload.profile_generation ?? "")}` !== this.owner) revoke();
      if (["human_memory_changed", "human_memory_privacy_changed", "human_memory_invalidated", "companion_projection_retracted"].includes(String(message.type))) {
        this.clearRead();
        if (this.value.ready) void this.refresh();
      }
    });
    const focus = () => { if (this.value.ready) void this.refresh(); };
    window.addEventListener("focus", focus);
    if (this.value.ready) void this.refresh();
    return () => {
      off(); offState(); window.removeEventListener("focus", focus);
      if (this.lifetime === life) {
        ++this.lifetime; this.clearRead(); this.reads?.dispose(); this.writes?.dispose();
        this.update({ ready: false, writing: false });
      }
    };
  }
  refresh = async (cursor: string | null = null) => {
    if (!this.value.ready || !this.reads) return;
    this.clearRead(); const epoch = this.epoch;
    this.update({ loading: true, error: "" });
    try {
      const result = await this.reads.request("primary.memory.list", { primary_ref: this.primary, limit: 20, ...(cursor ? { cursor } : {}) });
      if (epoch === this.epoch) this.update(page(result, this.primary));
    } catch (error) {
      if (epoch === this.epoch) this.update({ error: error instanceof Error ? error.message : "认知记忆读取失败" });
    } finally { if (epoch === this.epoch) this.update({ loading: false }); }
  };
  forget = async (item: CognitiveMemoryItem) => {
    if (!this.value.ready || this.value.writing || !item.can_forget) return;
    // Only an item from the latest authorized page can introduce a new action.
    if (!this.value.items.some((v) => v.can_forget && v.memory_id === item.memory_id && v.revision === item.revision && v.content_hash === item.content_hash)) return;
    const existing = [...this.actions.values()].find((a) => a.memory_id === item.memory_id);
    const action = existing ?? { action_id: crypto.randomUUID(), memory_id: item.memory_id,
      expected_revision: item.revision, expected_content_hash: item.content_hash };
    this.actions.set(action.action_id, action);
    await this.send(action);
  };
  retry = async (actionId: string) => {
    const action = this.actions.get(actionId);
    if (action) await this.send(action);
  };
  private async send(action: Action) {
    if (!this.value.ready || !this.writes || this.value.writing) return;
    const life = this.lifetime;
    const authorization = this.authorizationEpoch;
    this.clearRead();
    this.update({ writing: true, notice: "正在提交忘记请求", error: "" });
    try {
      const r = await this.writes.request("primary.memory.forget", { primary_ref: this.primary, ...action });
      if (life !== this.lifetime || authorization !== this.authorizationEpoch || !this.value.ready) {
        if (this.actions.get(action.action_id) === action) this.uncertain.add(action.action_id);
        return;
      }
      if (r.primary_ref !== this.primary || r.action_id !== action.action_id || r.memory_id !== action.memory_id ||
          r.status !== "applied" || !id(r.directive_ref) || !id(r.evidence_ref) || !hash(r.decision_hash)) {
        throw new PrimaryRequestError("忘记回执不匹配，结果未确认", true);
      }
      this.actions.delete(action.action_id);
      this.uncertain.delete(action.action_id);
      this.clearRead();
      this.update({ notice: "已忘记该记忆；保留原始历史档案。" });
      // Clear parent history/detail synchronously, before waiting for any fresh read.
      // A display callback failure cannot undo a confirmed SDK acknowledgement.
      let parentRefreshFailed = false;
      for (const listener of this.forgottenListeners) {
        try { listener(); }
        catch { parentRefreshFailed = true; }
      }
      // Always fresh-read after ACK; never display an earlier/in-flight page.
      await this.refresh();
      if (parentRefreshFailed && life === this.lifetime && authorization === this.authorizationEpoch) {
        this.update({ error: "已忘记；主对话刷新失败，请手动刷新。" });
      }
    } catch (error) {
      if (life === this.lifetime) {
        const rejectedBeforeAdmission = error instanceof PrimaryRequestError && !error.uncertain &&
          ["primary_memory_target_stale", "primary_memory_request_invalid", "primary_memory_action_invalid",
            "cognitive_target_stale", "cognitive_target_unavailable", "cognitive_identifier_invalid", "cognitive_revision_invalid", "cognitive_hash_invalid"].includes(error.message);
        if (rejectedBeforeAdmission && !this.uncertain.has(action.action_id)) {
          this.actions.delete(action.action_id);
          this.update({ notice: "本次忘记未执行，请刷新后重新选择。", error: error.message });
        } else {
          this.uncertain.add(action.action_id);
          this.update({ notice: "忘记尚未确认；重试会使用同一动作，不会自动重发。", error: error instanceof Error ? error.message : "忘记结果未确认" });
        }
      } else if (this.actions.get(action.action_id) === action) {
        this.uncertain.add(action.action_id);
      }
    } finally { if (life === this.lifetime) this.update({ writing: false }); }
  }
}
