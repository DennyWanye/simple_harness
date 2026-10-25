import type { MissionsChannel } from "../stores/missionsStore";

export type Json = Record<string, unknown>;
export const record = (value: unknown): Json =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Json) : {};
export const rows = (value: unknown): Json[] => (Array.isArray(value) ? value.map(record) : []);
export const text = (value: unknown): string => (typeof value === "string" ? value : "");
export const SESSION_ID = "desktop-main";

export class SessionClient {
  private pending = new Map<
    string,
    {
      resolve: (data: unknown) => void;
      reject: (error: Error) => void;
      timer: ReturnType<typeof setTimeout>;
      operation: string;
    }
  >();
  private off: () => void;
  private offState?: () => void;
  private channel: MissionsChannel;
  constructor(channel: MissionsChannel) {
    this.channel = channel;
    this.off = channel.onMessage((incoming) => {
      const message = record(incoming);
      const payload = record(message.payload);
      const id = text(payload.request_id);
      const request = this.pending.get(id);
      if (!request || message.type !== `session_${request.operation}_response`) return;
      this.pending.delete(id);
      clearTimeout(request.timer);
      if (payload.ok === true) request.resolve(payload.data);
      else
        request.reject(
          new Error(`${text(payload.error) || "会话请求未完成"} (${text(payload.error_code)})`),
        );
    });
    this.offState = channel.onStateChange?.((state) => {
      if (state !== "connected")
        this.rejectPending("连接已断开；发送结果可能已提交，请刷新后用原输入键重试。");
    });
  }
  request(operation: string, payload: Json = {}): Promise<unknown> {
    const id = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => {
          this.pending.delete(id);
          reject(new Error("请求结果尚未确认，请刷新后重试。"));
        },
        operation === "send" ? 210000 : 20000,
      );
      this.pending.set(id, { resolve, reject, timer, operation });
      if (!this.channel.send({ type: `session_${operation}`, request_id: id, payload })) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new Error("连接不可用，请稍后重试。"));
      }
    });
  }
  private rejectPending(message: string) {
    this.pending.forEach((request) => {
      clearTimeout(request.timer);
      request.reject(new Error(message));
    });
    this.pending.clear();
  }
  close() {
    this.off();
    this.offState?.();
    this.rejectPending("会话视图已关闭。");
  }
}
