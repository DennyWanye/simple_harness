export type WorkflowStatus =
  | "created"
  | "running"
  | "waiting"
  | "retryable"
  | "cancel_requested"
  | "cancelling"
  | "blocked"
  | "completed"
  | "failed"
  | "cancelled";

export type WorkflowRunSummary = {
  run_id: string;
  workflow_name: string;
  workflow_version: string;
  status: WorkflowStatus;
  created_at: number;
  updated_at: number;
  active_nodes: string[];
  run_version?: number;
  trace_id?: string | null;
  recovery_action?: string | null;
  next_retry_at?: number | null;
  error?: unknown;
};

export type WorkflowNodeView = {
  id: string;
  label: string;
  status: string;
  attempt?: number;
  error?: string | null;
  recovery_action?: string | null;
  next_retry_at?: number | null;
  started_at?: number | null;
  ended_at?: number | null;
};

export type WorkflowEdgeView = { source: string; target: string; label?: string };

export type TraceSpanView = {
  span_id: string;
  parent_span_id?: string | null;
  name: string;
  kind: string;
  status: string;
  duration_ms?: number | null;
  error?: string | null;
  input_summary?: string | null;
  output_summary?: string | null;
  redacted?: boolean;
};

export type CheckpointView = {
  checkpoint_id: string;
  checkpoint_ns?: string;
  node_id?: string | null;
  created_at: number;
  status: string;
  can_fork?: boolean;
  fork_reason?: string | null;
  requires_effect_confirmation?: boolean;
  effect_summary?: string | null;
};

export type EvaluationView = {
  evaluation_id: string;
  evaluator_name: string;
  evaluator_version: string;
  verdict: string;
  score?: number | null;
  explanation?: string | null;
  labels?: string[];
  comment?: string | null;
  experiment_version?: string | null;
  created_at?: number | null;
};

export type WorkflowDecisionOption = {
  label: string;
  value: unknown;
  description?: string | null;
  dangerous?: boolean;
};

export type WorkflowDecisionView = {
  decision_id: string;
  run_id: string;
  kind: string;
  status: "open" | "expired" | "resolved" | "cancelled" | "abandoned";
  prompt: unknown;
  nonce: string;
  version: number;
  expires_at?: number | null;
  created_at: number;
  options?: WorkflowDecisionOption[];
};

export type WorkflowDeliveryView = {
  delivery_id: string;
  status: string;
  channel?: string | null;
  attempts?: number;
  next_attempt_at?: number | null;
  last_error?: string | null;
  version: number;
};
