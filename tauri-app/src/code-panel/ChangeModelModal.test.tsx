// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChangeModelModal } from "./ChangeModelModal";
import { controlWS } from "./controlWs";
import { useSessionModelsStore } from "./sessionModelsStore";

vi.mock("./controlWs", () => ({
  controlWS: {
    send: vi.fn(() => true),
  },
}));

describe("ChangeModelModal model filtering", () => {
  beforeEach(() => {
    vi.mocked(controlWS.send).mockClear();
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
    render(
      <ChangeModelModal
        session_id="session-1"
        current_model={null}
        onClose={vi.fn()}
      />,
    );

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

    render(
      <ChangeModelModal
        session_id="session-1"
        current_model={null}
        onClose={vi.fn()}
      />,
    );

    expect(
      (screen.getByRole("combobox", { name: "上下文窗口" }) as HTMLSelectElement)
        .value,
    ).toBe("256000");

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
    render(
      <ChangeModelModal
        session_id="session-1"
        current_model={null}
        onClose={vi.fn()}
      />,
    );

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
    render(
      <ChangeModelModal
        session_id="session-1"
        current_model={null}
        onClose={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByRole("searchbox", { name: "筛选模型" }), {
      target: { value: "does-not-exist" },
    });

    expect(
      (screen.getByRole("option", {
        name: "没有匹配的模型",
      }) as HTMLOptionElement).disabled,
    ).toBe(true);
  });
});
