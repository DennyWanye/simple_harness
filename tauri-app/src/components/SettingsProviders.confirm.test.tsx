// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SettingsProviders } from "./SettingsProviders";
import { type Provider } from "./providersStore";
import type { ControlMessage } from "../types/messages";
import type { ControlChannel, ConnectionState } from "../ws/ControlChannel";

afterEach(cleanup);

class FakeProviderChannel {
  state: ConnectionState = "connected";
  sent: ControlMessage[] = [];
  send(message: ControlMessage) {
    this.sent.push(message);
    return true;
  }
  listeners = new Set<(message: unknown) => void>();
  onMessage(listener: (message: unknown) => void) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
  emit(message: unknown) {
    act(() => this.listeners.forEach((listener) => listener(message)));
  }
  private states = new Set<(state: ConnectionState) => void>();
  onStateChange(fn: (state: ConnectionState) => void) {
    this.states.add(fn);
    return () => this.states.delete(fn);
  }
  // 推"connected"：之后 send 照常记下消息。
  connect() {
    act(() => {
      this.state = "connected";
      this.states.forEach((fn) => fn("connected"));
    });
  }
}

const provider: Provider = {
  id: "delete-fixture",
  name: "Delete Fixture",
  base_url: "http://127.0.0.1:9/v1",
  models: ["fixture-model"],
  default_model: "fixture-model",
  api_key: "********",
  priority: 1,
  enabled: false,
};

describe("SettingsProviders delete confirmation", () => {
  it("requires the in-app dialog before sending the remove mutation", async () => {
    const channel = new FakeProviderChannel();
    render(<SettingsProviders getChannel={() => channel as unknown as ControlChannel} />);
    channel.emit({ type: "providers_changed", payload: { providers: [provider] } });

    fireEvent.click(await screen.findByTestId("provider-delete-btn-delete-fixture"));
    const dialog = screen.getByRole("dialog", { name: "删除 Provider" });
    expect(dialog.textContent).toContain("Delete Fixture");
    expect(channel.sent.some((message) => message.type === "settings_providers_remove")).toBe(
      false,
    );

    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog", { name: "删除 Provider" })).toBeNull();

    fireEvent.click(screen.getByTestId("provider-delete-btn-delete-fixture"));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "删除 Provider" })).getByRole("button", {
        name: "删除",
      }),
    );
    await waitFor(() =>
      expect(
        channel.sent.some(
          (message) =>
            message.type === "settings_providers_remove" &&
            message.payload?.id === "delete-fixture",
        ),
      ).toBe(true),
    );
  });

  it("keeps the add dialog open when the control channel is disconnected", async () => {
    const channel = new FakeProviderChannel();
    channel.state = "disconnected";
    render(<SettingsProviders getChannel={() => channel as unknown as ControlChannel} />);

    fireEvent.click(screen.getByTestId("provider-add-button"));
    const dialog = screen.getByRole("dialog", { name: "添加 provider" });
    fireEvent.change(within(dialog).getByTestId("provider-name-input"), {
      target: { value: "Offline Provider" },
    });
    fireEvent.change(within(dialog).getByTestId("provider-base-url-input"), {
      target: { value: "https://offline.example/v1" },
    });
    fireEvent.change(within(dialog).getByTestId("provider-api-key-input"), {
      target: { value: "sk-offline" },
    });
    fireEvent.change(within(dialog).getByPlaceholderText("新 model 名（例：claude-sonnet-4-5）"), {
      target: { value: "offline-model" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "+ 添加" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "添加" }));

    expect(screen.getByRole("dialog", { name: "添加 provider" })).not.toBeNull();
    expect(screen.getAllByText("控制通道未连接").length).toBeGreaterThan(0);
    expect(channel.sent.some((message) => message.type === "settings_providers_add")).toBe(false);
  });

  it("probes an edited provider by id without exposing its saved key", async () => {
    const channel = new FakeProviderChannel();
    render(<SettingsProviders getChannel={() => channel as unknown as ControlChannel} />);
    channel.emit({ type: "providers_changed", payload: { providers: [provider] } });

    fireEvent.click(await screen.findByTestId("provider-edit-btn-delete-fixture"));
    const dialog = screen.getByRole("dialog", { name: "编辑 provider" });
    fireEvent.click(within(dialog).getByTestId("provider-probe-models-button"));

    const probe = channel.sent.find(
      (message) => message.type === "settings_providers_probe_models",
    );
    expect(probe?.payload).toMatchObject({
      provider_id: "delete-fixture",
      base_url: "http://127.0.0.1:9/v1",
      api_key: "",
    });
  });

  it("keeps the dialog open until the backend acknowledges the save", async () => {
    const channel = new FakeProviderChannel();
    render(<SettingsProviders getChannel={() => channel as unknown as ControlChannel} />);
    channel.emit({ type: "providers_changed", payload: { providers: [provider] } });

    fireEvent.click(await screen.findByTestId("provider-edit-btn-delete-fixture"));
    const dialog = screen.getByRole("dialog", { name: "编辑 provider" });
    fireEvent.click(within(dialog).getByTestId("provider-save-button"));
    expect(screen.getByRole("dialog", { name: "编辑 provider" })).not.toBeNull();
    expect(
      (within(dialog).getByRole("button", { name: "保存中…" }) as HTMLButtonElement).disabled,
    ).toBe(true);

    channel.emit({ type: "settings_providers_updated", payload: { provider } });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑 provider" })).toBeNull());
  });

  it("探测回执与错误都从控制通道读到：模型并入编辑框，错误显示出来", async () => {
    const channel = new FakeProviderChannel();
    render(<SettingsProviders getChannel={() => channel as unknown as ControlChannel} />);
    channel.emit({ type: "providers_changed", payload: { providers: [provider] } });

    fireEvent.click(await screen.findByTestId("provider-edit-btn-delete-fixture"));
    const dialog = screen.getByRole("dialog", { name: "编辑 provider" });
    fireEvent.click(within(dialog).getByTestId("provider-probe-models-button"));
    channel.emit({
      type: "settings_providers_probe_models_response",
      payload: { ok: true, models: ["probed-model"] },
    });
    expect(within(dialog).getByText("probed-model")).toBeTruthy();

    channel.emit({
      type: "settings_providers_error",
      payload: { reason: "invalid", detail: "名称重复" },
    });
    expect(screen.getAllByText("名称重复").length).toBeGreaterThan(0);
  });

  it("通道换成新对象后，服务商事件从新通道读到", async () => {
    const first = new FakeProviderChannel();
    const second = new FakeProviderChannel();
    const view = render(
      <SettingsProviders getChannel={() => first as unknown as ControlChannel} />,
    );
    view.rerender(<SettingsProviders getChannel={() => second as unknown as ControlChannel} />);
    expect(first.listeners.size).toBe(0);
    second.emit({ type: "providers_changed", payload: { providers: [provider] } });
    expect(await screen.findByTestId("provider-edit-btn-delete-fixture")).toBeTruthy();
    second.emit({ type: "settings_providers_error", payload: { detail: "新通道上的错误" } });
    expect(screen.getAllByText("新通道上的错误").length).toBeGreaterThan(0);
  });

  // 复核要求：换到未连接的新通道 B 并连上后，保存回执与探测回执都从 B 读到。
  it("通道换成新对象：新通道上的保存回执关掉弹窗，探测回执把模型显示到编辑框", async () => {
    const first = new FakeProviderChannel();
    const second = new FakeProviderChannel();
    second.state = "connecting";
    const view = render(
      <SettingsProviders getChannel={() => first as unknown as ControlChannel} />,
    );
    first.emit({ type: "providers_changed", payload: { providers: [provider] } });

    view.rerender(<SettingsProviders getChannel={() => second as unknown as ControlChannel} />);
    second.connect();

    fireEvent.click(await screen.findByTestId("provider-edit-btn-delete-fixture"));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "编辑 provider" })).getByTestId(
        "provider-save-button",
      ),
    );
    second.emit({ type: "settings_providers_updated", payload: { provider } });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑 provider" })).toBeNull());

    fireEvent.click(screen.getByTestId("provider-edit-btn-delete-fixture"));
    const dialog = screen.getByRole("dialog", { name: "编辑 provider" });
    fireEvent.click(within(dialog).getByTestId("provider-probe-models-button"));
    expect(second.sent.some((message) => message.type === "settings_providers_probe_models")).toBe(
      true,
    );
    second.emit({
      type: "settings_providers_probe_models_response",
      payload: { ok: true, models: ["new-channel-model"] },
    });
    expect(within(dialog).getByText("new-channel-model")).toBeTruthy();
    expect(first.sent.some((message) => message.type === "settings_providers_probe_models")).toBe(
      false,
    );
  });
});
