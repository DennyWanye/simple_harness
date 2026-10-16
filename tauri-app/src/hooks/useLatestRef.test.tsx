// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useLatestRef } from "./useLatestRef";

describe("useLatestRef", () => {
  it("首次渲染即有值，重新渲染后回调读到最新提交的值", () => {
    const { result, rerender } = renderHook(({ value }) => useLatestRef(value), {
      initialProps: { value: "a" },
    });
    const ref = result.current;
    const read = () => ref.current;
    expect(read()).toBe("a");
    rerender({ value: "b" });
    expect(result.current).toBe(ref);
    expect(read()).toBe("b");
  });
});
