// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  CompanionDetailErrorResponse,
  CompanionDetailResponse,
  CompanionEvent,
} from "../../types/messages";
import { CompanionDetailModal } from "./CompanionDetailModal";

const event: CompanionEvent = {
  event_id: "event-1",
  profile_id: "profile-a",
  profile_generation: 1,
  session_id: "default",
  seq: 1,
  notification: {
    notification_id: "notice-1",
    kind: "growth_notice",
    summary: "成长详情",
    detail_ref: "detail-1",
    detail_version: "detail-v7",
    available_actions: ["rollback"],
  },
};

afterEach(cleanup);

describe("CompanionDetailModal", () => {
  it("uses only the owner-free paginated detail protocol", async () => {
    const query = vi.fn()
      .mockResolvedValueOnce({
        type: "companion_detail_response",
        request_id: "request-1",
        payload: {
          notification_id: "notice-1",
          section: "overview",
          detail_version: "detail-v7",
          items: [{ id: "row-1", title: "变更", summary: "已完成" }],
          next_cursor: "opaque-cursor",
        },
      })
      .mockResolvedValueOnce({
        type: "companion_detail_response",
        request_id: "request-2",
        payload: {
          notification_id: "notice-1",
          section: "overview",
          detail_version: "detail-v7",
          items: [{ id: "row-2", title: "回执" }],
          next_cursor: null,
        },
      });
    render(
      <CompanionDetailModal event={event} query={query} onClose={vi.fn()} />,
    );

    await screen.findByText("已完成");
    expect(query).toHaveBeenNthCalledWith(1, {
      notification_id: "notice-1",
      section: "overview",
      cursor: null,
      page_size: 20,
      expected_detail_version: "detail-v7",
    });
    expect(query.mock.calls[0][0]).not.toHaveProperty("profile_id");
    expect(query.mock.calls[0][0]).not.toHaveProperty("profile_generation");

    fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
    await screen.findByText("回执");
    expect(query).toHaveBeenNthCalledWith(2, {
      notification_id: "notice-1",
      section: "overview",
      cursor: "opaque-cursor",
      page_size: 20,
      expected_detail_version: "detail-v7",
    });
  });

  it("fails closed on detail_changed and restarts from page one", async () => {
    const query = vi.fn()
      .mockResolvedValueOnce({
        type: "companion_detail_error",
        request_id: "request-1",
        payload: { code: "detail_changed" },
      })
      .mockResolvedValueOnce({
        type: "companion_detail_response",
        request_id: "request-2",
        payload: {
          notification_id: "notice-1",
          section: "overview",
          detail_version: "detail-v7",
          items: [],
          next_cursor: null,
        },
      });
    render(
      <CompanionDetailModal event={event} query={query} onClose={vi.fn()} />,
    );

    expect((await screen.findByRole("alert")).textContent).toContain("发生变化");
    fireEvent.click(screen.getByRole("button", { name: "从第一页重新加载" }));
    await waitFor(() => expect(query).toHaveBeenCalledTimes(2));
    expect(query.mock.calls[1][0].cursor).toBeNull();
  });

  it("ignores a late page from the previously selected section", async () => {
    type DetailResult = CompanionDetailResponse | CompanionDetailErrorResponse;
    let resolveOverview!: (value: DetailResult) => void;
    let resolveEvidence!: (value: DetailResult) => void;
    const query = vi.fn((request: { section: string }) =>
      new Promise<DetailResult>((resolve) => {
        if (request.section === "overview") resolveOverview = resolve;
        else resolveEvidence = resolve;
      })
    );
    render(
      <CompanionDetailModal event={event} query={query} onClose={vi.fn()} />,
    );
    await waitFor(() => expect(query).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "证据" }));
    await waitFor(() => expect(query).toHaveBeenCalledTimes(2));

    await act(async () => {
      resolveEvidence({
        type: "companion_detail_response",
        request_id: "evidence",
        payload: {
          notification_id: "notice-1",
          section: "evidence",
          detail_version: "detail-v7",
          items: [{ id: "evidence-row", summary: "当前证据" }],
          next_cursor: null,
        },
      });
    });
    expect(await screen.findByText("当前证据")).toBeTruthy();

    await act(async () => {
      resolveOverview({
        type: "companion_detail_response",
        request_id: "overview",
        payload: {
          notification_id: "notice-1",
          section: "overview",
          detail_version: "detail-v7",
          items: [{ id: "old-row", summary: "迟到旧页" }],
          next_cursor: null,
        },
      });
    });
    expect(screen.queryByText("迟到旧页")).toBeNull();
    expect(screen.getByText("当前证据")).toBeTruthy();
  });
});
