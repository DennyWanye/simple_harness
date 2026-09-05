// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { PrimaryRequestError, PrimaryRequests, record, type PrimaryRequestPort } from "./requests";

export interface PrimaryPort extends PrimaryRequestPort {
  state(): "connected" | "connecting" | "disconnected";
  on_state_change(listener: (state: "connected" | "connecting" | "disconnected") => void): () => void;
}
export interface PrimaryRun {
  run_ref: string;
  generation: number;
  state: string;
  execution_session_ref?: string | null;
  sdk_run_ref?: string | null;
}
export interface PrimaryState {
  primary_ref: string;
  current_run: PrimaryRun | null;
  queued_count: number;
  queued_count_truncated: boolean;
  revision: string | number;
}
export interface PrimaryMessage {
  message_ref: string;
  role: "user" | "assistant" | "tool" | "artifact";
  has_more: boolean;
  total_chars: number;
  delivery_key?: string;
  text: string;
  run_ref?: string;
  turn_ref?: string;
}
export interface PrimarySnapshot {
  ready: boolean;
  loading: boolean;
  state: PrimaryState | null;
  messages: PrimaryMessage[];
  nextCursor: string | null;
  error: string;
  notice: string;
  viewEpoch: number;
  draftEpoch: number;
}
const empty = (): PrimarySnapshot => ({ ready: false, loading: false, state: null, messages: [], nextCursor: null, error: "", notice: "正在恢复主对话身份…", viewEpoch: 0, draftEpoch: 0 });

/** Bounded, replace-only read model. No persistence and no automatic mutation retry. */
export class PrimaryController {
  private snapshot = empty();
  private listeners = new Set<() => void>();
  private client: PrimaryRequests | null = null;
  private disposers: (() => void)[] = [];
  private owner = "";
  private epoch = 0;
  private lease = 0;
  private reading = false;
  private refreshAgain = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private followups = 0;
  private pendingDelivery: { text: string; delivery_key: string; uncertain: boolean } | null = null;
  private submitting = false;
  private controlling = false;
  private port: PrimaryPort;
  constructor(port: PrimaryPort) { this.port = port; }
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private update(patch: Partial<PrimarySnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((listener) => listener());
  }
  start = (port: PrimaryPort = this.port) => {
    this.port = port;
    this.client = new PrimaryRequests(this.port);
    this.disposers = [this.port.on_message(this.onMessage), this.port.on_state_change((state) => {
      if (state !== "connected") this.invalidate(false, "连接已断开；等待身份恢复后补读。");
      // Never reuse a cached ready flag on a new connection. Fresh identity
      // status/profile_bound is emitted after the existing signed bind.
    })];
    return () => {
      this.disposers.forEach((off) => off());
      this.disposers = [];
      this.invalidate(false, "");
      this.client?.dispose();
      this.client = null;
    };
  };
  private invalidate(forgetOwner: boolean, notice: string, readsOnly = false) {
    ++this.epoch;
    if (!readsOnly) ++this.lease;
    this.client?.invalidate(readsOnly);
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.reading = false;
    this.refreshAgain = false;
    this.followups = 0;
    if (forgetOwner) { this.owner = ""; this.pendingDelivery = null; }
    this.update({ ready: false, loading: false, state: null, messages: [], nextCursor: null, error: "", notice, viewEpoch: this.snapshot.viewEpoch + 1, draftEpoch: this.snapshot.draftEpoch + (forgetOwner ? 1 : 0) });
  }
  refreshLatest = () => {
    this.followups = 0;
    const ready = this.snapshot.ready;
    this.invalidate(false, "正在重新读取…", true);
    this.update({ ready });
    return this.refresh();
  };
  detail = async (message_ref: string, offset: number) => {
    const primary_ref = this.snapshot.state?.primary_ref;
    const epoch = this.epoch;
    if (!primary_ref || !this.client || !this.snapshot.ready) throw new Error("主对话未就绪");
    let result: Record<string, unknown>;
    try {
      result = await this.client.request("primary.messages.detail", { primary_ref, message_ref, offset, limit: 4096 });
    } catch (error) {
      if (epoch === this.epoch) {
        const ready = this.snapshot.ready;
        this.invalidate(false, "消息详情不可用；旧内容已清理，请刷新。", true);
        this.update({ ready });
      }
      throw error;
    }
    if (epoch !== this.epoch) throw new Error("内容已失效");
    if (result.message_ref !== message_ref || result.offset !== offset || typeof result.text !== "string" ||
        Array.from(result.text).length > 4096 || !Number.isSafeInteger(result.total_chars) ||
        (result.next_offset !== null && (!Number.isSafeInteger(result.next_offset) || Number(result.next_offset) !== offset + Array.from(result.text).length || Number(result.next_offset) <= offset)) ||
        Number(result.total_chars) < offset + Array.from(result.text).length ||
        (result.next_offset === null && Number(result.total_chars) !== offset + Array.from(result.text).length)) {
      throw new Error("消息详情格式无效");
    }
    return { text: result.text, next_offset: result.next_offset as number | null, total_chars: result.total_chars as number };
  };
  private onMessage = (raw: unknown) => {
    const message = record(raw), payload = record(message.payload);
    if (message.type === "companion_identity_unready" || message.type === "companion_control_rechallenge" || message.type === "companion_profile_unbound" ||
        (message.type === "companion_identity_status" && (payload.ready === false || payload.status === "unready"))) {
      this.invalidate(false, "身份不可用；旧消息已清理，未决发送等待重新绑定确认。");
      return;
    }
    if (message.type === "companion_identity_status" && payload.profile_id && this.owner &&
        this.owner !== `${String(payload.profile_id)}:${String(payload.profile_generation ?? "")}`) {
      this.invalidate(false, "身份已变化，等待当前连接重新绑定。");
      return;
    }
    if (message.type === "companion_profile_bound") {
      const owner = `${String(payload.profile_id ?? "")}:${String(payload.profile_generation ?? "")}`;
      if (!payload.profile_id || !payload.profile_generation || this.port.state() !== "connected") return;
      if (this.owner !== owner) this.invalidate(true, "正在读取主对话…");
      this.owner = owner;
      if (!this.snapshot.ready) {
        this.update({ ready: true, notice: "正在读取主对话…" });
        void this.refresh();
      }
      return;
    }
    if (["companion_projection_retracted", "human_memory_privacy_changed", "human_memory_invalidated", "human_memory_changed"].includes(String(message.type))) {
      const ready = this.snapshot.ready;
      this.invalidate(false, "内容已失效，正在重新读取…", true);
      this.update({ ready });
      if (ready) this.scheduleRefresh(100);
      return;
    }
    if (["permission_response_applied", "chat_v2_final", "chat_v2_error", "tool_result"].includes(String(message.type))) {
      this.followups = 0;
      this.scheduleRefresh(100);
    }
  };
  private scheduleRefresh(delay = 2_000) {
    if (!this.snapshot.ready || this.timer) return;
    this.timer = setTimeout(() => { this.timer = null; void this.refresh(); }, delay);
  }
  refresh = async (cursor: string | null = null): Promise<void> => {
    const client = this.client;
    if (!client || !this.snapshot.ready) return;
    if (this.reading) { this.refreshAgain = true; return; }
    this.reading = true;
    const epoch = this.epoch;
    this.update({ loading: true, error: "" });
    try {
      if (!this.snapshot.state) await client.request("primary.open", {});
      const state = parseState(await client.request("primary.state", {}));
      if (epoch !== this.epoch) return;
      const page = await client.request("primary.messages.page", { primary_ref: state.primary_ref, limit: 20, ...(cursor ? { cursor } : {}) });
      if (epoch !== this.epoch) return;
      if (page.primary_ref !== state.primary_ref) throw new Error("主对话历史归属不匹配");
      if (page.revision !== state.revision) throw new Error("历史读取期间状态已变化，请刷新。");
      const messages = parseMessages(page);
      this.update({ state, messages, nextCursor: typeof page.next_cursor === "string" ? page.next_cursor : null, notice: "", error: "", viewEpoch: this.snapshot.viewEpoch + 1 });
      if (state.current_run || state.queued_count) {
        if (this.followups < 12) { ++this.followups; this.scheduleRefresh(); }
        else this.update({ notice: "自动补读已暂停；运行可能仍在继续，可手动刷新状态。" });
      }
    } catch (error) {
      if (epoch === this.epoch) this.update({ state: null, messages: [], nextCursor: null, error: error instanceof Error ? error.message : "读取主对话失败" });
    } finally {
      if (epoch === this.epoch) {
        this.reading = false;
        this.update({ loading: false });
        if (this.refreshAgain) { this.refreshAgain = false; this.scheduleRefresh(100); }
      }
    }
  };
  submit = async (text: string, attachments: Record<string, unknown>[]): Promise<void> => {
    if (attachments.length) throw new Error("主对话附件接线尚未就绪；草稿与附件已保留。");
    if (!this.client || !this.snapshot.ready || !this.snapshot.state) throw new Error("主对话尚未就绪；草稿已保留。");
    if (this.submitting) throw new Error("正在等待上一条入队确认。");
    if (this.pendingDelivery && this.pendingDelivery.text !== text) throw new Error("上一条发送结果未知，请先以原文重试确认；不会自动提交不同消息。");
    const delivery = this.pendingDelivery ?? { text, delivery_key: crypto.randomUUID(), uncertain: false };
    this.pendingDelivery = delivery;
    this.submitting = true;
    const lease = this.lease;
    try {
      const ack = await this.client.request("queue.enqueue", { text: delivery.text, delivery_key: delivery.delivery_key });
      if (lease !== this.lease) throw new PrimaryRequestError("连接或隐私状态已变化；入队结果需重新确认。", true);
      if (ack.delivery_key !== delivery.delivery_key || typeof ack.turn_ref !== "string" || !ack.turn_ref || typeof ack.receipt_ref !== "string" || !ack.receipt_ref ||
          !Number.isSafeInteger(ack.enqueue_sequence) || Number(ack.enqueue_sequence) < 1 ||
          !(ack.scope_ref === null || typeof ack.scope_ref === "string") ||
          typeof ack.content_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(ack.content_sha256)) {
        throw new PrimaryRequestError("入队回执不完整，草稿保留。", true);
      }
      this.pendingDelivery = null;
      this.followups = 0;
      this.update({ notice: "已入队，等待执行。" });
      void this.refresh();
    } catch (error) {
      if (this.pendingDelivery === delivery) {
        // A later attempt's rejection cannot disprove an earlier durable commit.
        // Keep uncertainty on the delivery, rather than replacing it per attempt.
        delivery.uncertain ||= !(error instanceof PrimaryRequestError) || error.uncertain;
        if (!delivery.uncertain) this.pendingDelivery = null;
      }
      throw error;
    } finally { this.submitting = false; }
  };
  control = async (control: "pause" | "stop" | "cancel", run = this.snapshot.state?.current_run): Promise<void> => {
    if (!run || !this.client || !this.snapshot.ready || this.controlling) return;
    this.controlling = true;
    this.update({ notice: "", error: "" });
    const lease = this.lease;
    try {
      const receipt = await this.client.request("queue.control", { control, expected_run_ref: run.run_ref, expected_generation: run.generation });
      if (lease !== this.lease) return;
      if (receipt.run_ref !== run.run_ref || receipt.generation !== run.generation ||
          typeof receipt.receipt_ref !== "string" || !receipt.receipt_ref || typeof receipt.state !== "string") {
        throw new Error("控制回执目标不匹配或不完整；结果未确认，请刷新。");
      }
      const outcome = receipt.outcome;
      if (outcome === "signalled" || outcome === "already_requested") {
        this.update({ notice: outcome === "signalled" ? "控制请求已受理，等待运行状态确认。" : "该控制已提交，等待运行状态确认。", error: "" });
      } else if (outcome === "already_terminal") {
        this.update({ notice: "目标任务已结束；本次未发送控制。", error: "" });
      } else if (outcome === "superseded" || outcome === "stale" || outcome === "rejected") {
        this.update({ notice: "", error: `控制未应用（${outcome}），请刷新运行状态。` });
        return;
      } else {
        throw new Error("未知控制回执 outcome；未确认受理，请刷新。");
      }
      void this.refresh();
    } catch (error) {
      if (lease === this.lease) this.update({ error: error instanceof Error ? error.message : "控制请求失败" });
    } finally { this.controlling = false; }
  };
}
function parseState(raw: Record<string, unknown>): PrimaryState {
  const run = record(raw.current_run);
  if (typeof raw.primary_ref !== "string" || !raw.primary_ref || typeof raw.queued_count_truncated !== "boolean" || !Number.isSafeInteger(raw.queued_count) || Number(raw.queued_count) < 0 ||
      (typeof raw.revision !== "string" && typeof raw.revision !== "number") ||
      (raw.current_run !== null && (typeof run.run_ref !== "string" || !run.run_ref || !Number.isSafeInteger(run.generation) || typeof run.state !== "string"))) {
    throw new Error("主对话状态格式无效");
  }
  return raw as unknown as PrimaryState;
}
function parseMessages(page: Record<string, unknown>): PrimaryMessage[] {
  if (!Array.isArray(page.items) || page.items.length > 50) throw new Error("主对话历史格式无效");
  const ids = new Set<string>();
  return page.items.map((value) => {
    const m = record(value);
    if (typeof m.message_ref !== "string" || !m.message_ref || ids.has(m.message_ref) ||
        !["user", "assistant", "tool", "artifact"].includes(String(m.role)) || typeof m.text !== "string" || Array.from(m.text).length > 1024 || typeof m.has_more !== "boolean" || !Number.isSafeInteger(m.total_chars)) {
      throw new Error("主对话消息格式无效");
    }
    ids.add(m.message_ref);
    return m as unknown as PrimaryMessage;
  });
}
