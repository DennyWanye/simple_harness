// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 任务编排车道（mis）：任务详情面板场景。
//   mission-detail-active   src/views/MissionDetailPanel.tsx（进行中：用量超限/预算去向/等待原因含裁定框/改计划进度/取消/
//                           提示条内授权/接管卡/各类审批卡/Task 与验证各判定/产物含系统记录展开/产物内容（文本已核验）/评论/
//                           事件超过 50 条+加载更多）
//                           + MissionsViewParts.tsx（BudgetByDuty/StepsNoLongerCounting/ModelText/LayerSummary/ArtifactPanel/
//                           ActionOutcome/ResolveOutcomeBox/TakeoverBox）+ PlanChangePanel.tsx + OperationWorkspace.tsx（已确认含效果）
//                           + PlanningQuestions.tsx + PlanningAuthorization.tsx + ActionApprovalSummary.tsx + MissionSources.tsx
//                           + MissionAssurance.tsx + MissionDiagnostics.tsx（未读取态）+ missionStory/MissionStory.tsx（读取失败态）
//   mission-detail-confirm  src/views/MissionDetailPanel.tsx（待确认完成要求：提示条带跳转按钮、授权放在外面、只有输入上下文、
//                           系统记录收起、产物内容为二进制、事件全部显示）+ OperationWorkspace.tsx（待确认）+ PlanningQuestions.tsx
//   mission-detail-ended    src/views/MissionDetailPanel.tsx 的结束/特殊形态（已完成、人工接管停止、失败、已取消、被隔离、
//                           只等审批、自动进行中），并附产物内容的未核验/无内容形态
import { MissionDetailPanel } from "../../../src/views/MissionDetailPanel";
import type { MissionEvent, MissionsChannel, OrchestrationStatus } from "../../../src/stores/missionsStore";
import { noop } from "./galleryUtil";
import "./mis-gallery.css";
import {
  MIS_ARTIFACT_BINARY,
  MIS_ARTIFACT_EMPTY,
  MIS_ARTIFACT_TEXT,
  MIS_ARTIFACT_UNVERIFIED,
  MIS_DETAIL_A,
  MIS_DETAIL_APPROVALS_ONLY,
  MIS_DETAIL_B,
  MIS_DETAIL_CANCELLED,
  MIS_DETAIL_DONE,
  MIS_DETAIL_FAILED,
  MIS_DETAIL_FAILED_PLAIN,
  MIS_DETAIL_ISOLATED,
  MIS_DETAIL_RUNNING,
  MIS_STATUS,
  MIS_STATUS_PLAIN,
  misChannel,
  misEvents,
} from "./mis-fixtures";

type Json = Record<string, unknown>;

const asList = (value: unknown): Json[] => (Array.isArray(value) ? (value as Json[]) : []);
const dispatchNoop = noop;

/** 与 MissionsView 同样的派生：待审批、等待原因、交付物/系统记录。 */
function Panel({
  detail,
  channel,
  status,
  showInternal = false,
  artifact = null,
  comment = "",
  events = [],
  allEventsShown = false,
  showMore = false,
  reasons = {},
  bases = {},
  reviewPending = {},
}: {
  detail: Json;
  channel: MissionsChannel | null;
  status: OrchestrationStatus | null;
  showInternal?: boolean;
  artifact?: Json | null;
  comment?: string;
  events?: MissionEvent[];
  allEventsShown?: boolean;
  showMore?: boolean;
  reasons?: Record<string, string>;
  bases?: Record<string, string>;
  reviewPending?: Record<string, boolean>;
}) {
  const mission = (detail.mission ?? {}) as Json;
  const selectedId = String(mission.id);
  const artifacts = asList(detail.artifacts);
  const internals = artifacts.filter((a) => String(a.path).startsWith(".assurance/"));
  const deliverables = artifacts.filter((a) => !String(a.path).startsWith(".assurance/"));
  return (
    <MissionDetailPanel
      mission={mission}
      detail={detail}
      waiting={Array.isArray(detail.waiting_on) ? (detail.waiting_on as unknown[]) : []}
      send={() => "gallery-request"}
      selectedId={selectedId}
      channel={channel}
      ask={async () => false}
      pendingApprovals={asList(detail.approvals).filter((a) => a.state === "PENDING")}
      refreshPlanningQuestions={noop}
      bases={bases}
      setBases={dispatchNoop}
      reasons={reasons}
      setReasons={dispatchNoop}
      reviewPending={reviewPending}
      submitReviewDecision={noop}
      graphTab="story"
      setGraphTab={dispatchNoop}
      onStalled={noop}
      fetchEvents={noop}
      refreshSelected={noop}
      status={status}
      artifacts={artifacts}
      internals={internals}
      setShowInternal={dispatchNoop}
      showInternal={showInternal}
      deliverables={deliverables}
      setArtifact={dispatchNoop}
      artifactRequest={{ current: null }}
      artifact={artifact}
      comment={comment}
      setComment={dispatchNoop}
      events={events}
      allEventsShown={allEventsShown}
      setAllEventsShown={dispatchNoop}
      recentEvents={allEventsShown ? events : events.slice(-50)}
      showMore={showMore}
    />
  );
}

export function MissionDetailActiveScene() {
  const { channel } = misChannel();
  return (
    <div className="mis-col">
      <Panel
        detail={MIS_DETAIL_A}
        channel={channel}
        status={MIS_STATUS}
        showInternal
        artifact={MIS_ARTIFACT_TEXT}
        comment="看过了，结论没问题"
        events={misEvents(60)}
        showMore
        reasons={{ "ap-review-2": "结论缺少依据", "ap-arb": "按实际测试结果", "ap-source": "旧版更准确" }}
        bases={{ "task-blocked": "已人工确认进程退出" }}
        reviewPending={{ "ap-review": true }}
      />
    </div>
  );
}

export function MissionDetailConfirmScene() {
  const { channel } = misChannel();
  return (
    <div className="mis-col">
      <Panel
        detail={MIS_DETAIL_B}
        channel={channel}
        status={MIS_STATUS_PLAIN}
        artifact={MIS_ARTIFACT_BINARY}
        events={misEvents(55)}
        allEventsShown
      />
    </div>
  );
}

export function MissionDetailEndedScene() {
  const { channel } = misChannel();
  return (
    <div className="mis-col">
      <Panel detail={MIS_DETAIL_DONE} channel={channel} status={null} artifact={MIS_ARTIFACT_UNVERIFIED} events={misEvents(3)} />
      <Panel detail={MIS_DETAIL_FAILED} channel={channel} status={null} artifact={MIS_ARTIFACT_EMPTY} />
      <Panel detail={MIS_DETAIL_FAILED_PLAIN} channel={channel} status={null} />
      <Panel detail={MIS_DETAIL_CANCELLED} channel={channel} status={null} />
      <Panel detail={MIS_DETAIL_ISOLATED} channel={channel} status={MIS_STATUS_PLAIN} />
      <Panel detail={MIS_DETAIL_APPROVALS_ONLY} channel={channel} status={null} />
      <Panel detail={MIS_DETAIL_RUNNING} channel={null} status={null} />
    </div>
  );
}
