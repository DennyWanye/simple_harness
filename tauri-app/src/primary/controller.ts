// Types retained for the workspace and execution inspectors.
// Desktop text ingress is implemented by SessionClient.
import type { PrimaryRequestPort } from "./requests";
import type { ContextUsageSnapshot } from "../types/messages";

export interface PrimaryPort extends PrimaryRequestPort {
  state(): "connected" | "connecting" | "disconnected";
  on_state_change(
    listener: (state: "connected" | "connecting" | "disconnected") => void,
  ): () => void;
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
  role: "user" | "assistant" | "tool" | "artifact" | "reminder";
  notice_id?: string;
  has_more: boolean;
  total_chars: number;
  delivery_key?: string;
  text: string;
  run_ref?: string;
  turn_ref?: string;
}
export interface PrimarySnapshot {
  ready: boolean;
  verifiedOwnerKey: string | null;
  /** Opaque primary identity for this verified connection, not cached read content. */
  primaryRef: string | null;
  loading: boolean;
  state: PrimaryState | null;
  messages: PrimaryMessage[];
  nextCursor: string | null;
  error: string;
  notice: string;
  viewEpoch: number;
  draftEpoch: number;
  /** 本主对话最近一轮运行的上下文用量（primary.context.usage）；没有测量或读失败时为 null。 */
  contextUsage: ContextUsageSnapshot | null;
}
