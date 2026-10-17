// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { shouldHideToolTrace } from "../chat/messageVisibility";
import type { ChatStreamMessage } from "../components/messageStreamTypes";
import { forPet } from "../petText";
import type { Message } from "../stores/sessionTypes";

/**
 * 会话视图的陪伴流派生（strip <think> via forPet；工具执行轨迹 tool_call/tool_result 全程可观测——与原消息面板口径一致）。
 * 9H 从 ChatView 的 useMemo 里原样挪出，参数即原闭包里读的值。
 */
export function buildChatStreamMessages({
  visibleMessages,
  hideTools,
  hideProgress,
  activeSid,
  companionProvisional,
}: {
  visibleMessages: Message[];
  hideTools: boolean;
  hideProgress: boolean;
  activeSid: string;
  companionProvisional: string[];
}): ChatStreamMessage[] {
  const out: ChatStreamMessage[] = [];
  visibleMessages.forEach((m) => {
    const ts = m.ts;
    const runId = m.run_id?.trim() || undefined;
    if (m.role === "user") {
      out.push({
        role: "user",
        text: m.text ?? "",
        ts,
        runId,
        continuationStatus: m.continuation_status,
        continuationError: m.continuation_error,
      });
      return;
    }
    if (shouldHideToolTrace(m, hideTools)) {
      return;
    }
    if (m.role === "reasoning_summary") {
      if (!hideProgress && m.text) {
        out.push({
          role: "progress",
          text: m.text,
          ts,
          runId,
          phase: m.reasoning_phase,
          status: m.reasoning_status,
        });
      }
      return;
    }
    if ((m.role as string) === "tool_call") {
      const args = m.tool_args ? JSON.stringify(m.tool_args).slice(0, 120) : "";
      out.push({
        role: "tool",
        text: `🔧 调用 ${m.tool_name || "(工具)"}${args ? ` ${args}${args.length >= 120 ? "…" : ""}` : ""}`,
        ts,
        runId,
        toolName: m.tool_name,
        toolArgs: m.tool_args ?? {},
      });
      return;
    }
    if ((m.role as string) === "tool_result") {
      out.push({
        role: "tool",
        text: `${m.tool_ok === false ? "❌" : "✅"} ${m.tool_name || "(工具)"} 完成`,
        ts,
        runId,
        toolName: m.tool_name,
        toolOk: m.tool_ok,
        toolResultRaw: m.tool_result,
      });
      return;
    }
    if (m.role === "ppt_outline") {
      out.push({ role: "ppt_outline", message: m, session_id: activeSid, ts });
      return;
    }
    if (m.role === "workflow_progress" || m.role === "workflow_stage") {
      out.push({ role: m.role, message: m, ts });
      return;
    }
    const clean = forPet(m.text);
    if (clean) out.push({ role: "assistant", text: clean, ts, runId });
  });
  // provisional 条目排在全部已落库消息之后即可；用已有最大 ts 派生
  // 纯函数时间基（渲染期不得调用 Date.now，react-hooks/purity）。
  const provisionalBaseTs = (out.length ? out[out.length - 1].ts : 0) + 1;
  companionProvisional.forEach((text, index) => {
    const clean = forPet(text);
    if (clean) {
      out.push({
        role: "assistant",
        text: clean,
        ts: provisionalBaseTs + index,
      });
    }
  });
  return out;
}
