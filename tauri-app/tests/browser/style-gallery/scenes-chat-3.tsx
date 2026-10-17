// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// chat 车道场景（三）：整页视图与外壳。
// 场景 → 挂载的组件文件（src/ 下）：
//   session-list / session-list-empty → components/SessionList.tsx（假响应走 controlWS 监听表）
//   chat-view          → views/ChatView.tsx（含 MessageStreamPanel / MessageStreamRows / InputBar / WorkflowProgressGroup）
//   chat-view-boot     → views/ChatView.tsx（后端未就绪、身份未就绪、空会话）
//   primary-chat       → views/PrimaryChatView.tsx、primary/PrimaryWorkspaceBindings.tsx、code-panel/InputBar.tsx
//   primary-chat-empty / primary-chat-error / primary-chat-offline → views/PrimaryChatView.tsx 其它状态
//   artifacts-view / -empty / -error / -loading → views/ArtifactsView.tsx
//   sidebar            → components/Sidebar.tsx
//   workbench-shell    → components/WorkbenchShell.tsx、components/Sidebar.tsx、views/PrimaryChatView.tsx
//   workbench-artifacts→ components/WorkbenchShell.tsx（产物库视图）、views/ArtifactsView.tsx
//   app-root           → App.tsx（根容器；后端未启动的失败遮罩态）
import "./chat-gallery.css";
import { useState } from "react";
import { SessionList } from "../../../src/components/SessionList";
import { ChatView } from "../../../src/views/ChatView";
import { PrimaryChatView } from "../../../src/views/PrimaryChatView";
import { ArtifactsView } from "../../../src/views/ArtifactsView";
import { Sidebar } from "../../../src/components/Sidebar";
import { WorkbenchShell } from "../../../src/components/WorkbenchShell";
import App from "../../../src/App";
import type { WorkbenchShellProps } from "../../../src/components/WorkbenchShell";
import { controlWS } from "../../../src/code-panel/controlWs";
import { useSessionsStore } from "../../../src/stores/sessionsStore";
import { useMissionsStore } from "../../../src/stores/missionsStore";
import type { Message } from "../../../src/stores/sessionTypes";
import { setTauriAnswer } from "./tauriStub";
import { makeFakeChannel, okReply, type Wire } from "./fakeChannel";
import { noop, useOnce } from "./galleryUtil";
import {
  ARTIFACT_RESULT,
  HINT_RESULT,
  MARKDOWN_RICH,
  NOW,
  PPT_HISTORY,
  PPT_OUTLINE_MD,
  PROJECTLESS_SESSIONS,
  PROJECTS,
  wfStage,
  wfSummary,
} from "./chat-fixtures";

// ── controlWS 假响应：改写模块单例的 send，回包投给它的全局监听表 ─────────
type Listener = (message: unknown) => void;
function emitPanel(message: unknown) {
  const listeners = (globalThis as unknown as { __deskpet_panel_listeners__?: Set<Listener> })
    .__deskpet_panel_listeners__;
  listeners?.forEach((fn) => fn(message));
}

function installProjectResponder(withData: boolean) {
  controlWS.send = (raw) => {
    const msg = raw as { type: string; request_id?: string; payload?: Record<string, unknown> };
    if (msg.type === "project_catalog_page") {
      queueMicrotask(() =>
        emitPanel({
          type: "project_catalog_page_response",
          request_id: msg.request_id,
          payload: {
            ok: true,
            schema_version: 1,
            catalog_revision: 7,
            items: withData ? PROJECTS : [],
            next_cursor: withData ? "catalog-more" : null,
            pinned: null,
          },
        }),
      );
    } else if (msg.type === "project_sessions_page") {
      const projectless = msg.payload?.scope_kind === "projectless";
      queueMicrotask(() =>
        emitPanel({
          type: "project_sessions_page_response",
          request_id: msg.request_id,
          payload: {
            ok: true,
            schema_version: 1,
            catalog_revision: 7,
            scope: { kind: projectless ? "projectless" : "project", project_id: projectless ? null : msg.payload?.project_id },
            items: withData && projectless ? PROJECTLESS_SESSIONS : [],
            next_cursor: withData && projectless ? "sessions-more" : null,
            pinned: null,
          },
        }),
      );
    }
    return true;
  };
}

function seedOwner() {
  useSessionsStore.setState({ companion_owner: { profile_id: "gallery", profile_generation: 1 } });
}

export function SessionListScene() {
  useOnce(() => {
    seedOwner();
    installProjectResponder(true);
  });
  return (
    <div className="chat-sessionlist">
      <SessionList activeSid="s-active" onSwitchSid={noop} />
    </div>
  );
}

export function SessionListEmptyScene() {
  useOnce(() => {
    useSessionsStore.setState({ companion_owner: null });
    installProjectResponder(false);
  });
  return (
    <div className="chat-sessionlist">
      <SessionList activeSid="" onSwitchSid={noop} />
    </div>
  );
}

// ── ChatView ──────────────────────────────────────────────────────────
const CHAT_SID = "sid-chat";
function chatMessages(): Message[] {
  const m = (id: string, minutesAgo: number, extra: Partial<Message>): Message => ({
    id,
    ts: NOW - minutesAgo * 60_000,
    role: "assistant",
    ...extra,
  });
  return [
    m("c-user", 30, { role: "user", text: "帮我做一份行业调研" }),
    m("c-user-2", 29, { role: "user", text: "补充：重点看国内", continuation_status: "waiting" }),
    m("c-asst", 28, { text: MARKDOWN_RICH }),
    m("c-sum", 27, { role: "reasoning_summary", text: "正在规划调研步骤", run_id: "run-chat", reasoning_phase: "planning", reasoning_status: "running" }),
    m("c-call", 26, { role: "tool_call", tool_name: "search", tool_args: { q: "行业现状" }, run_id: "run-chat" }),
    m("c-res", 25, { role: "tool_result", tool_name: "search", tool_ok: true, tool_result: "3 条结果", run_id: "run-chat" }),
    m("c-call-free", 24, { role: "tool_call", tool_name: "read_file", tool_args: { path: "/a.csv" } }),
    m("c-res-art", 23, { role: "tool_result", tool_name: "ppt_create", tool_ok: true, tool_result: ARTIFACT_RESULT }),
    m("c-res-hint", 22, { role: "tool_result", tool_name: "read_file", tool_ok: false, tool_result: HINT_RESULT }),
    m("c-progress", 21, { role: "reasoning_summary", text: "**结果摘要**：已整理 3 个来源", reasoning_phase: "observation" }),
    m("c-ppt", 20, {
      role: "ppt_outline",
      outline_id: "o-chat",
      topic: "行业调研",
      outline_md: PPT_OUTLINE_MD,
      sources_count: 4,
      history: PPT_HISTORY,
      ppt_outline_awaiting: true,
    }),
    { ...wfSummary({ workflow_run_id: "run-wf", id: "c-wf" }), ts: NOW - 19 * 60_000 },
    { ...wfStage(2, "search", { workflow_run_id: "run-wf" }), ts: NOW - 18 * 60_000 },
    m("c-error", 17, { role: "error", text: "模型调用失败" }),
  ];
}

export function ChatViewScene() {
  useOnce(() => {
    seedOwner();
    installProjectResponder(true);
    const store = useSessionsStore.getState();
    store.upsert(CHAT_SID, {
      messages: chatMessages(),
      preferred_model: "deepseek-flash",
      project_name: "simple_harness",
      supervisor_inbox: [
        {
          alert_id: "al-1",
          severity: "yellow",
          action: "ask_user",
          diagnosis: "工具反复失败",
          user_message: "任务似乎卡住了，要继续吗？",
          suggested_buttons: ["继续", "停止"],
          received_at: NOW - 2 * 60_000,
        },
        {
          alert_id: "al-2",
          severity: "red",
          action: "nudge",
          diagnosis: "上下文接近上限",
          user_message: "",
          suggested_buttons: [],
          received_at: NOW - 60_000,
        },
      ],
    });
    useSessionsStore.setState({
      active_sid: CHAT_SID,
      project_session_pages: {
        projectless: {
          revision: 7,
          sessions: [PROJECTLESS_SESSIONS[0], { ...PROJECTLESS_SESSIONS[0], session_id: CHAT_SID, title: "行业调研会话" }],
          next_cursor: null,
          loading: false,
          error: null,
        },
      },
    });
  });
  return (
    <div className="chat-fullview">
      <ChatView activeSid={CHAT_SID} secret="gallery-secret" />
    </div>
  );
}

export function ChatViewBootScene() {
  useOnce(() => {
    useSessionsStore.setState({ companion_owner: null, active_sid: "" });
  });
  return (
    <div className="chat-fullview">
      <ChatView activeSid="" secret="" />
    </div>
  );
}

// ── PrimaryChatView ───────────────────────────────────────────────────
const PRIMARY_BOUND = { companion_profile_bound: { type: "companion_profile_bound", payload: { profile_id: "gallery", profile_generation: 1 } } };
const PRIMARY_STATE = { primary_ref: "primary-gallery", revision: "rev-1", current_run: null, queued_count: 0, queued_count_truncated: false };
const NOTICE_ID = "a".repeat(64);

function primaryItems() {
  return [
    { message_ref: "pm-user", role: "user", text: "请帮我整理本周进展，\n分点列出。", has_more: false, total_chars: 14 },
    { message_ref: "pm-asst", role: "assistant", text: MARKDOWN_RICH.slice(0, 600), has_more: true, total_chars: 2400 },
    { message_ref: "pm-tool", role: "tool", text: '{"call_id":"c1","name":"read_file"}\n文件内容预览', has_more: false, total_chars: 20 },
    { message_ref: "pm-mission", role: "tool", text: '{"call_id":"c2","name":"mission_start"}\n{"mission_id":"m-gallery"}', has_more: false, total_chars: 30 },
    { message_ref: "pm-tool-anon", role: "tool", text: "没有元数据首行的工具输出", has_more: false, total_chars: 12 },
    { message_ref: "pm-art", role: "artifact", text: "生成了 周报.docx", has_more: false, total_chars: 10 },
    { message_ref: "pm-rem", role: "reminder", text: "后台任务已完成", has_more: false, total_chars: 7, notice_id: NOTICE_ID },
  ];
}

function bindingItem(challenge: string, state: string, canDecide: boolean) {
  return {
    primary_ref: PRIMARY_STATE.primary_ref,
    run_ref: "run-1",
    sdk_run_ref: "sdk-1",
    generation: 1,
    effect_ref: `effect-${challenge}`,
    challenge_ref: challenge,
    challenge_hash: `hash-${challenge}`,
    scope_ref: `scope-${challenge}`,
    proposal_hash: `proposal-${challenge}`,
    root_path: `/Users/demo/${challenge}`,
    state,
    can_decide: canDecide,
    expires_at_millis: NOW + 600_000,
    binding_receipt_ref: null,
  };
}

function primaryChannel(mode: "full" | "empty" | "error") {
  return makeFakeChannel((msg: Wire) => {
    switch (msg.operation) {
      case "primary.state":
        return okReply(msg, PRIMARY_STATE);
      case "primary.messages.page":
        return okReply(msg, {
          primary_ref: PRIMARY_STATE.primary_ref,
          revision: mode === "error" ? "rev-other" : PRIMARY_STATE.revision,
          next_cursor: mode === "full" ? "older-1" : null,
          items: mode === "full" ? primaryItems() : [],
        });
      case "primary.bindings.pending":
        return okReply(msg, {
          primary_ref: PRIMARY_STATE.primary_ref,
          truncated: false,
          next_cursor: null,
          items:
            mode === "full"
              ? [
                  bindingItem("pending-1", "pending", true),
                  bindingItem("recorded-1", "allow_recorded", true),
                  bindingItem("bound-1", "bound", false),
                  bindingItem("odd-1", "mystery", false),
                ]
              : [],
        });
      default:
        return okReply(msg, {});
    }
  }, PRIMARY_BOUND);
}

export function PrimaryChatScene() {
  const channel = useOnceValue(() => primaryChannel("full").channel);
  return (
    <div className="chat-fullview">
      <PrimaryChatView channel={channel} onOpenSettings={noop} />
    </div>
  );
}

export function PrimaryChatEmptyScene() {
  const channel = useOnceValue(() => primaryChannel("empty").channel);
  return (
    <div className="chat-fullview">
      <PrimaryChatView channel={channel} onOpenSettings={noop} />
    </div>
  );
}

export function PrimaryChatErrorScene() {
  const channel = useOnceValue(() => primaryChannel("error").channel);
  return (
    <div className="chat-fullview">
      <PrimaryChatView channel={channel} onOpenSettings={noop} active={false} />
    </div>
  );
}

export function PrimaryChatOfflineScene() {
  return (
    <div className="chat-fullview">
      <PrimaryChatView channel={null} />
    </div>
  );
}

/** 首次渲染前造一次固定值（假通道等），之后保持同一引用。 */
function useOnceValue<T>(make: () => T): T {
  const [value] = useState(make);
  return value;
}

// ── ArtifactsView ─────────────────────────────────────────────────────
const ARTIFACT_ENTRIES = [
  { name: "季度汇报.pptx", path: "/data/artifacts/季度汇报.pptx", size: 2_456_789, modified_at: NOW - 3_600_000 },
  { name: "截图.png", path: "/data/artifacts/截图.png", size: 88_000, modified_at: NOW - 7_200_000 },
  { name: "notes.md", path: "/data/artifacts/notes.md", size: 100, modified_at: 0 },
];

export function ArtifactsViewScene() {
  useOnce(() => setTauriAnswer("list_artifacts", ARTIFACT_ENTRIES));
  return (
    <div className="chat-fullview">
      <ArtifactsView />
    </div>
  );
}

export function ArtifactsViewEmptyScene() {
  useOnce(() => setTauriAnswer("list_artifacts", []));
  return (
    <div className="chat-fullview">
      <ArtifactsView />
    </div>
  );
}

export function ArtifactsViewErrorScene() {
  useOnce(() =>
    setTauriAnswer("list_artifacts", () => {
      throw new Error("产物目录不可读");
    }),
  );
  return (
    <div className="chat-fullview">
      <ArtifactsView />
    </div>
  );
}

export function ArtifactsViewLoadingScene() {
  useOnce(() => setTauriAnswer("list_artifacts", () => new Promise(() => undefined)));
  return (
    <div className="chat-fullview">
      <ArtifactsView />
    </div>
  );
}

// ── Sidebar / WorkbenchShell ──────────────────────────────────────────
function seedMissions() {
  useMissionsStore.getState().setMissions([
    { id: "m1", goal: "待审批任务", status: "running", pending_approvals: 2 },
    { id: "m2", goal: "另一个", status: "running", pending_approvals: 1 },
  ]);
}

export function SidebarScene() {
  useOnce(seedMissions);
  return (
    <div className="chat-grid2">
      <div className="chat-sidebar-box">
        <Sidebar view="missions" onViewChange={noop} connectionState="connected" routeKind="local" moreActions={{ onTrace: noop, onFeedback: noop }} />
      </div>
      <div className="chat-sidebar-box">
        <Sidebar view="settings" onViewChange={noop} />
      </div>
    </div>
  );
}

const EMPTY_PROPS = {} as never;
function shellProps(view: WorkbenchShellProps["view"]): WorkbenchShellProps {
  return {
    view,
    onViewChange: noop,
    connectionState: "connected",
    routeKind: "cloud",
    chatProps: { activeSid: "", secret: "" },
    skillsProps: EMPTY_PROPS,
    settingsProps: EMPTY_PROPS,
    moreActions: { onTrace: noop, onFeedback: noop },
  };
}

export function WorkbenchShellScene() {
  const channel = useOnceValue(() => {
    seedMissions();
    return primaryChannel("full").channel;
  });
  return (
    <div className="chat-fullview">
      <WorkbenchShell {...shellProps("chat")} primaryChannel={channel} banner={<div role="alert">后台意外退出（夹具横幅）</div>} />
    </div>
  );
}

export function WorkbenchArtifactsScene() {
  useOnce(() => setTauriAnswer("list_artifacts", ARTIFACT_ENTRIES));
  return (
    <div className="chat-fullview">
      <WorkbenchShell {...shellProps("artifacts")} primaryChannel={null} />
    </div>
  );
}

// ── App（整机根组件）：start_backend 返回空秘钥 → 启动失败遮罩；不连任何后端 ──
export function AppRootScene() {
  return <App />;
}
