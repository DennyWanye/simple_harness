// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChangeModelModal } from "./ChangeModelModal";
import { controlWS } from "./controlWs";
import { useSessionModelsStore } from "./sessionModelsStore";
import { useProvidersStore } from "./providersStore";

vi.mock("./controlWs", () => ({
  controlWS: {
    send: vi.fn(() => true),
  },
}));

const head = (default_model: string, models: string[]) => ({
  id: "relay",
  name: "Relay",
  enabled: true,
  default_model,
  models,
  incarnation_id: "inc-1",
  config_revision: 4,
});

describe("ChangeModelModal model filtering", () => {
  beforeEach(() => {
    vi.mocked(controlWS.send).mockClear();
    useProvidersStore.getState().set_providers([]);
    useSessionModelsStore.getState().set_catalog(
      [
        {
          id: "kimi-k3",
          label: "Kimi K3",
          caps: { thinking: true, fast: true, context: true, effort: false },
        },
        {
          id: "sf-glm-5.2",
          label: "GLM 5.2",
          caps: { thinking: true, fast: true, context: true, effort: false },
        },
        {
          id: "gpt-5.5",
          label: "GPT 5.5",
          caps: { thinking: false, fast: false, context: true, effort: true },
        },
      ],
      "live",
      "sf-glm-5.2",
    );
  });

  afterEach(() => cleanup());

  it("refreshes the authoritative model catalog whenever the picker opens", () => {
    render(<ChangeModelModal onClose={vi.fn()} />);

    expect(controlWS.send).toHaveBeenCalledWith({ type: "models_list" });
  });

  it("defaults an unconfirmed context value to 256K and persists it on save", () => {
    useSessionModelsStore.getState().set_catalog(
      [
        {
          id: "gpt-5.6-sol",
          label: "GPT 5.6 Sol",
          caps: { thinking: true, fast: true, context: true, effort: true },
          context_window: 400_000,
          supported_windows: [128_000, 256_000, 512_000, 1_000_000],
        },
      ],
      "live",
      "gpt-5.6-sol",
    );

    render(<ChangeModelModal onClose={vi.fn()} />);

    expect((screen.getByRole("combobox", { name: "上下文窗口" }) as HTMLSelectElement).value).toBe(
      "256000",
    );

    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(controlWS.send).toHaveBeenCalledWith({
      type: "model_context_set",
      payload: {
        scope: "global",
        model: "gpt-5.6-sol",
        fields: { context_window: 256_000 },
      },
    });
  });

  it("filters the dropdown immediately by model id or display label", () => {
    render(<ChangeModelModal onClose={vi.fn()} />);

    const filter = screen.getByRole("searchbox", { name: "筛选模型" });
    const select = screen.getByRole("combobox", { name: "模型" });
    fireEvent.change(filter, { target: { value: "KIMI" } });

    expect((select as HTMLSelectElement).value).toBe("");
    expect(
      within(select)
        .getAllByRole("option")
        .filter((option) => !(option as HTMLOptionElement).hidden)
        .map((option) => option.textContent),
    ).toEqual(["Kimi K3"]);

    fireEvent.change(select, { target: { value: "kimi-k3" } });
    expect((select as HTMLSelectElement).value).toBe("kimi-k3");
    expect((filter as HTMLInputElement).value).toBe("");

    fireEvent.change(filter, { target: { value: "5.2" } });
    expect(
      within(select)
        .getAllByRole("option")
        .filter((option) => !(option as HTMLOptionElement).hidden)
        .map((option) => option.textContent),
    ).toEqual(["sf-glm-5.2"]);
  });

  it("shows an explicit empty state when no model matches", () => {
    render(<ChangeModelModal onClose={vi.fn()} />);

    fireEvent.change(screen.getByRole("searchbox", { name: "筛选模型" }), {
      target: { value: "does-not-exist" },
    });

    expect(
      (
        screen.getByRole("option", {
          name: "没有匹配的模型",
        }) as HTMLOptionElement
      ).disabled,
    ).toBe(true);
  });

  it("切换模型后，上下文档位回到新模型的默认值，选过的档位不带过去", () => {
    useSessionModelsStore.getState().set_catalog(
      [
        {
          id: "model-a",
          label: "Model A",
          caps: { thinking: false, fast: false, context: true, effort: false },
          context_window: 512_000,
          supported_windows: [128_000, 256_000, 512_000, 1_000_000],
        },
        {
          id: "model-b",
          label: "Model B",
          caps: { thinking: false, fast: false, context: true, effort: false },
          context_window: 128_000,
          supported_windows: [128_000, 256_000, 1_000_000],
        },
      ],
      "live",
      "model-a",
    );
    useProvidersStore.getState().set_providers([head("model-a", ["model-a", "model-b"])]);
    render(<ChangeModelModal onClose={vi.fn()} />);
    const ctx = () => screen.getByRole("combobox", { name: "上下文窗口" }) as HTMLSelectElement;
    expect(ctx().value).toBe("512000");
    fireEvent.change(ctx(), { target: { value: "1000000" } });
    expect(ctx().value).toBe("1000000");

    fireEvent.change(screen.getByRole("combobox", { name: "模型" }), {
      target: { value: "model-b" },
    });
    expect(ctx().value).toBe("128000");

    // 不改档位直接保存：新模型保持自己的窗口，不发档位变更。
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(controlWS.send).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: "model_context_set" }),
    );
  });

  it("saves the chosen model as the head provider's default model, appending it to the list", () => {
    useProvidersStore.getState().set_providers([head("sf-glm-5.2", ["sf-glm-5.2"])]);
    render(<ChangeModelModal onClose={vi.fn()} />);
    fireEvent.change(screen.getByRole("combobox", { name: "模型" }), {
      target: { value: "kimi-k3" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(controlWS.send).toHaveBeenCalledWith({
      type: "settings_providers_update",
      payload: {
        id: "relay",
        patch: { models: ["sf-glm-5.2", "kimi-k3"], default_model: "kimi-k3" },
        expected_incarnation_id: "inc-1",
        expected_config_revision: 4,
      },
    });
    expect(controlWS.send).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: "session_set_model" }),
    );
  });

  it("does not update the provider when the model is unchanged, and offers no reasoning controls", () => {
    useProvidersStore.getState().set_providers([head("sf-glm-5.2", ["sf-glm-5.2", "kimi-k3"])]);
    render(<ChangeModelModal onClose={vi.fn()} />);
    expect(screen.queryByRole("group", { name: "推理模式" })).toBeNull();
    expect(screen.queryByRole("group", { name: "推理强度" })).toBeNull();
    expect(screen.queryByRole("button", { name: /清空/ })).toBeNull();
    expect(controlWS.send).toHaveBeenCalledWith({ type: "settings_providers_list_request" });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(controlWS.send).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: "settings_providers_update" }),
    );
  });
});
