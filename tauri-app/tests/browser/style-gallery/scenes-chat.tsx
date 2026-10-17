// chat 车道场景（一）：消息气泡、产物卡、PPT 大纲、Markdown、输入栏与小组件。
// 场景 → 挂载的组件文件（src/ 下）：
//   message-bubbles   → code-panel/MessageBubble.tsx（含 ArtifactCard / PPTOutlineCard / MarkdownMessage / CodeBlock）
//   artifact-cards    → code-panel/ArtifactCard.tsx
//   ppt-outline-cards → code-panel/PPTOutlineCard.tsx
//   markdown-message  → components/MarkdownMessage.tsx、code-panel/CodeBlock.tsx
//   input-bars        → code-panel/InputBar.tsx
//   slash-widgets     → code-panel/SlashDropdown.tsx、code-panel/ArgHintBar.tsx
//   slash-test-harness→ code-panel/SlashTestHarness.tsx（内含 InputBar）
//   confirm-dialogs   → code-panel/ConfirmDialog.tsx、components/useConfirm.tsx
//   auto-resume-banner→ code-panel/AutoResumeBanner.tsx
//   subagent-active / subagent-done → code-panel/SubagentProgressPanel.tsx
import { useEffect } from "react";
import "./chat-gallery.css";
import { MessageBubble } from "../../../src/code-panel/MessageBubble";
import { ArtifactCard } from "../../../src/code-panel/ArtifactCard";
import { PPTOutlineCard } from "../../../src/code-panel/PPTOutlineCard";
import { MarkdownMessage } from "../../../src/components/MarkdownMessage";
import { CodeBlock, InlineCode } from "../../../src/code-panel/CodeBlock";
import { InputBar } from "../../../src/code-panel/InputBar";
import { SlashDropdown } from "../../../src/code-panel/SlashDropdown";
import { ArgHintBar } from "../../../src/code-panel/ArgHintBar";
import { SlashTestHarness } from "../../../src/code-panel/SlashTestHarness";
import { ConfirmDialog } from "../../../src/code-panel/ConfirmDialog";
import { useConfirm } from "../../../src/components/useConfirm";
import { AutoResumeBanner } from "../../../src/code-panel/AutoResumeBanner";
import { SubagentProgressPanel } from "../../../src/code-panel/SubagentProgressPanel";
import { useSubagentStore } from "../../../src/code-panel/subagentStore";
import { useSessionsStore } from "../../../src/stores/sessionsStore";
import { noop, noopAsync, useOnce } from "./galleryUtil";
import { ARTIFACTS, MARKDOWN_RICH, NOW, PPT_HISTORY, PPT_OUTLINE_MD, bubbleMessages } from "./chat-fixtures";

const BUBBLE_SID = "sid-bubbles";

export function MessageBubblesScene() {
  useOnce(() => {
    useSessionsStore.getState().upsert(BUBBLE_SID, { messages: bubbleMessages(BUBBLE_SID) });
  });
  const messages = bubbleMessages(BUBBLE_SID);
  return (
    <div className="chat-col">
      {messages.map((msg) => (
        <MessageBubble key={msg.id} msg={msg} />
      ))}
    </div>
  );
}

export function ArtifactCardsScene() {
  return (
    <div className="chat-grid2">
      {ARTIFACTS.map((artifact, index) => (
        <div className="chat-cell" key={`${artifact.title}-${index}`}>
          <ArtifactCard artifact={artifact} toolName="ppt_create" />
        </div>
      ))}
    </div>
  );
}

export function PptOutlineCardsScene() {
  return (
    <div className="chat-col">
      <PPTOutlineCard
        outlineId="o-await"
        topic="年度回顾"
        outlineMd={PPT_OUTLINE_MD}
        sourcesCount={3}
        noResearch
        history={PPT_HISTORY}
        awaiting
        sessionId="sid-ppt"
        runId="run-ppt"
        decisionId="dec-ppt"
        nonce="nonce"
        version={1}
      />
      <PPTOutlineCard
        outlineId="o-resolved"
        topic=""
        outlineMd=""
        sourcesCount={0}
        noResearch={false}
        history={[]}
        decisionStatus="已确认生成"
      />
      <PPTOutlineCard
        outlineId="o-default"
        topic="默认已提交"
        outlineMd={PPT_OUTLINE_MD}
        sourcesCount={1}
        noResearch={false}
        history={PPT_HISTORY}
      />
    </div>
  );
}

export function MarkdownMessageScene() {
  return (
    <div className="chat-col">
      <MarkdownMessage>{MARKDOWN_RICH}</MarkdownMessage>
      <MarkdownMessage>{"[file 协议](file:///C:/Users/demo/a.pptx) 与 [盘符路径](C:\\demo\\b.docx) 与 [UNC](\\\\server\\share\\c.md)"}</MarkdownMessage>
      <CodeBlock language="typescript">{"const answer: number = 42;\nconsole.log(answer);\n"}</CodeBlock>
      <CodeBlock>{"纯文本代码块，没有语言标签"}</CodeBlock>
      <div>
        行内：<InlineCode>npm test</InlineCode>
      </div>
    </div>
  );
}

const IB = {
  idle: "ib-idle",
  busy: "ib-busy",
  error: "ib-error",
  odd: "ib-odd",
  running: "ib-running",
};

export function InputBarsScene() {
  useOnce(() => {
    const store = useSessionsStore.getState();
    store.upsert(IB.idle, { project_root: "/Users/demo/proj", project_name: "演示项目" });
    store.upsert(IB.busy, {
      status: "permission",
      inflight: true,
      selected_run_id: "run-wait",
      run_projections: {
        "run-wait": {
          run_id: "run-wait",
          task_scope_id: "scope",
          version: 2,
          conversation_boundary_version: 1,
          status: "waiting",
          inflight: true,
          ui_state: "open",
          started_at: NOW - 60_000,
          last_activity: NOW - 1000,
        },
      },
    });
    store.upsert(IB.running, {
      status: "thinking",
      selected_run_id: "run-go",
      run_projections: {
        "run-go": {
          run_id: "run-go",
          task_scope_id: "scope",
          version: 1,
          status: "running",
          inflight: true,
          ui_state: "open",
          started_at: NOW - 60_000,
          last_activity: NOW - 1000,
        },
      },
    });
    store.upsert(IB.error, { status: "error" });
    store.upsert(IB.odd, { status: "compacting" as never });
  });
  return (
    <div className="chat-col">
      <InputBar sessionId={IB.idle} />
      <InputBar sessionId={IB.idle} disabled placeholder="正在恢复身份…" />
      <InputBar sessionId={IB.busy} />
      <InputBar sessionId={IB.running} />
      <InputBar sessionId={IB.error} />
      <InputBar sessionId={IB.odd} />
      <InputBar
        placeholder="输入消息，Enter 发送…"
        primary={{ submit: noopAsync, status: "执行中 · 排队 1", stop: noop }}
      />
      <InputBar disabled placeholder="输入消息，Enter 发送…" primary={{ submit: noopAsync }} />
    </div>
  );
}

const SLASH_COMMANDS = [
  {
    name: "goal",
    description: "设定当前会话目标",
    args_schema: [
      { name: "text", type: "string", description: "目标描述", required: true },
      { name: "deadline", type: "string", description: "", required: false },
    ],
  },
  { name: "help", description: "" },
  { name: "prefs", description: "查看偏好", args_schema: [] },
];

export function SlashWidgetsScene() {
  return (
    <div className="chat-col">
      <div className="chat-dropdown-host">
        <SlashDropdown candidates={SLASH_COMMANDS} selectedIdx={0} onAccept={noop} />
      </div>
      <SlashDropdown candidates={[]} selectedIdx={0} onAccept={noop} />
      <ArgHintBar
        commandName="goal"
        currentArgIndex={1}
        argSchema={[
          { name: "text", type: "string", description: "目标描述", required: true },
          { name: "deadline", type: "string", description: "截止时间", required: false },
          { name: "owner", type: "string", description: "", required: false },
        ]}
      />
      <ArgHintBar commandName="empty" currentArgIndex={0} argSchema={[]} />
    </div>
  );
}

export function SlashTestHarnessScene() {
  return <SlashTestHarness />;
}

function ConfirmAsker() {
  const [dialog, ask] = useConfirm();
  useEffect(() => {
    void ask({ title: "卸载 Skill？", message: "卸载后需要重新安装。\n第二行说明。", confirm_label: "卸载" });
  }, [ask]);
  return <>{dialog}</>;
}

export function ConfirmDialogsScene() {
  return (
    <div className="chat-col">
      <ConfirmDialog title="删除会话？" message="该会话将从历史列表移除。" confirm_label="删除" onConfirm={noop} onCancel={noop} />
      <ConfirmDialog
        title="迁移数据目录？"
        message={<b>迁移期间请勿关闭程序。</b>}
        confirm_label="开始迁移"
        cancel_label="以后再说"
        variant="primary"
        onConfirm={noop}
        onCancel={noop}
      />
      <ConfirmAsker />
    </div>
  );
}

export function AutoResumeBannerScene() {
  useOnce(() => {
    const store = useSessionsStore.getState();
    store.upsert("ar-sid", { auto_resume_attempts: 1, inflight: true });
    store.upsert("ar-hidden", { auto_resume_attempts: 0, inflight: false });
    useSessionsStore.setState({ active_sid: "ar-sid" });
  });
  return (
    <div className="chat-col">
      <AutoResumeBanner />
      <AutoResumeBanner sessionId="ar-sid" maxAttempts={3} />
      <AutoResumeBanner sessionId="ar-hidden" />
    </div>
  );
}

function seedSubagents(active: boolean) {
  const runs = active
    ? [
        { run_id: "r1", task_id: "调研：行业现状", kind: "research", status: "running", ts: NOW - 42_000 },
        { run_id: "r2", task_id: "", kind: "code", status: "queued", ts: NOW - 30_000 },
        { run_id: "r3", task_id: "汇总数据", kind: "unknown_kind", status: "completed", ts: NOW - 20_000 },
        { run_id: "r4", task_id: "失败任务", kind: "", status: "failed", ts: NOW - 10_000 },
        { run_id: "r5", task_id: "取消任务", kind: "research", status: "failed", reason: "cancelled", ts: NOW - 5_000 },
      ]
    : [
        { run_id: "d1", task_id: "已完成任务一", kind: "research", status: "completed", ts: NOW - 42_000 },
        { run_id: "d2", task_id: "已完成任务二", kind: "code", status: "completed", ts: NOW - 30_000 },
      ];
  const store = useSubagentStore.getState();
  store.clear();
  runs.forEach((run) => store.upsert(run));
  store.setMetrics(active ? { peak_concurrent: 2, total_queued: 5, total_rejected: 1 } : { peak_concurrent: 2, total_queued: 2, total_rejected: 0 });
}

export function SubagentActiveScene() {
  useOnce(() => seedSubagents(true));
  return (
    <div className="chat-col">
      <SubagentProgressPanel />
      <SubagentProgressPanel variant="dark" />
    </div>
  );
}

export function SubagentDoneScene() {
  useOnce(() => seedSubagents(false));
  return (
    <div className="chat-col">
      <SubagentProgressPanel />
      <SubagentProgressPanel variant="dark" />
    </div>
  );
}
