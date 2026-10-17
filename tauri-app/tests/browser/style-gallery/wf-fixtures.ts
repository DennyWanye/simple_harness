// 工作流检查面板（components/workflows/*）的固定夹具。时间戳都是固定秒数；"现在"在 freezeTime.ts 里固定为 2026-10-01T08:00Z。
import type {
  CheckpointView,
  EvaluationView,
  TraceSpanView,
  WorkflowDecisionView,
  WorkflowDeliveryView,
  WorkflowEdgeView,
  WorkflowNodeView,
  WorkflowRunSummary,
} from "../../../src/components/workflows/types";

const T0 = 1790841200; // 早于"现在"约 400 秒
const FUTURE = 1790850000; // 晚于"现在"

export const WF_RUNS: WorkflowRunSummary[] = [
  {
    run_id: "run-000000000001",
    workflow_name: "deep-research 一个很长很长的工作流名称用来测试省略号截断效果",
    workflow_version: "3",
    status: "retryable",
    created_at: T0,
    updated_at: T0 + 60,
    active_nodes: ["search"],
    next_retry_at: FUTURE,
    recovery_action: "等待下一次自动重试",
    error: { message: "搜索服务超时（夹具）" },
  },
  { run_id: "run-000000000002", workflow_name: "ppt-outline", workflow_version: "1", status: "running", created_at: T0, updated_at: T0, active_nodes: ["draft"] },
  { run_id: "run-000000000003", workflow_name: "doc-review", workflow_version: "1", status: "waiting", created_at: T0, updated_at: T0, active_nodes: [] },
  { run_id: "run-000000000004", workflow_name: "publish", workflow_version: "2", status: "completed", created_at: T0, updated_at: T0, active_nodes: [] },
  { run_id: "run-000000000005", workflow_name: "import", workflow_version: "1", status: "failed", created_at: T0, updated_at: T0, active_nodes: [], error: "导入失败（字符串错误）" },
  { run_id: "run-000000000006", workflow_name: "cleanup", workflow_version: "1", status: "cancelled", created_at: T0, updated_at: T0, active_nodes: [] },
  { run_id: "run-000000000007", workflow_name: "sync", workflow_version: "1", status: "created", created_at: T0, updated_at: T0, active_nodes: [] },
  { run_id: "run-000000000008", workflow_name: "blocked-flow", workflow_version: "1", status: "blocked", created_at: T0, updated_at: T0, active_nodes: [] },
  { run_id: "run-000000000009", workflow_name: "cancel-flow", workflow_version: "1", status: "cancel_requested", created_at: T0, updated_at: T0, active_nodes: [] },
];

export const WF_NODES: WorkflowNodeView[] = [
  { id: "plan", label: "规划", status: "succeeded" },
  { id: "search", label: "检索资料", status: "retryable", attempt: 3, error: "上游 503（夹具）", recovery_action: "退避后重试", next_retry_at: FUTURE },
  { id: "draft", label: "起草", status: "running", attempt: 1 },
  { id: "review", label: "审阅", status: "waiting" },
  { id: "publish", label: "发布", status: "failed", error: "发布目录不可写" },
  { id: "notify", label: "通知", status: "blocked", recovery_action: "人工处理" },
  { id: "archive", label: "归档", status: "succeeded_pending" },
  { id: "extra", label: "附加", status: "pending" },
  { id: "done", label: "完成", status: "completed" },
];

export const WF_EDGES: WorkflowEdgeView[] = [
  { source: "plan", target: "search", label: "资料需求" },
  { source: "search", target: "draft" },
  { source: "draft", target: "review", label: "一个很长的边标签用来测试截断" },
];

export const WF_DELIVERIES: WorkflowDeliveryView[] = [
  { delivery_id: "d-1", status: "blocked", channel: "email", attempts: 2, last_error: "收件服务拒绝（夹具）", next_attempt_at: FUTURE, version: 1 },
  { delivery_id: "d-2", status: "retry_wait", channel: null, attempts: 1, next_attempt_at: FUTURE, version: 1 },
  { delivery_id: "d-3", status: "dead_letter", channel: "webhook", attempts: 5, last_error: "已放弃", version: 2 },
  { delivery_id: "d-4", status: "delivered", channel: "webhook", version: 1 },
];

export const WF_SPANS: TraceSpanView[] = [
  { span_id: "root", name: "workflow deep-research", kind: "workflow", status: "ok", duration_ms: 1234.4 },
  { span_id: "s1", parent_span_id: "root", name: "plan", kind: "node", status: "ok", duration_ms: 12, input_summary: "用户问题", output_summary: "三步计划" },
  { span_id: "s2", parent_span_id: "root", name: "search", kind: "tool", status: "error", duration_ms: null, error: "上游 503" },
  { span_id: "s3", parent_span_id: "s2", name: "http.get 一个非常长的跨度名称用来测试单行省略号截断的效果是否正常", kind: "http", status: "ok", duration_ms: 88.6, redacted: true },
  { span_id: "s4", parent_span_id: "s3", name: "dns", kind: "net", status: "ok", duration_ms: 1 },
  { span_id: "s5", parent_span_id: "s4", name: "tcp", kind: "net", status: "ok", duration_ms: 2 },
  { span_id: "s6", parent_span_id: "s5", name: "tls", kind: "net", status: "ok", duration_ms: 3 },
  { span_id: "s7", parent_span_id: "s6", name: "send", kind: "net", status: "ok", duration_ms: 4 },
  { span_id: "s8", parent_span_id: "s7", name: "recv", kind: "net", status: "ok", duration_ms: 5 },
];

/** 没有根（父节点都不在列表里）的跨度，走"孤儿当根"分支。 */
export const WF_ORPHAN_SPANS: TraceSpanView[] = [
  { span_id: "o1", parent_span_id: "missing", name: "orphan", kind: "node", status: "ok", duration_ms: 7 },
];

export const WF_CHECKPOINTS: CheckpointView[] = [
  { checkpoint_id: "checkpoint-aaaaaaaaaaaaaaaa", node_id: "plan", created_at: T0, status: "safe", can_fork: true },
  { checkpoint_id: "checkpoint-bbbbbbbbbbbbbbbb", node_id: null, created_at: T0, status: "unsafe", can_fork: false, fork_reason: "该节点已产生外部副作用" },
  { checkpoint_id: "checkpoint-cccccccccccccccc", node_id: "draft", created_at: T0, status: "safe", requires_effect_confirmation: true, effect_summary: "会重新发送一封邮件" },
];

export const WF_EVALUATIONS: EvaluationView[] = [
  { evaluation_id: "e1", evaluator_name: "citation", evaluator_version: "1", verdict: "pass", score: 0.9, experiment_version: "exp-a", comment: "引用齐全" },
  { evaluation_id: "e2", evaluator_name: "clarity", evaluator_version: "2", verdict: "warn", score: 0.6, experiment_version: "exp-b", explanation: "第二段略啰嗦" },
  { evaluation_id: "e3", evaluator_name: "human", evaluator_version: "1", verdict: "pass", score: null },
  { evaluation_id: "e4", evaluator_name: "format", evaluator_version: "1", verdict: "fail", score: 0.4, experiment_version: "exp-a" },
];

/** 版本 A 平均分低于 B（差值为负，走红色分支）。 */
export const WF_EVALUATIONS_NEGATIVE: EvaluationView[] = [
  { evaluation_id: "n1", evaluator_name: "citation", evaluator_version: "1", verdict: "pass", score: 0.3, experiment_version: "exp-x" },
  { evaluation_id: "n2", evaluator_name: "citation", evaluator_version: "1", verdict: "pass", score: 0.8, experiment_version: "exp-y" },
];

const base = { run_id: "run-000000000001", nonce: "nonce-fixture", version: 1, created_at: T0 };

export const WF_DECISIONS: Record<string, WorkflowDecisionView> = {
  promptOptions: {
    ...base,
    decision_id: "dec-options",
    kind: "choice",
    status: "open",
    prompt: {
      title: "选择检索范围",
      description: "检索可能需要几分钟。",
      options: ["只看内部文档", { label: "全网检索", description: "会访问外部站点", dangerous: true, value: "web" }, { title: "跳过", value: null }, 42, { nope: 1 }],
      budget: { tokens: 1000 },
    },
  },
  explicitOptions: {
    ...base,
    decision_id: "dec-explicit",
    kind: "choice",
    status: "open",
    prompt: "直接给出的选项（字符串提示）",
    options: [
      { label: "继续", value: true },
      { label: "删除全部中间产物（危险）", value: "purge", dangerous: true, description: "不可撤销" },
    ],
  },
  expired: {
    ...base,
    decision_id: "dec-expired",
    kind: "approval",
    status: "open",
    expires_at: 1700000000,
    prompt: { question: "是否允许发布？", reason: "发布会覆盖线上版本" },
  },
  approval: {
    ...base,
    decision_id: "dec-approval",
    kind: "permission_review",
    status: "open",
    expires_at: FUTURE * 1000,
    prompt: { summary: "允许写入发布目录？" },
  },
  freeText: {
    ...base,
    decision_id: "dec-text",
    kind: "clarification",
    status: "open",
    prompt: ["数组提示走 JSON 详情分支"],
  },
  emptyPrompt: {
    ...base,
    decision_id: "dec-empty",
    kind: "clarification-with-a-very-long-kind-name-for-ellipsis",
    status: "open",
    prompt: { message: "", extra: "只有额外字段" },
  },
};
