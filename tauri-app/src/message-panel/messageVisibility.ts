import { extractArtifactsFromResult } from "../code-panel/ArtifactCard";
import type { Message } from "../stores/sessionsStore";

/**
 * The "hide tool messages" preference only hides execution trace rows.
 * Deliverable artifacts are user-facing results and must stay visible.
 */
export function shouldHideToolTrace(message: Message, hideTools: boolean): boolean {
  if (!hideTools) return false;
  if (message.role === "tool_call") return true;
  if (message.role !== "tool_result") return false;
  if (message.tool_ok === false || !message.tool_result) return true;
  return extractArtifactsFromResult(message.tool_result).length === 0;
}
