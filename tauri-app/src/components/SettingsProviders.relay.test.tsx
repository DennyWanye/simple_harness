// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WI-4 relay-managed provider UI tests.
 *
 * These mount the real components because WI-4 is about visible row state:
 * relay registry rows are no longer frontend-only virtual rows, but they
 * still need restricted editing affordances.
 */
import React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Provider } from "./SettingsProviders";
import { AddProviderModal } from "./AddProviderModal";
import { SettingsProviders } from "./SettingsProviders";
import { relayProviderRegistration } from "../auth/relayProviderRegistration";

const dndMocks = vi.hoisted(() => ({
  useSortable: vi.fn(),
}));

vi.mock("@dnd-kit/core", () => ({
  DndContext: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  closestCenter: vi.fn(),
  KeyboardSensor: vi.fn(),
  PointerSensor: vi.fn(),
  useSensor: vi.fn((sensor, opts) => ({ sensor, opts })),
  useSensors: vi.fn((...sensors) => sensors),
}));

vi.mock("@dnd-kit/sortable", () => ({
  SortableContext: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  sortableKeyboardCoordinates: vi.fn(),
  verticalListSortingStrategy: {},
  arrayMove: <T,>(items: T[], from: number, to: number) => {
    const next = [...items];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    return next;
  },
  useSortable: dndMocks.useSortable,
}));

vi.mock("@dnd-kit/utilities", () => ({
  CSS: { Transform: { toString: vi.fn(() => undefined) } },
}));

vi.mock("../auth/relayProviderRegistration", () => ({
  relayProviderRegistration: {
    // Default to success so callers that don't care about the result still
    // exercise the happy-path feedback; individual tests override per-case.
    recover: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const relayProvider: Provider = {
  id: "relay-cloud",
  source: "relay",
  account_ref: "acct_1",
  name: "中转站 · chinzy",
  base_url: "https://relay.example.com/v1",
  models: ["gpt-4o-mini", "claude-sonnet-4-5"],
  default_model: "gpt-4o-mini",
  model: "gpt-4o-mini",
  api_key: "********",
  priority: 1,
  enabled: true,
};

const userProvider: Provider = {
  id: "user-openai",
  source: "user",
  name: "OpenAI",
  base_url: "https://api.openai.com/v1",
  models: ["gpt-4o"],
  default_model: "gpt-4o",
  model: "gpt-4o",
  api_key: "********",
  priority: 2,
  enabled: true,
};

function renderSettings(providers: Provider[]) {
  const send = vi.fn();
  const channel = { state: "connected" as const, send };
  const relayAdapter = { currentUser: vi.fn() };
  render(
    <SettingsProviders
      getChannel={() => channel as any}
      lastMessage={{
        type: "settings_providers_list_response",
        payload: { providers },
      } as any}
      relayAdapter={relayAdapter as any}
    />,
  );
  return { send, relayAdapter };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SettingsProviders relay-managed rows", () => {
  it("renders relay row as draggable, enabled, default-model editable, and reset-key instead of delete", () => {
    dndMocks.useSortable.mockReturnValue({
      attributes: { "data-dnd-attributes": "relay" },
      listeners: { "data-dnd-listeners": "relay" },
      setNodeRef: vi.fn(),
      transform: null,
      transition: undefined,
      isDragging: false,
    });
    const { send } = renderSettings([relayProvider, userProvider]);

    const relayRow = screen.getByTestId("provider-row-relay-cloud");
    expect(within(relayRow).getByTestId("provider-relay-badge").textContent).toBe("relay");
    expect(within(relayRow).getByTestId("provider-reset-key-btn-relay-cloud")).toBeTruthy();
    expect(within(relayRow).queryByTestId("provider-delete-btn-relay-cloud")).toBeNull();
    const enabledCheckbox = within(relayRow).getByLabelText(/启用|鍚/);
    expect(enabledCheckbox).toHaveProperty("checked", true);
    fireEvent.click(enabledCheckbox);
    expect(send).toHaveBeenCalledWith({
      type: "settings_providers_update",
      payload: {
        id: "relay-cloud",
        patch: { enabled: false },
      },
    });

    const defaultModel = within(relayRow).getByTestId("provider-default-model-select-relay-cloud");
    fireEvent.change(defaultModel, { target: { value: "claude-sonnet-4-5" } });
    expect(send).toHaveBeenCalledWith({
      type: "settings_providers_update",
      payload: {
        id: "relay-cloud",
        patch: { default_model: "claude-sonnet-4-5" },
      },
    });

    const relaySortableCall = dndMocks.useSortable.mock.calls.find(
      ([arg]) => arg.id === "relay-cloud",
    );
    expect(relaySortableCall?.[0]).toMatchObject({ id: "relay-cloud", disabled: false });
  });

  it("clicking relay reset key calls relayProviderRegistration.recover with relayAdapter", async () => {
    dndMocks.useSortable.mockReturnValue({
      attributes: {},
      listeners: {},
      setNodeRef: vi.fn(),
      transform: null,
      transition: undefined,
      isDragging: false,
    });
    const { relayAdapter } = renderSettings([relayProvider]);

    fireEvent.click(screen.getByTestId("provider-reset-key-btn-relay-cloud"));

    expect(relayProviderRegistration.recover).toHaveBeenCalledWith(relayAdapter);
    // flush the async success feedback so no act() warning / dangling state
    expect(
      (await screen.findByTestId("provider-reset-status-relay-cloud")).textContent,
    ).toContain("已重置");
  });

  it("keeps user rows unchanged with no relay badge and normal delete button", () => {
    dndMocks.useSortable.mockReturnValue({
      attributes: {},
      listeners: {},
      setNodeRef: vi.fn(),
      transform: null,
      transition: undefined,
      isDragging: false,
    });
    renderSettings([userProvider]);

    const userRow = screen.getByTestId("provider-row-user-openai");
    expect(within(userRow).queryByTestId("provider-relay-badge")).toBeNull();
    expect(within(userRow).getByTestId("provider-delete-btn-user-openai")).toBeTruthy();
    expect(within(userRow).queryByTestId("provider-reset-key-btn-user-openai")).toBeNull();
  });
});

describe("SettingsProviders reset-key feedback (boundaries)", () => {
  function setupRow() {
    dndMocks.useSortable.mockReturnValue({
      attributes: {},
      listeners: {},
      setNodeRef: vi.fn(),
      transform: null,
      transition: undefined,
      isDragging: false,
    });
  }

  it("shows pending + disables the button while rotating, blocking double-clicks", async () => {
    setupRow();
    let resolveRecover!: (r: { ok: true }) => void;
    vi.mocked(relayProviderRegistration.recover).mockReturnValueOnce(
      new Promise<{ ok: true }>((r) => {
        resolveRecover = r;
      }) as Promise<any>,
    );
    renderSettings([relayProvider]);
    const btn = screen.getByTestId("provider-reset-key-btn-relay-cloud");

    fireEvent.click(btn);
    // in-flight: disabled + busy + label changed
    expect(btn).toHaveProperty("disabled", true);
    expect(btn.getAttribute("aria-busy")).toBe("true");
    expect(btn.textContent).toContain("重置中");

    // second click while pending must be a no-op (button disabled)
    fireEvent.click(btn);
    expect(relayProviderRegistration.recover).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveRecover({ ok: true });
    });
    expect(screen.getByTestId("provider-reset-status-relay-cloud").textContent).toContain(
      "已重置",
    );
    expect(screen.getByTestId("provider-reset-key-btn-relay-cloud")).toHaveProperty(
      "disabled",
      false,
    );
  });

  it("maps a failure reason to an actionable message (empty_models -> 重新登录)", async () => {
    setupRow();
    vi.mocked(relayProviderRegistration.recover).mockResolvedValueOnce({
      ok: false,
      reason: "empty_models",
    } as any);
    renderSettings([relayProvider]);

    fireEvent.click(screen.getByTestId("provider-reset-key-btn-relay-cloud"));

    const status = await screen.findByTestId("provider-reset-status-relay-cloud");
    expect(status.textContent).toContain("请重新登录");
  });

  it("repeated_failure surfaces the re-login / balance hint", async () => {
    setupRow();
    vi.mocked(relayProviderRegistration.recover).mockResolvedValueOnce({
      ok: false,
      reason: "repeated_failure",
    } as any);
    renderSettings([relayProvider]);

    fireEvent.click(screen.getByTestId("provider-reset-key-btn-relay-cloud"));

    const status = await screen.findByTestId("provider-reset-status-relay-cloud");
    expect(status.textContent).toContain("反复失效");
  });

  it("without a relay session, prompts to log in and never calls recover", async () => {
    setupRow();
    const send = vi.fn();
    const channel = { state: "connected" as const, send };
    render(
      <SettingsProviders
        getChannel={() => channel as any}
        lastMessage={
          {
            type: "settings_providers_list_response",
            payload: { providers: [relayProvider] },
          } as any
        }
        relayAdapter={null as any}
      />,
    );

    fireEvent.click(screen.getByTestId("provider-reset-key-btn-relay-cloud"));

    const status = await screen.findByTestId("provider-reset-status-relay-cloud");
    expect(status.textContent).toContain("请先登录");
    expect(relayProviderRegistration.recover).not.toHaveBeenCalled();
  });
});

describe("AddProviderModal relay-managed editing", () => {
  it("disables relay id, base_url, and api_key fields and shows reset guidance", () => {
    render(
      <AddProviderModal
        editing={relayProvider}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    expect(screen.getByTestId("provider-id-input")).toHaveProperty("disabled", true);
    expect(screen.getByTestId("provider-base-url-input")).toHaveProperty("disabled", true);
    expect(screen.getByTestId("provider-api-key-input")).toHaveProperty("disabled", true);
    expect(screen.getByText(/重置 key/)).toBeTruthy();
  });
});
