// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SettingsProviders, type Provider } from "./SettingsProviders";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ControlChannel, ConnectionState } from "../ws/ControlChannel";

afterEach(cleanup);

class FakeProviderChannel {
  state: ConnectionState = "connected";
  sent: ControlMessage[] = [];
  send(message: ControlMessage) {
    this.sent.push(message);
    return true;
  }
  onMessage() {
    return () => undefined;
  }
  onStateChange() {
    return () => undefined;
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
    render(
      <SettingsProviders
        getChannel={() => channel as unknown as ControlChannel}
        lastMessage={{
          type: "providers_changed",
          payload: { providers: [provider] },
        } as unknown as IncomingMessage}
      />,
    );

    fireEvent.click(await screen.findByTestId("provider-delete-btn-delete-fixture"));
    const dialog = screen.getByRole("dialog", { name: "删除 Provider" });
    expect(dialog.textContent).toContain("Delete Fixture");
    expect(channel.sent.some((message) => message.type === "settings_providers_remove")).toBe(false);

    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog", { name: "删除 Provider" })).toBeNull();

    fireEvent.click(screen.getByTestId("provider-delete-btn-delete-fixture"));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "删除 Provider" })).getByRole(
        "button",
        { name: "删除" },
      ),
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
    render(
      <SettingsProviders
        getChannel={() => channel as unknown as ControlChannel}
        lastMessage={null}
      />,
    );

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
    render(
      <SettingsProviders
        getChannel={() => channel as unknown as ControlChannel}
        lastMessage={{
          type: "providers_changed",
          payload: { providers: [provider] },
        } as unknown as IncomingMessage}
      />,
    );

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
    const initial = {
      type: "providers_changed",
      payload: { providers: [provider] },
    } as unknown as IncomingMessage;
    const view = render(
      <SettingsProviders
        getChannel={() => channel as unknown as ControlChannel}
        lastMessage={initial}
      />,
    );

    fireEvent.click(await screen.findByTestId("provider-edit-btn-delete-fixture"));
    const dialog = screen.getByRole("dialog", { name: "编辑 provider" });
    fireEvent.click(within(dialog).getByTestId("provider-save-button"));
    expect(screen.getByRole("dialog", { name: "编辑 provider" })).not.toBeNull();
    expect(
      (within(dialog).getByRole("button", { name: "保存中…" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    view.rerender(
      <SettingsProviders
        getChannel={() => channel as unknown as ControlChannel}
        lastMessage={{
          type: "settings_providers_updated",
          payload: { provider },
        } as unknown as IncomingMessage}
      />,
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "编辑 provider" })).toBeNull(),
    );
  });
});
