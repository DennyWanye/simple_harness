// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { act, cleanup, render, screen } from "@testing-library/react";
import { Profiler } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useCompanionProvisionalValues } from "./companionSelectors";
import { useSessionsStore } from "./sessionsStore";

let renders = 0;

function Subscriber() {
  const values = useCompanionProvisionalValues();
  return <div data-testid="values">{values.join("|")}</div>;
}

// 用 Profiler 数提交次数（渲染期不改模块变量）。
function CountedSubscriber() {
  return (
    <Profiler id="subscriber" onRender={() => (renders += 1)}>
      <Subscriber />
    </Profiler>
  );
}

beforeEach(() => {
  renders = 0;
  useSessionsStore.setState({
    companion_provisional_streams: {},
    inflight_count: 0,
  });
});

afterEach(cleanup);

describe("useCompanionProvisionalValues", () => {
  it("keeps a stable external-store snapshot and does not loop on unrelated updates", () => {
    render(<CountedSubscriber />);
    expect(renders).toBe(1);

    act(() => {
      useSessionsStore.setState({ inflight_count: 1 });
    });
    expect(renders).toBe(1);

    act(() => {
      useSessionsStore
        .getState()
        .upsert_companion_provisional("run-1", "invocation-1", "epoch-1", "partial");
    });
    expect(renders).toBe(2);
    expect(screen.getByTestId("values").textContent).toBe("partial");
  });
});
