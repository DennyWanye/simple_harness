import { describe, expect, it } from "vitest";

import type { TaskRunProjectionState } from "../stores/sessionsStore";
import type { ProjectDirectoryRequest } from "../types/skillPlatform";
import {
  selectVisibleProjectDirectoryRequest,
  storeProjectDirectoryRequest,
} from "./projectDirectoryState";

function request(
  sessionId: string,
  runId: string,
): ProjectDirectoryRequest["payload"] {
  return {
    session_id: sessionId,
    run_id: runId,
    request_id: `request-${runId}`,
    decision_id: `decision-${runId}`,
    nonce: `nonce-${runId}`,
    version: 0,
    title: "选择项目保存位置",
    required_action: "选择父目录",
    wait_kind: "user_content",
    wait_ref: `wait-${runId}`,
    project_name: "末日生存 Demo",
    folder_name: "apocalypse-demo",
    project_kind: "Godot 游戏",
  };
}

function projection(
  runId: string,
  status: TaskRunProjectionState["status"],
): TaskRunProjectionState {
  return {
    run_id: runId,
    task_scope_id: `scope-${runId}`,
    version: 1,
    status,
    inflight: status === "running",
    ui_state: "open",
    started_at: 1,
    last_activity: 1,
  };
}

describe("project directory request visibility", () => {
  it("shows only the active Session's selected waiting Run", () => {
    const selected = request("session-a", "run-a");
    const requests = storeProjectDirectoryRequest({}, selected);

    expect(
      selectVisibleProjectDirectoryRequest(
        requests,
        "session-a",
        "run-a",
        { "run-a": projection("run-a", "waiting") },
      ),
    ).toEqual(selected);
  });

  it("does not leak the card into a newly selected Session", () => {
    const requests = storeProjectDirectoryRequest(
      {},
      request("session-a", "run-a"),
    );

    expect(
      selectVisibleProjectDirectoryRequest(
        requests,
        "session-b",
        null,
        undefined,
      ),
    ).toBeNull();
  });

  it("does not show the card for another Run in the same Session", () => {
    const requests = storeProjectDirectoryRequest(
      {},
      request("session-a", "run-a"),
    );

    expect(
      selectVisibleProjectDirectoryRequest(
        requests,
        "session-a",
        "run-b",
        {
          "run-a": projection("run-a", "waiting"),
          "run-b": projection("run-b", "running"),
        },
      ),
    ).toBeNull();
  });

  it.each(["starting", "running", "completed", "failed", "cancelled"] as const)(
    "hides the card when the owning Run is %s",
    (status) => {
      const requests = storeProjectDirectoryRequest(
        {},
        request("session-a", "run-a"),
      );

      expect(
        selectVisibleProjectDirectoryRequest(
          requests,
          "session-a",
          "run-a",
          { "run-a": projection("run-a", status) },
        ),
      ).toBeNull();
    },
  );
});
