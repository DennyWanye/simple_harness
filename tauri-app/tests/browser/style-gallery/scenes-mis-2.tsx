// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 任务编排车道（mis）：任务页外壳、列表、新建表单、资料、对话卡片与零件场景。
//   missions-view              src/views/MissionsView.tsx（可用：列表+错误条，无选中）+ MissionListAside.tsx（后台出错提示/列表读取出错/
//                              各种状态行/策略漂移）+ liveGraph/MissionProgress.tsx
//   missions-view-unavailable  src/views/MissionsView.tsx（编排服务不可用）
//   mission-list-aside         src/views/MissionListAside.tsx（空列表+编排降级、列表出错且无行、选中行+卡住步骤数）+ liveGraph/MissionProgress.tsx
//   mission-create-form        src/views/MissionCreateForm.tsx（附资料+严格引用+两种上下文+坏的 pytest 行+上限无效+提交受阻；
//                              创建中+发布目录未授权+缺资料提示；无上下文选项）+ PublishCriterionHelper.tsx + MissionSources.tsx（SourceDrafts）
//   mission-sources            src/views/MissionSources.tsx（MissionSources 当前/替代/撤销版本、无通道；SourceDrafts 固定路径/单份/禁用/多份）
//   chat-mission-card          src/views/ChatMissionCard.tsx（待确认+步骤+问题+各类审批+已发布、失败、已完成、读取失败、连接未就绪、未知状态）
//                              + OperationWorkspace.tsx + PlanningQuestions.tsx + ActionApprovalSummary.tsx
//   chat-mission-notices       src/views/ChatMissionNotices.tsx（5 条通知只显示最新 3 条+全部已收到、格式错误提示）
//   plan-change-panel          src/views/PlanChangePanel.tsx（卡住的改计划+被挡通知；已过期（读取失败/数据格式不对）；无内容）
//   operation-workspace        src/views/OperationWorkspace.tsx（等待要求、待确认多目标/单目标、只读无完成标准、已确认内容/效果、无通道）
//   mission-parts              src/views/MissionsViewParts.tsx、ActionApprovalSummary.tsx、PublishCriterionHelper.tsx、
//                              liveGraph/MissionProgress.tsx、PlanningQuestions.tsx、PlanningAuthorization.tsx、MissionDiagnostics.tsx、
//                              MissionAssurance.tsx 的独立形态
import {
  useMissionsStore,
  type MissionRow,
  type OrchestrationStatus,
} from "../../../src/stores/missionsStore";
import { ActionApprovalSummary } from "../../../src/views/ActionApprovalSummary";
import { ChatMissionCard } from "../../../src/views/ChatMissionCard";
import { ChatMissionNotices } from "../../../src/views/ChatMissionNotices";
import { MissionsChannelContext } from "../../../src/views/chatMission";
import { MissionProgress } from "../../../src/views/liveGraph/MissionProgress";
import { MissionAssurance } from "../../../src/views/MissionAssurance";
import {
  MissionCreateForm,
  type MissionCreateFormProps,
} from "../../../src/views/MissionCreateForm";
import { MissionDiagnostics } from "../../../src/views/MissionDiagnostics";
import { MissionListAside } from "../../../src/views/MissionListAside";
import { MissionSources, SourceDrafts } from "../../../src/views/MissionSources";
import { MissionsView } from "../../../src/views/MissionsView";
import {
  ActionOutcome,
  ArtifactPanel,
  BudgetByDuty,
  LayerSummary,
  ModelText,
  ResolveOutcomeBox,
  StepsNoLongerCounting,
  TakeoverBox,
} from "../../../src/views/MissionsViewParts";
import { OperationWorkspace } from "../../../src/views/OperationWorkspace";
import { PlanChangePanel } from "../../../src/views/PlanChangePanel";
import { PlanningAuthorization } from "../../../src/views/PlanningAuthorization";
import { PlanningQuestions } from "../../../src/views/PlanningQuestions";
import { PublishCriterionHelper } from "../../../src/views/PublishCriterionHelper";
import { noop, useOnce } from "./galleryUtil";
import { makeFakeChannel } from "./fakeChannel";
import "./mis-gallery.css";
import {
  MIS_ARTIFACT_BINARY,
  MIS_ARTIFACT_EMPTY,
  MIS_ARTIFACT_TEXT,
  MIS_DETAIL_A,
  MIS_POLICY,
  MIS_ROWS,
  MIS_STATUS,
  MIS_STATUS_DEGRADED,
  MIS_STATUS_PLAIN,
  misChannel,
  misChatChannel,
  misNoticesChannel,
  misStaleConvergenceChannel,
  misWorkspaceApprovedContent,
  misWorkspaceApprovedEffects,
  misWorkspaceConfirm,
  misWorkspaceConfirmSingle,
  misWorkspaceReadonly,
  misWorkspaceWaiting,
} from "./mis-fixtures";

type Json = Record<string, unknown>;
const dispatchNoop = noop;

function asideStore(
  status: OrchestrationStatus | null,
  missions: MissionRow[],
  extra: Partial<MissionCreateFormProps["store"]> = {},
) {
  return {
    selectedId: null,
    detail: null,
    status,
    policy: null,
    missions,
    error: null,
    listError: null,
    selectedEvents: undefined,
    selectedHasMore: false,
    selectedLoading: false,
    select: noop,
    setError: noop,
    ...extra,
  };
}

// ---------- 任务页外壳 ----------

export function MissionsViewScene() {
  const { channel } = misChannel();
  useOnce(() => {
    const store = useMissionsStore.getState();
    store.reset();
    store.setStatus(MIS_STATUS);
    store.setMissions(MIS_ROWS);
    useMissionsStore.setState({
      listError: "列表读取失败：收到的数据格式不对",
      error: "上一次操作失败：连接已变化",
    });
  });
  return (
    <div className="mis-shell">
      <MissionsView channel={channel} />
    </div>
  );
}

export function MissionsViewUnavailableScene() {
  useOnce(() => {
    const store = useMissionsStore.getState();
    store.reset();
    store.setStatus({ available: false, state: "unavailable", reason: "画廊夹具：编排服务未启动" });
  });
  return (
    <div className="mis-shell">
      <MissionsView channel={null} />
    </div>
  );
}

export function MissionListAsideScene() {
  const missions = MIS_ROWS as MissionRow[];
  return (
    <div className="mis-aside-row">
      <MissionListAside
        status={MIS_STATUS_DEGRADED}
        setCreating={dispatchNoop}
        store={asideStore(MIS_STATUS_DEGRADED, [])}
        selectedRef={{ current: null }}
        setArtifact={dispatchNoop}
        selectedId={null}
        open={noop}
        stalled={{}}
        drift={[]}
      />
      <MissionListAside
        status={MIS_STATUS_PLAIN}
        setCreating={dispatchNoop}
        store={asideStore(MIS_STATUS_PLAIN, [], { listError: "列表读取失败" })}
        selectedRef={{ current: null }}
        setArtifact={dispatchNoop}
        selectedId={null}
        open={noop}
        stalled={{}}
        drift={[]}
      />
      <MissionListAside
        status={MIS_STATUS}
        setCreating={dispatchNoop}
        store={asideStore(MIS_STATUS, missions, { policy: MIS_POLICY })}
        selectedRef={{ current: "mission-a" }}
        setArtifact={dispatchNoop}
        selectedId="mission-a"
        open={noop}
        stalled={{ "mission-a": 2, "mission-b": 0 }}
        drift={MIS_POLICY.drift}
      />
    </div>
  );
}

// ---------- 新建表单 ----------

const ctx512 = MIS_STATUS.context_profiles![0];
const ctx256 = MIS_STATUS.context_profiles![1];

function formProps(over: Partial<MissionCreateFormProps>): MissionCreateFormProps {
  return {
    createPending: false,
    goal: "",
    setGoal: dispatchNoop,
    criteria: "",
    setCriteria: dispatchNoop,
    store: asideStore(MIS_STATUS, []),
    badPytestLines: [],
    canAttachSources: false,
    sources: [],
    setSources: dispatchNoop,
    setSourceImporting: dispatchNoop,
    strictQuotes: false,
    setStrictQuotes: dispatchNoop,
    status: MIS_STATUS,
    selectedContextId: "ctx-512k",
    setContextProfile: dispatchNoop,
    selectedContext: ctx512,
    defaultTokenCap: ctx512.mission_max_tokens,
    maxTokens: "",
    setMaxTokens: dispatchNoop,
    maxAttempts: "",
    setMaxAttempts: dispatchNoop,
    validCaps: true,
    submittable: false,
    submit: noop,
    submitBlocker: "",
    missingMaterialHint: "",
    ...over,
  };
}

export function MissionCreateFormScene() {
  return (
    <div className="mis-row">
      <div className="mis-cell">
        <MissionCreateForm
          {...formProps({
            goal: "根据参考资料《新品需求说明》写一份发布说明",
            criteria: "pytest: 至少 6 个测试\n完成后把说明发布出去",
            badPytestLines: ["pytest: 至少 6 个测试"],
            canAttachSources: true,
            sources: [
              { path: "sources/需求.md", content: "# 需求\n- 一\n- 二", kind: "markdown" },
              { path: "sources/", content: "", kind: "text" },
            ],
            strictQuotes: true,
            maxTokens: "-5",
            maxAttempts: "abc",
            validCaps: false,
            submitBlocker: "每份来源资料都要填写路径（如 sources/笔记.md）和正文",
          })}
        />
      </div>
      <div className="mis-cell">
        <MissionCreateForm
          {...formProps({
            createPending: true,
            goal: "根据附件整理清单",
            criteria: "清单至少十条",
            store: asideStore(MIS_STATUS_PLAIN, []),
            status: { ...MIS_STATUS, mission_budget_defaults: null },
            selectedContextId: "ctx-256k",
            selectedContext: ctx256,
            defaultTokenCap: undefined,
            missingMaterialHint:
              "目标里提到了参考资料，但还没有附上任何资料；需要的话请在上面「参考资料」里添加",
          })}
        />
      </div>
      <div className="mis-cell">
        <MissionCreateForm
          {...formProps({
            goal: "写 NOTES.md",
            criteria: "file:NOTES.md",
            store: asideStore({ ...MIS_STATUS_PLAIN, publish: null }, []),
            status: MIS_STATUS_PLAIN,
            selectedContextId: "",
            selectedContext: undefined,
            defaultTokenCap: undefined,
            submittable: true,
            missingMaterialHint:
              "目标里提到了参考资料，但还没有附上任何资料；需要的话请在上面「参考资料」里添加",
          })}
        />
      </div>
    </div>
  );
}

// ---------- 资料 ----------

export function MissionSourcesScene() {
  const { channel } = misChannel();
  const sources = MIS_DETAIL_A.sources as Json[];
  return (
    <div className="mis-row">
      <div className="mis-cell">
        <MissionSources
          missionId="mission-a"
          sources={sources}
          channel={channel}
          onChanged={noop}
        />
        <MissionSources missionId="mission-z" sources={[]} channel={null} onChanged={noop} />
      </div>
      <div className="mis-cell">
        <SourceDrafts
          sources={[{ path: "sources/纪要.md", content: "固定路径的替换正文", kind: "markdown" }]}
          onChange={noop}
          fixedPath
          single
        />
        <SourceDrafts
          sources={[{ path: "sources/", content: "", kind: "text" }]}
          onChange={noop}
          single
          disabled
        />
        <SourceDrafts
          sources={[
            { path: "sources/a.md", content: "第一份", kind: "markdown" },
            { path: "sources/b.txt", content: "第二份", kind: "text" },
          ]}
          onChange={noop}
        />
        <SourceDrafts sources={[]} onChange={noop} disabled />
      </div>
    </div>
  );
}

// ---------- 对话卡片与通知 ----------

export function ChatMissionCardScene() {
  const { channel } = misChatChannel();
  return (
    <div className="mis-col">
      <MissionsChannelContext.Provider value={channel}>
        <ChatMissionCard missionId="chat-1" />
        <ChatMissionCard missionId="chat-2" />
        <ChatMissionCard missionId="chat-3" />
        <ChatMissionCard missionId="chat-4" />
        <ChatMissionCard missionId="chat-5" />
        <ChatMissionCard missionId="chat-silent" />
      </MissionsChannelContext.Provider>
      <ChatMissionCard missionId="chat-offline" />
    </div>
  );
}

export function ChatMissionNoticesScene() {
  const { channel: list } = misNoticesChannel("list");
  const { channel: protocol } = misNoticesChannel("protocol");
  const { channel: plain } = misNoticesChannel("plain-error");
  return (
    <div className="mis-col">
      <MissionsChannelContext.Provider value={list}>
        <ChatMissionNotices />
      </MissionsChannelContext.Provider>
      <MissionsChannelContext.Provider value={protocol}>
        <ChatMissionNotices />
      </MissionsChannelContext.Provider>
      <MissionsChannelContext.Provider value={plain}>
        <ChatMissionNotices />
      </MissionsChannelContext.Provider>
      <ChatMissionNotices />
    </div>
  );
}

// ---------- 改计划进度 ----------

export function PlanChangePanelScene() {
  const { channel } = misChannel();
  const { channel: stale } = misStaleConvergenceChannel("mission-a");
  const { channel: badFormat } = misStaleConvergenceChannel("mission-a", "protocol_error");
  return (
    <div className="mis-col">
      <PlanChangePanel missionId="mission-a" channel={channel} />
      <PlanChangePanel missionId="mission-a" channel={stale} />
      <PlanChangePanel missionId="mission-a" channel={badFormat} />
      <PlanChangePanel missionId="mission-b" channel={channel} />
      <PlanChangePanel missionId="mission-a" channel={null} />
    </div>
  );
}

// ---------- 完成要求与操作 ----------

export function OperationWorkspaceScene() {
  const { channel } = makeFakeChannel();
  const values = [
    misWorkspaceWaiting("ws-1"),
    misWorkspaceConfirm("ws-2"),
    misWorkspaceConfirmSingle("ws-3"),
    misWorkspaceReadonly("ws-4"),
    misWorkspaceApprovedContent("ws-5"),
    misWorkspaceApprovedEffects("ws-6"),
    { ...misWorkspaceApprovedEffects("ws-7"), intents: [] },
  ];
  return (
    <div className="mis-row">
      {values.map((value) => (
        <div className="mis-cell" key={String(value.mission_id)}>
          <OperationWorkspace value={value} channel={channel} onChanged={noop} />
        </div>
      ))}
      <div className="mis-cell">
        <OperationWorkspace value={misWorkspaceConfirm("ws-8")} channel={null} onChanged={noop} />
        <OperationWorkspace value={{}} channel={channel} onChanged={noop} />
      </div>
    </div>
  );
}

// ---------- 零件 ----------

export function MissionPartsScene() {
  const { channel } = makeFakeChannel();
  const send = () => undefined;
  return (
    <div className="mis-row">
      <div className="mis-cell">
        <ActionApprovalSummary
          summary={{ text: "旧格式摘要（系统）", source: "system" }}
          action={{ reason: { text: "系统理由", source: "system" } }}
        />
        <ActionApprovalSummary
          summary={{ text: "旧格式摘要（模型）", source: "model" }}
          action={{ reason: { text: "模型理由" } }}
        />
        <ActionApprovalSummary summary={{ text: "旧格式无理由" }} action={{}} />
        <ActionApprovalSummary
          summary={{
            connector: "file_publish",
            operation: "publish",
            target: "A.md",
            params: { artifact_path: "A.md" },
            previous_attempt: { outcome: "not_delivered", reason: "内部码" },
          }}
          action={{ reason: "动作理由", reason_source: "system" }}
        />
        <ActionApprovalSummary
          summary={{ previous_attempt: { outcome: "human_ruled_not_applied" } }}
          action={{
            connector: "mail",
            operation: "send",
            target: "boss@example.com",
            reason: { text: "模型写的理由", source: "model" },
          }}
        />
        <ActionApprovalSummary summary={undefined} action={{ connector: "x" }} />
        <ActionApprovalSummary
          summary={{ previous_attempt: { outcome: "service_refused" } }}
          action={{}}
        />
        <ActionOutcome
          action={{
            state: "SUCCEEDED",
            published_path: "/srv/a.md",
            published_hash: "sha256:1234567890abcdef",
          }}
        />
        <ActionOutcome action={{ state: "UNKNOWN" }} />
        <ActionOutcome action={{ state: "PROPOSED", published_path: "/srv/b.md" }} />
        <ActionOutcome action={{}} />
        <ResolveOutcomeBox actionKey="act-ok" failed={false} send={send} />
        <ResolveOutcomeBox actionKey="act-failed" failed send={send} />
        <div>
          任务 A
          <TakeoverBox taskId="task-a" send={send} />
        </div>
        <ModelText value={{ text: "模型写的话", source: "model" }} />
        <LayerSummary value={{ text: "模型来源", source: "model" }} />
        <LayerSummary value={{ text: "系统来源", source: "system" }} />
        <LayerSummary value="纯字符串" />
        <StepsNoLongerCounting rows={[{ label: "步骤一", requirements_revision: 1 }]} />
        <StepsNoLongerCounting rows={[]} />
        <BudgetByDuty rows={MIS_DETAIL_A.budget_by_duty as unknown[]} />
        <BudgetByDuty rows={[{ obligation_id: "z", attempts: 0, settled_tokens: 0 }]} />
      </div>
      <div className="mis-cell">
        <ArtifactPanel artifact={MIS_ARTIFACT_TEXT} verified />
        <ArtifactPanel artifact={{ ...MIS_ARTIFACT_TEXT, truncated: false }} />
        <ArtifactPanel artifact={MIS_ARTIFACT_BINARY} />
        <ArtifactPanel artifact={MIS_ARTIFACT_EMPTY} />
        <PublishCriterionHelper
          criteria=""
          onChange={noop}
          disabled={false}
          publish={{ enabled: true }}
        />
        <PublishCriterionHelper
          criteria="完成后上传到网盘"
          onChange={noop}
          disabled={false}
          publish={{ enabled: true }}
        />
        <PublishCriterionHelper
          criteria=""
          onChange={noop}
          disabled
          publish={{ enabled: false, reason: "目录不存在" }}
        />
        <PublishCriterionHelper criteria="" onChange={noop} disabled={false} publish={null} />
      </div>
      <div className="mis-cell">
        <MissionProgress status="CREATED" />
        <MissionProgress status="PLANNING" />
        <MissionProgress status="ACTIVE" counts={{ completed: 1, total: 4 }} />
        <MissionProgress status="ACTIVE" uiState="verifying" counts={{ completed: 4, total: 4 }} />
        <MissionProgress status="COMPLETED" counts={{ completed: 3, total: 3 }} />
        <MissionProgress status="FAILED" counts={{ completed: 0, total: 0 }} />
        <MissionProgress status="CANCELLED" uiState="verifying" />
        <PlanningQuestions
          questions={[
            {
              decision_id: "pq-1",
              state: "PENDING",
              question: "选一个",
              options: [{ key: "a", label: "甲" }],
            },
            { decision_id: "pq-2", state: "PENDING", question: "自由回答", options: [] },
            { decision_id: "pq-3", state: "ANSWERED", question: "已答", answer: "乙" },
            { decision_id: "pq-4", state: "SUPERSEDED", question: "失效" },
          ]}
          channel={channel}
          onAnswered={noop}
        />
        <PlanningQuestions
          questions={[{ decision_id: "pq-5", state: "PENDING", question: "无通道" }]}
          channel={null}
          onAnswered={noop}
        />
        <PlanningAuthorization
          requests={[{ request_id: "pa-1", mission_id: "m" }]}
          channel={channel}
          onChanged={noop}
        />
        <PlanningAuthorization
          requests={[{ request_id: "pa-2", mission_id: "m" }]}
          channel={null}
          onChanged={noop}
        />
        <MissionDiagnostics missionId="mission-a" channel={channel} />
        <MissionDiagnostics missionId="mission-a" channel={null} />
        <MissionAssurance missionId="mission-a" channel={channel} />
      </div>
    </div>
  );
}
