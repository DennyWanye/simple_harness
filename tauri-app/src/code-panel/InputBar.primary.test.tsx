import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InputBar } from "./InputBar";
import { controlWS } from "./controlWs";
vi.mock("./controlWs", () => ({ controlWS: { send: vi.fn(), state: () => "connected", on_message: () => () => {} } }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
describe("primary composer acceptance", () => {
  it("retains the draft until ACK and prevents duplicate Enter while pending", async () => {
    let ack!: () => void;
    const submit = vi.fn(() => new Promise<void>((resolve) => { ack = resolve; }));
    render(<InputBar primary={{ submit }} placeholder="primary" />);
    const input = screen.getByPlaceholderText("primary");
    fireEvent.change(input, { target: { value: "durable hello" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(submit).toHaveBeenCalledTimes(1);
    expect((input as HTMLTextAreaElement).value).toBe("durable hello");
    expect(controlWS.send).not.toHaveBeenCalled();
    ack();
    await waitFor(() => expect((input as HTMLTextAreaElement).value).toBe(""));
  });
  it("retains rejected draft and reports the rejection without legacy fallback", async () => {
    render(<InputBar primary={{ submit: async () => { throw new Error("queue_rejected"); } }} placeholder="primary" />);
    const input = screen.getByPlaceholderText("primary");
    fireEvent.change(input, { target: { value: "keep me" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText("queue_rejected");
    expect((input as HTMLTextAreaElement).value).toBe("keep me");
    expect(controlWS.send).not.toHaveBeenCalled();
  });
  it("does not interpret slash commands using the legacy session path", async () => {
    const submit = vi.fn(async () => {});
    render(<InputBar primary={{ submit }} placeholder="primary" />);
    fireEvent.change(screen.getByPlaceholderText("primary"), { target: { value: "/plan-bs hello" } });
    fireEvent.keyDown(screen.getByPlaceholderText("primary"), { key: "Enter" });
    await screen.findByText(/主对话命令接线尚未就绪/);
    expect(submit).not.toHaveBeenCalled();
    expect(controlWS.send).not.toHaveBeenCalled();
  });
});
