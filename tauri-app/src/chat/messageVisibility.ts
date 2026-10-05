import { extractArtifactsFromResult } from "../code-panel/ArtifactCard";
import type { Message } from "../stores/sessionsStore";
import { MISSION_CARD_TOOLS } from "../views/chatMission";

/** Message page policy: tool execution trace is visible on every open. */
export const DEFAULT_HIDE_TOOL_TRACE = false;

/**
 * The conversation timeline belongs to the whole Session even when individual
 * user, assistant, or tool messages carry their originating Run identity.
 * Only workflow progress projections follow the selected Run; an unscoped
 * workflow projection fails closed because its owning Run is unknown.
 */
export function isMessageVisibleForSelectedRun(
  message: Message,
  selectedRunId: string | null,
): boolean {
  const isWorkflowProjection =
    message.role === "workflow_progress" || message.role === "workflow_stage";
  if (!isWorkflowProjection) return true;
  if (!selectedRunId) return true;
  return Boolean(message.run_id && message.run_id === selectedRunId);
}

/**
 * The "hide tool messages" preference only hides execution trace rows.
 * Deliverable artifacts are user-facing results and must stay visible.
 */
export function shouldHideToolTrace(message: Message, hideTools: boolean): boolean {
  if (!hideTools) return false;
  if (message.role === "tool_call") return true;
  if (message.role !== "tool_result") return false;
  if (message.tool_ok === false || !message.tool_result) return true;
  // 后台任务卡片是给人看的（进度与待确认/批准），不是执行痕迹
  if (message.tool_name && MISSION_CARD_TOOLS.has(message.tool_name)) return false;
  return extractArtifactsFromResult(message.tool_result).length === 0;
}
