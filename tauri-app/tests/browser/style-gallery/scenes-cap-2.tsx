// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// cap 车道场景（二）：上下文构成、ContextTrace、权限弹窗、审批中心、澄清/外部等待弹窗、伙伴成长。
//   context-breakdown        → src/components/ContextBreakdownModal.tsx（实测快照 + 构成条 + 变化折线图）
//   context-breakdown-states → src/components/ContextBreakdownModal.tsx（仅绑定加载中、旧记录不完整 + 构成不可用、无快照）
//   context-trace            → src/components/ContextTracePanel.tsx（Runs 页签有数据、未连接、读取出错）
//   permission-popup         → src/components/PermissionPopup.tsx（敏感/写入/读取/子任务/未知类别、提交中+报错、无参数）
//   approval-center          → src/components/ApprovalCenterPanel.tsx（多条待审批）
//   clarify-wait-dialogs     → src/components/ClarificationDialog.tsx、src/components/ExternalWaitDialog.tsx
//   companion-cards          → src/components/companion/CompanionCard.tsx（评测授权/激活/操作确认/已失效/墓碑/重要/忙碌）
//   companion-detail         → src/components/companion/CompanionDetailModal.tsx（有条目+下一页、出错、空章节）
// 说明：ContextTrace 的旧 Context 页签与构成弹窗的段落展开都要点击，画廊不模拟点击，渲染不到。
import { useState } from "react";
import { ApprovalCenterPanel } from "../../../src/components/ApprovalCenterPanel";
import { ClarificationDialog } from "../../../src/components/ClarificationDialog";
import { ContextBreakdownModal } from "../../../src/components/ContextBreakdownModal";
import { ContextTracePanel } from "../../../src/components/ContextTracePanel";
import { ExternalWaitDialog } from "../../../src/components/ExternalWaitDialog";
import { PermissionPopup } from "../../../src/components/PermissionPopup";
import { CompanionCard } from "../../../src/components/companion/CompanionCard";
import { CompanionDetailModal } from "../../../src/components/companion/CompanionDetailModal";
import {
  actionConfirmationDecision,
  activationDecision,
  approvalChannel,
  bindingOnlySnapshot,
  breakdownQuery,
  companionEvent,
  companionQuery,
  evaluationDecision,
  fullBreakdown,
  legacySnapshot,
  measuredSnapshot,
  traceChannel,
  unavailableBreakdown,
} from "./cap-fixtures";
import { noop } from "./galleryUtil";
import "./cap-gallery.css";

export function ContextBreakdownScene() {
  const [query] = useState(() => breakdownQuery(fullBreakdown));
  return <ContextBreakdownModal open onClose={noop} snapshot={measuredSnapshot} query={query} />;
}

export function ContextBreakdownStatesScene() {
  const [queries] = useState(() => ({
    silent: breakdownQuery(null),
    unavailable: breakdownQuery(unavailableBreakdown),
  }));
  return (
    <>
      <ContextBreakdownModal
        open
        onClose={noop}
        snapshot={bindingOnlySnapshot}
        query={queries.silent}
      />
      <ContextBreakdownModal
        open
        onClose={noop}
        snapshot={legacySnapshot}
        query={queries.unavailable}
      />
      <ContextBreakdownModal open onClose={noop} snapshot={null} query={queries.silent} />
    </>
  );
}

export function ContextTraceScene() {
  const [getters] = useState(() => {
    const ok = traceChannel("ok");
    const failing = traceChannel("error");
    return { ok: () => ok, failing: () => failing, none: () => null };
  });
  return (
    <>
      <div className="cap-frame">
        <ContextTracePanel open onClose={noop} getChannel={getters.ok} />
      </div>
      <div className="cap-frame-short">
        <ContextTracePanel open onClose={noop} getChannel={getters.none} />
      </div>
      <div className="cap-frame-short">
        <ContextTracePanel open onClose={noop} getChannel={getters.failing} />
      </div>
    </>
  );
}

const shellRequest = {
  request_id: "permission-1",
  run_id: "run-1",
  category: "shell" as const,
  summary: "允许 DeskPet 执行 run_shell",
  params: { tool_name: "run_shell", arguments: { command: "python -m pytest -q", cwd: "F:/projects/demo" } },
  default_action: "prompt" as const,
  dangerous: true,
  session_id: "session-1",
};

export function PermissionPopupScene() {
  return (
    <>
      <PermissionPopup request={{ ...shellRequest, category: "read_file", run_id: undefined, params: {} }} onResolve={noop} />
      <PermissionPopup
        request={{ ...shellRequest, category: "write_file", summary: "写入 src/app.ts" }}
        onResolve={noop}
        allowSession={false}
        resolving
        resolveError="提交失败：后端不可用（夹具）"
      />
      <PermissionPopup
        request={{ ...shellRequest, summary: "允许 DeskPet 执行 workflow_spawn", params: { tool_name: "workflow_spawn", arguments: { objective: "test" } } }}
        onResolve={noop}
      />
      <PermissionPopup
        request={{ ...shellRequest, category: "unknown_kind" as never, summary: "未知类别的操作" }}
        onResolve={noop}
      />
      <PermissionPopup request={shellRequest} onResolve={noop} onStopRun={noop} />
    </>
  );
}

export function ApprovalCenterScene() {
  const [channel] = useState(() => approvalChannel());
  return <ApprovalCenterPanel channel={channel} enabled sessionId="s-1" />;
}

export function ClarifyWaitDialogsScene() {
  return (
    <>
      <ClarificationDialog
        current={{ request_id: "q1", question: "要用哪个目录？\n（第二行说明）", options: ["src", "tests", "一个很长很长的选项，用来验证按钮内文字换行与左对齐的显示效果"] }}
        onResolve={noop}
      />
      <ClarificationDialog current={{ request_id: "q2", question: "要不要覆盖？" }} onResolve={noop} />
      <ExternalWaitDialog
        request={{
          session_id: "session-1",
          run_id: "run-1",
          request_id: "decision-2",
          decision_id: "decision-2",
          nonce: "nonce-2",
          version: 0,
          title: "请在浏览器完成第三方授权",
          required_action: "打开授权页面并点击同意。",
          wait_kind: "third_party",
          wait_ref: "external-wait:2",
        }}
        onComplete={noop}
      />
      <ExternalWaitDialog
        request={{
          session_id: "session-1",
          run_id: "run-1",
          request_id: "decision-1",
          decision_id: "decision-1",
          nonce: "nonce-1",
          version: 0,
          title: "请完成 Windows 系统确认",
          required_action: "在 UAC 窗口中点击允许。",
          wait_kind: "uac",
          wait_ref: "external-wait:1",
          evidence_refs: ["evidence:uac-visible"],
        }}
        onComplete={noop}
      />
    </>
  );
}

export function CompanionCardsScene() {
  const events = [
    companionEvent({ decision: evaluationDecision }),
    companionEvent({ event_id: "e-2", importance: "important", decision: { ...evaluationDecision, status: "confirmed" } }),
    companionEvent({ event_id: "e-3", decision: activationDecision }),
    companionEvent({ event_id: "e-4", decision: { ...activationDecision, candidate_code_digest: "code-digest", status: "rejected" } }),
    companionEvent({ event_id: "e-5", decision: actionConfirmationDecision }),
    companionEvent({ event_id: "e-6" }),
    companionEvent({
      event_id: "e-7",
      tombstone: true,
      notification: { ...companionEvent().notification, notification_id: "notice-7", summary: "该成长记录已被遗忘" },
    }),
    companionEvent({
      event_id: "e-8",
      notification: { ...companionEvent().notification, notification_id: "notice-8", detail_ref: "", available_actions: [] },
    }),
  ];
  return (
    <div className="cap-column">
      {events.map((event, index) => (
        <CompanionCard key={event.event_id} event={event} onOpenDetail={noop} onAction={noop} busy={index === 5} />
      ))}
    </div>
  );
}

export function CompanionDetailScene() {
  const [queries] = useState(() => ({
    items: companionQuery("items"),
    error: companionQuery("error"),
    empty: companionQuery("empty"),
  }));
  const event = companionEvent({
    notification: { ...companionEvent().notification, kind: "growth_notice", summary: "成长详情", detail_version: "detail-v7" },
  });
  return (
    <>
      <CompanionDetailModal event={event} query={queries.empty} onClose={noop} />
      <CompanionDetailModal event={event} query={queries.error} onClose={noop} />
      <CompanionDetailModal event={event} query={queries.items} onClose={noop} />
    </>
  );
}
