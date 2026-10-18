// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 画廊场景表：场景名 → 组件。每个场景只用固定夹具数据，渲染结果必须确定（不依赖时间、随机数、网络）。
//
// 约定：场景模块文件名为 scenes-<组>.tsx，只导出组件；名字以 Scene 结尾的导出就是一个场景，
// 场景名由函数名推出（SettingsSystemScene → settings-system）。这样各组场景互不改同一个文件。
//
// 场景 → 挂载的组件文件（src/ 下）：每个场景模块顶部注释有逐场景清单。按模块汇总：
//   scenes-basic.tsx   runtime-banner → components/RuntimeBackendBanner.tsx
//   scenes-set.tsx     设置页各分区：views/SettingsView、components/SettingsPanel、SettingsSystemSections、
//                      SettingsOrchestrationSections、SettingsProviders、ModelContextCard
//   scenes-set-2.tsx   components/AddProviderModal、code-panel/ChangeModelModal、components/OnboardingWizard、FeedbackPanel、
//                      StartupOverlay、ProjectDirectoryCard、ContextRing、
//                      ProtectedPathCard
//   scenes-cap.tsx     components/SkillStorePanel、CapabilityCenterPanel、CapabilityOperationCard、views/SkillsView
//   scenes-cap-2.tsx   components/ContextBreakdownModal、ContextTracePanel、PermissionPopup、ApprovalCenterPanel、
//                      ClarificationDialog、ExternalWaitDialog
//   scenes-mis.tsx     views/MissionDetailPanel（+ MissionsViewParts、PlanChangePanel、OperationWorkspace、PlanningQuestions、
//                      PlanningAuthorization、ActionApprovalSummary、MissionSources、MissionAssurance、MissionDiagnostics）
//   scenes-mis-2.tsx   views/MissionsView、MissionListAside、MissionCreateForm、PublishCriterionHelper、MissionSources、
//                      ChatMissionCard、ChatMissionNotices、PlanChangePanel、OperationWorkspace、MissionsViewParts、
//                      liveGraph/MissionProgress 等
//   scenes-chat.tsx    code-panel/CodeBlock、InputBar、SlashDropdown、ArgHintBar、SlashTestHarness、ConfirmDialog、
//                      components/MarkdownMessage、useConfirm
//   scenes-chat-3.tsx  components/Sidebar、WorkbenchShell、views/PrimaryChatView、ArtifactsView、
//                      primary/PrimaryWorkspaceBindings、App.tsx
//   scenes-wf.tsx      components/workflows/RunInspectorPanel、DecisionPanel、EvaluationPanel、ReplayDialog、WorkflowGraph、
//                      CheckpointTimeline、TraceTree、RunList
import "./tauriStub";
import type { ComponentType } from "react";
import "./gallery.css";

const modules = import.meta.glob<Record<string, unknown>>("./scenes-*.tsx", { eager: true });

function sceneName(exportName: string) {
  return exportName
    .replace(/Scene$/, "")
    .replace(/([a-z0-9])([A-Z])/g, "$1-$2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1-$2")
    .toLowerCase();
}

const table: Record<string, ComponentType> = {};
for (const path of Object.keys(modules).sort()) {
  for (const [key, value] of Object.entries(modules[path])) {
    if (!key.endsWith("Scene") || typeof value !== "function") continue;
    const name = sceneName(key);
    if (table[name]) throw new Error(`画廊场景重名：${name}（${path}）`);
    table[name] = value as ComponentType;
  }
}

export const SCENES: Record<string, ComponentType> = table;
