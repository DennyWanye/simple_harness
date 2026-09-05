// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

export interface PrimaryWireRequest {
  type: "human_memory_request";
  request_id: string;
  operation: string;
  request: Record<string, unknown>;
}
export interface PrimaryRequestPort {
  send_command(message: PrimaryWireRequest): boolean;
  on_message(listener: (message: unknown) => void): () => void;
}
export class PrimaryRequestError extends Error {
  readonly uncertain: boolean;
  constructor(message: string, uncertain = false) { super(message); this.uncertain = uncertain; }
}
export function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}
export class PrimaryRequests {
  private pending = new Map<string, {
    operation: string;
    resolve: (result: Record<string, unknown>) => void;
    reject: (error: Error) => void;
    timer: ReturnType<typeof setTimeout>;
  }>();
  private off: () => void;
  private readonly port: PrimaryRequestPort;
  constructor(port: PrimaryRequestPort) {
    this.port = port;
    this.off = port.on_message((raw) => {
      const message = record(raw);
      if (message.type !== "human_memory_response") return;
      const id = String(message.request_id ?? "");
      const pending = this.pending.get(id);
      if (!pending) return;
      this.pending.delete(id);
      clearTimeout(pending.timer);
      const payload = record(message.payload);
      if (payload.ok !== true) {
        pending.reject(new PrimaryRequestError(String(record(payload.error).code ?? "主对话请求失败")));
      } else if (payload.operation !== pending.operation) {
        pending.reject(new PrimaryRequestError("响应操作不匹配，结果未确认", true));
      } else if (payload.result === null || typeof payload.result !== "object" || Array.isArray(payload.result)) {
        pending.reject(new PrimaryRequestError("主对话响应格式无效，结果未确认", true));
      } else {
        pending.resolve(record(payload.result));
      }
    });
  }
  request(operation: string, request: Record<string, unknown>): Promise<Record<string, unknown>> {
    const request_id = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(request_id);
        reject(new PrimaryRequestError("响应超时，结果未确认；草稿保留，请刷新后重试原消息。", true));
      }, 15_000);
      this.pending.set(request_id, { operation, resolve, reject, timer });
      if (!this.port.send_command({ type: "human_memory_request", request_id, operation, request })) {
        this.pending.delete(request_id);
        clearTimeout(timer);
        reject(new PrimaryRequestError("控制通道未连接，草稿已保留。"));
      }
    });
  }
  invalidate(readsOnly = false): void {
    for (const [id, pending] of this.pending) {
      if (readsOnly && !pending.operation.startsWith("primary.")) continue;
      this.pending.delete(id);
      clearTimeout(pending.timer);
      pending.reject(new PrimaryRequestError("读取已失效或连接中断；已发送请求的结果未确认。", true));
    }
  }
  dispose(): void { this.invalidate(); this.off(); }
}
