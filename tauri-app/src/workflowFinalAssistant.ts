export type WorkflowFinalAssistant = {
  eventId: string;
  text: string;
};

export function workflowFinalAssistant(
  message: unknown,
): WorkflowFinalAssistant | null {
  if (!message || typeof message !== "object") return null;
  const envelope = message as Record<string, unknown>;
  if (envelope.type !== "workflow_event") return null;
  const event = envelope.payload;
  if (!event || typeof event !== "object") return null;
  const eventRecord = event as Record<string, unknown>;
  if (eventRecord.event_type !== "workflow.final_assistant") return null;
  const outer = eventRecord.payload;
  if (!outer || typeof outer !== "object") return null;
  const outerRecord = outer as Record<string, unknown>;
  const nested = outerRecord.payload;
  const nestedRecord = nested && typeof nested === "object"
    ? nested as Record<string, unknown>
    : {};
  const text = String(nestedRecord.text || outerRecord.text || "").trim();
  if (!text) return null;
  return {
    eventId: String(eventRecord.event_id || ""),
    text,
  };
}

export function isWorkflowLifecycleOnlyMessage(row: unknown): boolean {
  if (!row || typeof row !== "object") return false;
  const event = (row as Record<string, unknown>).workflow_event;
  if (!event || typeof event !== "object") return false;
  const eventType = String((event as Record<string, unknown>).event_type || "");
  return new Set([
    "workflow.accepted",
    "workflow.progress",
    "workflow.decision",
    "workflow.final",
  ]).has(eventType);
}
