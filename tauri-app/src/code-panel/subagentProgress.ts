import type { SubagentRunView } from "./subagentStore";

export function isInternalDeepResearchAttempt(run: SubagentRunView): boolean {
  return run.kind === "research" && /\.dr-\d+\.a\d+$/.test(run.run_id);
}
