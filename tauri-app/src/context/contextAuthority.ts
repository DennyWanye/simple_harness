import type { ContextUsageSnapshot } from "../types/messages";

export type ContextDisplayState =
  | "measured"
  | "binding_only"
  | "legacy_incomplete"
  | "unavailable";

export function contextDisplayState(
  snapshot: ContextUsageSnapshot | null | undefined,
): ContextDisplayState {
  if (!snapshot) return "unavailable";
  if (snapshot.legacy_incomplete) return "legacy_incomplete";
  if (snapshot.source === "binding_only") return "binding_only";
  if (
    snapshot.availability !== "available" ||
    snapshot.has_measurement !== true ||
    (snapshot.source !== "measured" && snapshot.source !== "compacted") ||
    !Number.isFinite(snapshot.prompt_tokens) ||
    snapshot.prompt_tokens < 0 ||
    !Number.isFinite(snapshot.effective_ceiling) ||
    snapshot.effective_ceiling <= 0
  ) return "unavailable";
  return "measured";
}

/** Stable comparison for monotonic authorities and duplicate WS frames. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value) ?? "undefined";
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => (
    `${JSON.stringify(key)}:${canonicalJson(record[key])}`
  )).join(",")}}`;
}

let requestSequence = 0;

export function newContextRequestId(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return `context-breakdown:${uuid}`;
  requestSequence += 1;
  return `context-breakdown:${Date.now().toString(36)}:${requestSequence.toString(36)}`;
}
