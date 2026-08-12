// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SettingsProviders, type Provider } from "./SettingsProviders";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ControlChannel, ConnectionState } from "../ws/ControlChannel";

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
});
