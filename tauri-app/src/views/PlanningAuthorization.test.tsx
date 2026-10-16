// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MissionsChannel } from "../stores/missionsStore";
import { PlanningAuthorization } from "./PlanningAuthorization";

afterEach(cleanup);

function channel() {
  const send = vi.fn(() => true);
  return {
    send,
    channel: { send, onMessage: vi.fn(() => () => {}) } as unknown as MissionsChannel,
  };
}

const request = { request_id: "auth-1", mission_id: "mission-1" };

describe("PlanningAuthorization", () => {
  it("点授权后显示处理中，按钮不可点", () => {
    const { send, channel: ch } = channel();
    render(<PlanningAuthorization requests={[request]} channel={ch} onChanged={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "授权本轮规划" }));
    expect(send).toHaveBeenCalledTimes(1);
    const sent = (send.mock.calls[0] as unknown[])[0] as {
      type: string;
      payload: Record<string, unknown>;
    };
    expect(sent.type).toBe("mission_planning_authorization");
    expect(sent.payload).toMatchObject({ mission_id: "mission-1", request_id: "auth-1" });
    expect((screen.getByRole("button", { name: "正在授权…" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("换了通道就解除处理中：按钮恢复可点，再点从新通道发出", () => {
    const first = channel();
    const onChanged = () => {};
    const view = render(
      <PlanningAuthorization requests={[request]} channel={first.channel} onChanged={onChanged} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "授权本轮规划" }));
    expect(screen.getByRole("button", { name: "正在授权…" })).toBeTruthy();

    // 旧通道上的请求不会再有回执。换一个通道对象重渲染。
    const second = channel();
    view.rerender(
      <PlanningAuthorization requests={[request]} channel={second.channel} onChanged={onChanged} />,
    );
    expect(screen.queryByRole("button", { name: "正在授权…" })).toBeNull();
    const authorize = screen.getByRole("button", { name: "授权本轮规划" }) as HTMLButtonElement;
    expect(authorize.disabled).toBe(false);
    fireEvent.click(authorize);
    expect(second.send).toHaveBeenCalledTimes(1);
  });
});
