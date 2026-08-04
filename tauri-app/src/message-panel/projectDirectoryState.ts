import type { TaskRunProjectionState } from "../stores/sessionsStore";
import type { ProjectDirectoryRequest } from "../types/skillPlatform";

export type ProjectDirectoryRequestsBySession = Record<
  string,
  ProjectDirectoryRequest["payload"] | undefined
>;

export function storeProjectDirectoryRequest(
  current: ProjectDirectoryRequestsBySession,
  request: ProjectDirectoryRequest["payload"],
): ProjectDirectoryRequestsBySession {
  return {
    ...current,
    [request.session_id]: request,
  };
}

export function selectVisibleProjectDirectoryRequest(
  requests: ProjectDirectoryRequestsBySession,
  activeSessionId: string,
  selectedRunId: string | null,
  runProjections: Record<string, TaskRunProjectionState> | undefined,
): ProjectDirectoryRequest["payload"] | null {
  const request = requests[activeSessionId];
  if (!request || request.session_id !== activeSessionId) return null;
  if (!selectedRunId || request.run_id !== selectedRunId) return null;
  if (runProjections?.[request.run_id]?.status !== "waiting") return null;
  return request;
}
