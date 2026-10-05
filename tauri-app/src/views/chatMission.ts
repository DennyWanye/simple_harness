/** 对话任务卡片共用的连接上下文与工具结果解析（2026-09-29）。 */
import { createContext } from "react";

import { asRecord as record, asText as text, type MissionsChannel } from "../stores/missionsStore";

/** App 把任务页用的那条连接放进来；对话卡片与任务页同路。 */
export const MissionsChannelContext = createContext<MissionsChannel | null>(null);

/** 结果会在对话里显示成任务卡片的工具：发起后台任务、改后台任务的要求、换后台任务的资料。 */
export const MISSION_CARD_TOOLS = new Set(["mission_start", "mission_amend", "mission_source_update"]);

/** 这些工具的结果（JSON 文本，可能包一层）里的 mission_id。 */
export function missionIdFromToolResult(raw: string | undefined): string {
  if (!raw) return "";
  try {
    const value = JSON.parse(raw) as unknown;
    for (const candidate of [value, record(value).value, record(value).result, record(record(value).value).value]) {
      const id = text(record(candidate).mission_id);
      if (id) return id;
    }
  } catch {
    /* 不是 JSON：没有卡片 */
  }
  return "";
}
