// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { useDepsChanged } from "./useDepsChanged";

function Draft({ owner, other }: { owner: string; other: number }) {
  const [resets, setResets] = useState(0);
  if (useDepsChanged([owner])) setResets((n) => n + 1);
  return (
    <span data-testid="resets">
      {resets}:{other}
    </span>
  );
}

describe("useDepsChanged", () => {
  it("首次渲染不算变化；依赖变了算一次；无关重渲染与新数组字面量不算", () => {
    const { rerender } = render(<Draft owner="a" other={0} />);
    expect(screen.getByTestId("resets").textContent).toBe("0:0");
    rerender(<Draft owner="a" other={1} />);
    expect(screen.getByTestId("resets").textContent).toBe("0:1");
    rerender(<Draft owner="b" other={1} />);
    expect(screen.getByTestId("resets").textContent).toBe("1:1");
    rerender(<Draft owner="b" other={2} />);
    expect(screen.getByTestId("resets").textContent).toBe("1:2");
  });
});
