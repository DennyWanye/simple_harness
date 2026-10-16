// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 弹窗层：探测到的模型并入草稿；编辑对象换了就按新对象重填。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AddProviderModal } from "./AddProviderModal";
import type { Provider } from "./providersStore";

afterEach(() => cleanup());

const provider = (id: string, name: string, models: string[]): Provider => ({
  id,
  source: "user",
  name,
  base_url: `https://${id}.example.com/v1`,
  models,
  default_model: models[0],
  api_key: "********",
  priority: 0,
  enabled: true,
});

const value = (testId: string) => (screen.getByTestId(testId) as HTMLInputElement).value;
const isDefault = (model: string) =>
  (screen.getByLabelText(`设 ${model} 为默认`) as HTMLInputElement).checked;

describe("AddProviderModal 弹窗", () => {
  it("挂载时已有探测结果就并入模型列表，第一项设为默认；之后的探测只追加新模型", () => {
    const base = { editing: null, onClose: vi.fn(), onSave: vi.fn() };
    const { rerender } = render(<AddProviderModal {...base} probedModels={["m-a", "m-b"]} />);
    expect(screen.getByText("m-a")).toBeTruthy();
    expect(screen.getByText("m-b")).toBeTruthy();
    expect(isDefault("m-a")).toBe(true);

    fireEvent.click(screen.getByLabelText("设 m-b 为默认"));
    rerender(<AddProviderModal {...base} probedModels={["m-b", "m-c"]} />);
    expect(screen.getByText("m-c")).toBeTruthy();
    expect(screen.getAllByText("m-b")).toHaveLength(1);
    expect(isDefault("m-b")).toBe(true);
  });

  it("编辑对象换了按新对象重填，并清掉没加进去的新模型输入", () => {
    const base = { onClose: vi.fn(), onSave: vi.fn() };
    const first = provider("relay-a", "Relay A", ["a-1"]);
    const { rerender } = render(<AddProviderModal {...base} editing={first} />);
    expect(value("provider-name-input")).toBe("Relay A");
    fireEvent.change(screen.getByTestId("provider-name-input"), { target: { value: "改了名" } });
    fireEvent.change(screen.getByTestId("provider-new-model-input"), {
      target: { value: "draft-model" },
    });

    rerender(<AddProviderModal {...base} editing={first} />);
    expect(value("provider-name-input")).toBe("改了名");

    rerender(<AddProviderModal {...base} editing={provider("relay-b", "Relay B", ["b-1"])} />);
    expect(value("provider-name-input")).toBe("Relay B");
    expect(value("provider-base-url-input")).toBe("https://relay-b.example.com/v1");
    expect(value("provider-new-model-input")).toBe("");
    expect(screen.getByText("b-1")).toBeTruthy();
    expect(screen.queryByText("a-1")).toBeNull();
  });
});
