import { describe, expect, it, vi } from "vitest";
import type { ControlWS } from "../code-panel/controlWs";
import { primaryRunChannel } from "./runChannel";
const run = { run_ref: "foreground-A", execution_session_ref: "hidden-exec-A", sdk_run_ref: "sdk-A" };
function fixture() {
  let receive: (message: unknown) => void = () => {};
  const send = vi.fn(() => true);
  const port = { send_command: send, on_message: (listener: typeof receive) => { receive = listener; return () => {}; } } as unknown as ControlWS;
  return { channel: primaryRunChannel(port, run), send, emit: (value: unknown) => receive(value) };
}
describe("primary execution event binding", () => {
  it("accepts real hidden execution IDs, rejects primary fake sid and other runs", () => {
    const h = fixture(), received = vi.fn(); h.channel.on_message(received);
    const request = { type: "permission_request", payload: { session_id: "hidden-exec-A", run_id: "sdk-A", decision_id: "permission" } };
    h.emit(request);
    h.emit({ ...request, payload: { ...request.payload, session_id: "primary-real" } });
    h.emit({ ...request, payload: { ...request.payload, run_id: "sdk-B" } });
    expect(received).toHaveBeenCalledTimes(1);
    expect(received).toHaveBeenCalledWith(request);
  });
  it("re-reads exact execution permissions and preserves nonce/version in replies", () => {
    const h = fixture();
    h.channel.send({ type: "permissions_pending_list", payload: {} });
    expect(h.send).toHaveBeenCalledWith(expect.objectContaining({ payload: { session_id: "hidden-exec-A" } }));
    const payload = { session_id: "hidden-exec-A", run_id: "sdk-A", request_id: "r", decision_id: "d", nonce: "n", version: 0, decision: "allow" };
    h.channel.send({ type: "permission_response", payload });
    expect(h.send).toHaveBeenLastCalledWith(expect.objectContaining({ type: "permission_response", payload }));
    expect(h.channel.send({ type: "chat_v2_interrupt" })).toBe(false);
  });
  it("filters mixed pending snapshots and carries exact directory errors without a session field", () => {
    const h = fixture(), received = vi.fn(); h.channel.on_message(received);
    const exact = { session_id: "hidden-exec-A", run_id: "sdk-A", decision_id: "d" };
    h.emit({ type: "permissions_pending_list_response", payload: { pending: [exact, { ...exact, run_id: "other" }] } });
    expect(received).toHaveBeenLastCalledWith({ type: "permissions_pending_list_response", payload: { pending: [exact] } });
    h.emit({ type: "project_directory_error", payload: { decision_id: "d", error: "invalid" } });
    expect(received).toHaveBeenCalledTimes(2);
  });
});
