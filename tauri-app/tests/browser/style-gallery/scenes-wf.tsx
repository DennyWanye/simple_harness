// 工作流检查面板车道（wf）场景。
//   wf-inspector-runs     components/workflows/RunInspectorPanel.tsx（运行视图：报错/恢复/重试时间/节点尝试/待恢复交付）
//                         + DecisionPanel.tsx + WorkflowGraph.tsx + RunList.tsx + components/Icon.tsx
//   wf-inspector-states   RunInspectorPanel.tsx 的加载中/未选择/详情加载/trace/checkpoints/evaluations 视图
//                         + TraceTree.tsx + CheckpointTimeline.tsx + EvaluationPanel.tsx + RunList.tsx（空）
//   wf-decisions          components/workflows/DecisionPanel.tsx 各形态（提示内选项/显式选项/过期/审批/自由文本/提交中/报错/反馈）
//   wf-evaluation         components/workflows/EvaluationPanel.tsx（多版本比较正负差值/空/提交中/报错/反馈）
//   wf-replay             components/workflows/ReplayDialog.tsx（可分支+需确认副作用+报错、不可分支、提交中）
//   wf-parts              WorkflowGraph.tsx / CheckpointTimeline.tsx / TraceTree.tsx / RunList.tsx 的空状态与数据态
import { CheckpointTimeline } from "../../../src/components/workflows/CheckpointTimeline";
import { DecisionPanel } from "../../../src/components/workflows/DecisionPanel";
import { EvaluationPanel } from "../../../src/components/workflows/EvaluationPanel";
import { ReplayDialog } from "../../../src/components/workflows/ReplayDialog";
import { RunInspectorPanel } from "../../../src/components/workflows/RunInspectorPanel";
import { RunList } from "../../../src/components/workflows/RunList";
import { TraceTree } from "../../../src/components/workflows/TraceTree";
import { WorkflowGraph } from "../../../src/components/workflows/WorkflowGraph";
import { noop } from "./galleryUtil";
import "./wf-gallery.css";
import {
  WF_CHECKPOINTS,
  WF_DECISIONS,
  WF_DELIVERIES,
  WF_EDGES,
  WF_EVALUATIONS,
  WF_EVALUATIONS_NEGATIVE,
  WF_NODES,
  WF_ORPHAN_SPANS,
  WF_RUNS,
  WF_SPANS,
} from "./wf-fixtures";

const empty = { nodes: [], edges: [], spans: [], checkpoints: [], evaluations: [] };

export function WfInspectorRunsScene() {
  return (
    <div className="wf-full">
      <RunInspectorPanel
        view="runs"
        runs={WF_RUNS}
        selectedRunId={WF_RUNS[0].run_id}
        nodes={WF_NODES}
        edges={WF_EDGES}
        spans={[]}
        checkpoints={[]}
        evaluations={[]}
        decisions={[WF_DECISIONS.explicitOptions]}
        deliveries={WF_DELIVERIES}
        error="加载运行记录失败（夹具）"
        actionError="上一次操作失败（夹具）"
        actionFeedback="已提交（夹具）"
        pendingActionId="d-1"
        onSelectRun={noop}
        onRefresh={noop}
        onDeliveryAction={noop}
      />
    </div>
  );
}

export function WfInspectorStatesScene() {
  return (
    <div className="wf-grid">
      <div className="wf-tall">
        <RunInspectorPanel runs={[]} loading {...empty} onSelectRun={noop} onRefresh={noop} />
      </div>
      <div className="wf-tall">
        <RunInspectorPanel runs={[]} {...empty} onSelectRun={noop} onRefresh={noop} />
      </div>
      <div className="wf-tall">
        <RunInspectorPanel runs={WF_RUNS.slice(1, 3)} selectedRunId={WF_RUNS[1].run_id} detailLoading {...empty} onSelectRun={noop} onRefresh={noop} />
      </div>
      <div className="wf-tall">
        <RunInspectorPanel view="trace" runs={WF_RUNS.slice(4, 6)} selectedRunId={WF_RUNS[4].run_id} {...empty} spans={WF_SPANS} onSelectRun={noop} onRefresh={noop} />
      </div>
      <div className="wf-tall">
        <RunInspectorPanel
          view="checkpoints"
          runs={WF_RUNS.slice(3, 4)}
          selectedRunId={WF_RUNS[3].run_id}
          {...empty}
          checkpoints={WF_CHECKPOINTS}
          onSelectRun={noop}
          onRefresh={noop}
          onFork={noop}
        />
      </div>
      <div className="wf-tall">
        <RunInspectorPanel
          view="evaluations"
          runs={WF_RUNS.slice(2, 3)}
          selectedRunId={WF_RUNS[2].run_id}
          {...empty}
          evaluations={WF_EVALUATIONS}
          pendingActionId="evaluation"
          actionError="评分提交失败（夹具）"
          onSelectRun={noop}
          onRefresh={noop}
        />
      </div>
      <div className="wf-tall">
        <RunInspectorPanel
          runs={WF_RUNS.slice(6, 9)}
          selectedRunId={WF_RUNS[7].run_id}
          {...empty}
          decisions={[WF_DECISIONS.expired]}
          pendingActionId="dec-expired"
          onSelectRun={noop}
          onRefresh={noop}
        />
      </div>
      <div className="wf-tall">
        <RunInspectorPanel runs={WF_RUNS.slice(1, 2)} selectedRunId="not-in-list" loading {...empty} onSelectRun={noop} onRefresh={noop} />
      </div>
    </div>
  );
}

export function WfDecisionsScene() {
  const d = WF_DECISIONS;
  return (
    <div className="wf-grid">
      <div className="wf-cell">
        <DecisionPanel decision={d.promptOptions} onResolve={noop} onReopen={noop} onCancel={noop} />
      </div>
      <div className="wf-cell">
        <DecisionPanel decision={d.explicitOptions} submitting error="提交失败（夹具）" feedback="已记录（夹具）" onResolve={noop} onReopen={noop} onCancel={noop} />
      </div>
      <div className="wf-cell">
        <DecisionPanel decision={d.expired} onResolve={noop} onReopen={noop} onCancel={noop} />
      </div>
      <div className="wf-cell">
        <DecisionPanel decision={d.expired} submitting onResolve={noop} onReopen={noop} onCancel={noop} />
      </div>
      <div className="wf-cell">
        <DecisionPanel decision={d.approval} onResolve={noop} onReopen={noop} onCancel={noop} />
      </div>
      <div className="wf-cell">
        <DecisionPanel decision={d.approval} submitting onResolve={noop} onReopen={noop} onCancel={noop} />
      </div>
      <div className="wf-cell">
        <DecisionPanel decision={d.freeText} onResolve={noop} onReopen={noop} onCancel={noop} />
      </div>
      <div className="wf-cell">
        <DecisionPanel decision={d.emptyPrompt} submitting onResolve={noop} onReopen={noop} onCancel={noop} />
      </div>
      <div className="wf-cell">
        <DecisionPanel decision={null} onResolve={noop} onReopen={noop} onCancel={noop} />
      </div>
    </div>
  );
}

export function WfEvaluationScene() {
  return (
    <div className="wf-grid">
      <div className="wf-cell">
        <EvaluationPanel evaluations={WF_EVALUATIONS} feedback="评分已保存（夹具）" onSubmit={noop} />
      </div>
      <div className="wf-cell">
        <EvaluationPanel evaluations={WF_EVALUATIONS_NEGATIVE} submitting error="提交失败（夹具）" onSubmit={noop} />
      </div>
      <div className="wf-cell">
        <EvaluationPanel evaluations={[]} onSubmit={noop} />
      </div>
    </div>
  );
}

export function WfReplayScene() {
  return (
    <div className="wf-grid">
      <div className="wf-tall">
        <ReplayDialog open checkpoint={WF_CHECKPOINTS[2]} error="分支创建失败（夹具）" onClose={noop} onSubmit={noop} />
      </div>
      <div className="wf-tall">
        <ReplayDialog open checkpoint={WF_CHECKPOINTS[1]} onClose={noop} onSubmit={noop} />
      </div>
      <div className="wf-tall">
        <ReplayDialog open checkpoint={WF_CHECKPOINTS[0]} submitting onClose={noop} onSubmit={noop} />
      </div>
      <div className="wf-tall">
        <ReplayDialog open={false} checkpoint={WF_CHECKPOINTS[0]} onClose={noop} onSubmit={noop} />
      </div>
    </div>
  );
}

export function WfPartsScene() {
  return (
    <div className="wf-grid">
      <div className="wf-cell">
        <WorkflowGraph nodes={[]} edges={[]} />
      </div>
      <div className="wf-cell">
        <CheckpointTimeline checkpoints={[]} />
      </div>
      <div className="wf-cell">
        <CheckpointTimeline checkpoints={WF_CHECKPOINTS} />
      </div>
      <div className="wf-cell">
        <TraceTree spans={[]} />
      </div>
      <div className="wf-cell">
        <TraceTree spans={WF_ORPHAN_SPANS} />
      </div>
      <div className="wf-cell">
        <RunList runs={[]} onSelect={noop} />
      </div>
      <div className="wf-cell">
        <RunList runs={WF_RUNS} selectedRunId={WF_RUNS[3].run_id} onSelect={noop} />
      </div>
    </div>
  );
}
