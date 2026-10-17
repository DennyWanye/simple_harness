// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { InboxItem } from "../stores/sessionSelectors";
import type {
  Message,
  TaskRunProjectionState,
  WorkflowV5ControlAction,
} from "../stores/sessionTypes";
import { type WorkflowTaskTrace } from "./AgentActivityMessage";
import type { CompanionEvent } from "../types/messages";
import type { ProjectDirectoryRequest } from "../types/skillPlatform";

export type StreamFilter = "all" | "chat" | "warn" | "err";

export type ChatStreamMessage =
  | {
      // 2026-06-12: 鍔?"tool" 鈥?宸ュ叿鎵ц杞ㄨ抗(璋冪敤/缁撴灉)杩涗富娑堟伅娴?
      // 鐢ㄦ埛鍏ㄧ▼鍙娴?姝ゅ墠娲剧敓灞傛妸 tool_call/tool_result 婊ゆ帀浜?銆?
      role: "user" | "assistant" | "tool";
      text: string;
      ts: number;
      runId?: string;
      toolName?: string;
      toolArgs?: Record<string, unknown>;
      toolOk?: boolean;
      toolResultRaw?: string;
      continuationStatus?: Message["continuation_status"];
      continuationError?: string;
    }
  | {
      role: "progress";
      text: string;
      ts: number;
      runId?: string;
      phase?: Message["reasoning_phase"];
      status?: Message["reasoning_status"];
    }
  | {
      role: "ppt_outline";
      message: Message;
      session_id: string;
      ts: number;
    }
  | {
      role: "workflow_progress";
      message: Message;
      ts: number;
    }
  | {
      role: "workflow_stage";
      message: Message;
      ts: number;
    };

export type ThinkingStreamMessage =
  | Extract<ChatStreamMessage, { role: "progress" }>
  | (Extract<ChatStreamMessage, { role: "user" | "assistant" | "tool" }> & {
      role: "tool";
    });

export interface MessageStreamPanelProps {
  filter: StreamFilter;
  /** Companion-mode chat messages, oldest 鈫?newest. Internally we sort
   * by ts when merging with alerts. */
  chatMessages: ChatStreamMessage[];
  warnings: InboxItem[];
  errors: InboxItem[];
  companionEvents?: CompanionEvent[];
  onSetFilter: (f: StreamFilter) => void;
  onDismiss: (sid: string, alert_id: string) => void;
  onDismissAll: (severity: "yellow" | "red") => void;
  onJumpToSession: (sid: string) => void;
  onChoice: (sid: string, alert_id: string, button_index: number, button_text: string) => void;
  onCompanionDetail?: (event: CompanionEvent) => void;
  onCompanionAction?: (
    event: CompanionEvent,
    action: string,
    allow?: boolean,
  ) => void | Promise<void>;
  /** 2026-05-16: pet window = floating absolute overlay (default,
   * unchanged). Code-mode window embeds this in a 3-column flex layout
   * 鈫?embedded=true switches the wrapper from absolute to a relative
   * flex-fill panel (no top/left/width hardcode). Visual styling
   * identical; only positioning differs. Default false keeps the pet
   * window byte-identical (zero regression). */
  embedded?: boolean;
  onWorkflowRetry?: (
    runId: string,
    actionId: Exclude<WorkflowV5ControlAction, "none">,
    retryKey: string,
  ) => Promise<{ run_id: string; accepted?: boolean }>;
  sessionId?: string;
  selectedRunId?: string | null;
  runProjections?: Record<string, TaskRunProjectionState>;
  /** @deprecated Test-only compatibility seam. Production uses the shared store. */
  agentSnapshot?: unknown;
  projectDirectoryRequest?: ProjectDirectoryRequest["payload"] | null;
  projectDirectoryError?: string | null;
  onProjectDirectoryConfirm?: (parentDirectory: string, folderName: string) => boolean;
}

export type StreamRow =
  | {
      kind: "chat";
      ts: number;
      msg: ChatStreamMessage;
      key: string;
    }
  | {
      kind: "alert";
      ts: number;
      severity: "yellow" | "red";
      item: InboxItem;
      key: string;
    }
  | {
      kind: "thinking_group";
      ts: number;
      runId: string;
      messages: ThinkingStreamMessage[];
      runProjection?: TaskRunProjectionState;
      key: string;
    }
  | {
      kind: "workflow_group";
      ts: number;
      runId: string;
      summary?: Message;
      stages: Message[];
      taskTrace?: WorkflowTaskTrace;
      runProjectionStatus?: TaskRunProjectionState["status"];
      runProjection?: TaskRunProjectionState;
      key: string;
    }
  | {
      kind: "companion";
      ts: number;
      event: CompanionEvent;
      key: string;
    }
  | {
      kind: "project_directory";
      ts: number;
      request: ProjectDirectoryRequest["payload"];
      key: string;
    };
