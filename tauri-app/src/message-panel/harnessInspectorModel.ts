// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { HarnessInspectorSnapshot } from "../types/messages";

export type HarnessLayerState =
  | "pending"
  | "active"
  | "completed"
  | "warning"
  | "error";

export interface HarnessLayerMessage {
  id: string;
  title: string;
  detail?: string;
  status: string;
  rawStatus?: string;
  startedAt?: number | null;
  endedAt?: number | null;
  durationMs?: number | null;
  time?: number | null;
  details?: unknown;
  errorCode?: string | null;
  errorMessage?: string | null;
}

export interface HarnessLayer {
  id:
    | "prepare"
    | "kernel"
    | "driver"
    | "provider"
    | "tools"
    | "canonical";
  label: string;
  description: string;
  summary: string;
  state: HarnessLayerState;
  messages: HarnessLayerMessage[];
}

export interface HarnessActivityStep {
  id: string;
  layerId: HarnessLayer["id"];
  layerLabel: string;
  title: string;
  summary: string;
  decision: string;
  status: string;
  time: number;
  input?: unknown;
  output?: unknown;
  missingInput?: string;
  errorCode?: string | null;
  errorMessage?: string | null;
  rawFact: string;
  explanationSource: "ui_projection";
  loopIteration?: number;
  loopPhase?: "判断" | "行动与观察";
}

export interface HarnessReactToolAction {
  callId: string;
  toolName: string;
  status: string;
  input?: unknown;
  output?: unknown;
  missingInput?: string;
  errorCode?: string | null;
  errorMessage?: string | null;
}

export interface HarnessReactLoopGroup {
  id: string;
  iteration: number;
  providerId: string;
  modelId: string;
  providerStatus: string;
  providerInput?: unknown;
  providerOutput?: unknown;
  providerMissingInput?: string;
  observableDecision: string;
  toolActions: HarnessReactToolAction[];
  observation: string;
  nextStep: string;
  errorExplanation?: string;
}

export interface HarnessProductionChainStep {
  rawName: string;
  friendlyName: string;
  explanation: string;
}

export const HARNESS_PRODUCTION_CHAIN: HarnessProductionChainStep[] = [
  {
    rawName: "ProductVenueRunAdapter.open",
    friendlyName: "产品入口",
    explanation: "解析 Session、任务范围、工作区和本轮消息。",
  },
  {
    rawName: "ProductTurnPreparer.prepare_context",
    friendlyName: "组装 Context OS",
    explanation: "把会话历史、记忆、能力和当前消息冻结为本轮输入。",
  },
  {
    rawName: "ProductTurnPreparer.prepare_direct_run",
    friendlyName: "兼容直通",
    explanation:
      "跳过旧 IntentTriage 和旧计划门禁，把已组装 Context 直接交给主 Agent；不是独立思考层。",
  },
  {
    rawName: "RunKernel · agent.general/react",
    friendlyName: "建立 Durable Root Run",
    explanation: "建立唯一 Run 身份、所有权、生命周期和固定 Profile。",
  },
  {
    rawName: "ReActDriver → AgentLoopCollaborator",
    friendlyName: "Agent 循环",
    explanation: "Provider 做可观察判断；需要工具时产生类型化工具调用。",
  },
  {
    rawName: "EffectBatchExecutor → Driver.signal",
    friendlyName: "执行并回灌观察",
    explanation: "执行已准备和授权的工具，再把结果送回同一个 AgentLoop。",
  },
  {
    rawName: "Canonical projection",
    friendlyName: "唯一终态",
    explanation: "RunKernel 写入唯一事实，再投影到消息区和本观察面板。",
  },
];

const TERMINAL_RUN = new Set(["completed", "failed", "cancelled"]);
const FAILED = new Set(["failed", "unknown", "cancelled", "rejected"]);
const ACTIVE = new Set([
  "running",
  "claimed",
  "active",
  "prepared",
  "admitted",
  "starting",
]);

export function humanStatus(status: string): string {
  const labels: Record<string, string> = {
    created: "已创建",
    starting: "启动中",
    running: "执行中",
    active: "进行中",
    claimed: "模型响应中",
    waiting: "等待中",
    pending: "待处理",
    prepared: "已准备",
    admitted: "已准入",
    ready: "已就绪",
    ready_backfill: "等待失败回填",
    settled: "已结算",
    completed: "已完成",
    succeeded: "成功",
    failed: "失败",
    cancelled: "已取消",
    rejected: "已拒绝",
    unknown: "结果未知",
    error: "失败",
    warning: "需关注",
    open: "可继续",
    background: "后台",
    closed: "已关闭",
    allowed: "已允许",
    accepted: "已接受",
  };
  return labels[status] ?? status;
}

export function humanFailureLayer(layer: string | null | undefined): string {
  const labels: Record<string, string> = {
    product_turn_preparer: "ProductTurnPreparer",
    run_kernel: "RunKernel",
    driver: "Driver / Profile",
    agent_loop: "AgentLoop",
    tool_executor: "Tool Executor",
    canonical_projection: "Canonical 投影",
  };
  return layer ? (labels[layer] ?? layer) : "Driver 终态（旧事件未记录子层）";
}

export function shortHarnessId(
  value: string | null | undefined,
  length = 9,
): string {
  if (!value) return "—";
  return value.length <= length ? value : `${value.slice(0, length)}…`;
}

function eventTitle(kind: string): string {
  const labels: Record<string, string> = {
    "run.waiting": "Run 等待继续条件",
    "run.resumed": "Run 恢复执行",
    "run.final": "Run 写入唯一终态",
    "tool.outcome": "工具结果进入 Canonical 事件",
  };
  return labels[kind] ?? kind;
}

function record(value: unknown): Record<string, unknown> | null {
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function toolNamesFromProviderOutput(value: unknown): string[] {
  const output = record(value);
  const calls = output?.tool_calls;
  if (!Array.isArray(calls)) return [];
  return calls
    .map((call) => {
      const item = record(call);
      const fn = record(item?.function);
      return String(item?.name ?? fn?.name ?? "").trim();
    })
    .filter(Boolean);
}

function toolCallIdsFromProviderOutput(value: unknown): string[] {
  const output = record(value);
  const calls = output?.tool_calls;
  if (!Array.isArray(calls)) return [];
  return calls
    .map((call) => String(record(call)?.id ?? "").trim())
    .filter(Boolean);
}

function toolArgumentsFromProviderOutput(
  value: unknown,
  callId: string,
): unknown {
  const output = record(value);
  const calls = output?.tool_calls;
  if (!Array.isArray(calls)) return undefined;
  const call = calls
    .map(record)
    .find((item) => String(item?.id ?? "").trim() === callId);
  if (!call) return undefined;
  const fn = record(call.function);
  const valueFromCall = call.arguments ?? fn?.arguments;
  if (typeof valueFromCall !== "string") return valueFromCall;
  try {
    return JSON.parse(valueFromCall);
  } catch {
    return valueFromCall;
  }
}

function toolResultFromProviderInput(value: unknown, callId: string): unknown {
  const input = record(value);
  const payload = record(input?.payload) ?? input;
  const messages = Array.isArray(payload?.messages)
    ? payload.messages
    : Array.isArray(input?.messages)
      ? input.messages
      : [];
  const message = messages
    .map(record)
    .find(
      (item) =>
        item?.role === "tool" &&
        String(item.tool_call_id ?? item.call_id ?? "").trim() === callId,
    );
  if (!message) return undefined;
  return message.content ?? message.output ?? message.result;
}

function providerDecision(value: unknown): string {
  const toolNames = toolNamesFromProviderOutput(value);
  if (toolNames.length > 0) {
    return `可观察决策：模型请求调用 ${toolNames.join("、")}。`;
  }
  const output = record(value);
  if (typeof output?.content === "string" && output.content.trim()) {
    return "可观察决策：模型返回文本，交由 Driver 判断是否结束本轮。";
  }
  return "可观察决策：等待或接收本轮模型响应；不展示隐藏思维链。";
}

export function humanFailureExplanation(
  code: string | null | undefined,
  type: string | null | undefined,
  message: string | null | undefined,
): string {
  const combined = `${code ?? ""} ${type ?? ""} ${message ?? ""}`.toLowerCase();
  if (combined.includes("402") || combined.includes("payment_required")) {
    return "GLM 云端账户余额不足，本次任务已停止。";
  }
  if (
    combined.includes("provider_dispatch_unknown_after_handoff") ||
    combined.includes("transport_error_after_handoff")
  ) {
    return "模型请求已经交给云端，但连接在收到可证明的完整响应前断开。为避免重复执行，Harness 没有自动重发。";
  }
  if (combined.includes("remoteprotocolerror")) {
    return "云端在返回完整响应前主动断开了连接。";
  }
  if (combined.includes("connecttimeout")) {
    return "连接云端模型时超时，Harness 已保留原始错误供排查。";
  }
  if (combined.includes("authorization_scope_missing")) {
    return "工具缺少可确定的授权资源范围，因此 Harness 在执行前安全拒绝了调用。";
  }
  return "本轮未能正常完成；下方仍保留原始错误码、错误消息和发生层。";
}

export function activityValuePreview(value: unknown): string {
  if (value == null) return "—";
  const projection = record(value);
  const payload = record(projection?.payload) ?? projection;
  const messages = Array.isArray(payload?.messages)
    ? payload.messages
    : Array.isArray(projection?.messages)
      ? projection.messages
      : [];
  if (messages.length > 0) {
    const latest = [...messages]
      .reverse()
      .map(record)
      .find((message) => typeof message?.content === "string");
    const content =
      typeof latest?.content === "string"
        ? latest.content.replace(/\s+/g, " ").trim()
        : "";
    const messageCount =
      typeof projection?.message_count === "number"
        ? projection.message_count
        : messages.length;
    const toolCount =
      typeof projection?.tool_count === "number"
        ? projection.tool_count
        : Array.isArray(payload?.tools)
          ? payload.tools.length
          : 0;
    const prefix = `${messageCount} 条消息${toolCount ? ` · ${toolCount} 个工具` : ""}`;
    return content
      ? `${prefix} · 最近输入：${content.slice(0, 120)}${content.length > 120 ? "…" : ""}`
      : prefix;
  }
  const text =
    typeof value === "string" ? value : JSON.stringify(value) ?? String(value);
  const compact = text.replace(/\s+/g, " ").trim();
  return `${compact.slice(0, 160)}${compact.length > 160 ? "…" : ""}`;
}

export function buildHarnessActivityFeed(
  snapshot: HarnessInspectorSnapshot,
): HarnessActivityStep[] {
  const steps: Array<HarnessActivityStep & { order: number }> = [];
  const prepareLabels: Record<string, string> = {
    prepare_context: "准备本轮 Context OS 输入",
    direct_run: "进入主 Agent（原始阶段：direct_run）",
    route_intent: "判断问题类型与是否需要澄清",
    plan_decision: "决定是否生成计划并进入 Run",
  };
  const prepareDecisions: Record<string, string> = {
    prepare_context:
      "可观察决策：将 Session 历史、记忆、能力快照和本轮消息合成为启动上下文。",
    direct_run:
      "可观察决策：没有运行旧 IntentTriage 或旧 plan gate；已组装 Context 直接进入固定 Root Profile agent.general。direct_run 是兼容直通阶段，不是独立思考层。",
    route_intent:
      "历史兼容阶段：IntentTriage 只做问题分析与澄清判断，不负责最终 Driver 路由。",
    plan_decision:
      "历史兼容阶段：确定计划门禁结果，并把冻结后的输入交给 RunKernel。",
  };

  for (const [index, step] of (
    snapshot.prepared_context.steps ?? []
  ).entries()) {
    steps.push({
      id: `feed:prepare:${step.step}:${index}`,
      layerId: "prepare",
      layerLabel: "1 · ProductTurnPreparer",
      title: prepareLabels[step.step] ?? step.step,
      summary: `${formatDuration(step.duration_ms)} · 已保存真实输入/输出`,
      decision:
        prepareDecisions[step.step] ??
        "可观察决策：完成 ProductTurnPreparer 的一个确定性阶段。",
      status: "succeeded",
      time: step.ended_at,
      input: step.input,
      output: step.output,
      rawFact: `product_turn_trace.${step.step}`,
      explanationSource: "ui_projection",
      order: 10,
    });
  }
  if ((snapshot.prepared_context.steps ?? []).length === 0) {
    steps.push({
      id: "feed:prepare:snapshot",
      layerId: "prepare",
      layerLabel: "1 · ProductTurnPreparer",
      title: snapshot.prepared_context.available
        ? "冻结 Context 与能力快照"
        : "启动快照缺失",
      summary: snapshot.prepared_context.available
        ? `${snapshot.prepared_context.prepared_ref_count} 个上下文引用`
        : "该 Run 没有可审计的启动输入",
      decision:
        "可观察决策：Run 只消费冻结后的启动快照，避免执行中上下文漂移。",
      status: snapshot.prepared_context.available ? "succeeded" : "failed",
      time:
        snapshot.prepared_context.created_at ?? snapshot.run.created_at,
      input: snapshot.prepared_context.details,
      missingInput: snapshot.prepared_context.available
        ? undefined
        : "该历史 Run 没有持久化 ProductTurnPreparer 输入。",
      rawFact: "execution_run.start_snapshot",
      explanationSource: "ui_projection",
      order: 10,
    });
  }

  steps.push({
    id: `feed:kernel:${snapshot.run.run_id}`,
    layerId: "kernel",
    layerLabel: "2 · RunKernel",
    title: "建立唯一 Root Run",
    summary: `Root 已建立 · ${snapshot.run.driver_kind} · ${snapshot.run.profile_key} · Run 当前/最终状态：${humanStatus(snapshot.run.status)}`,
    decision:
      "可观察决策：RunKernel 已建立 durable 身份、所有权和生命周期围栏；右侧状态沿用整个 Run 的当前/最终状态，不代表 Root 建立动作本身失败。",
    status: "succeeded",
    time: snapshot.run.created_at,
    input: {
      request_id: snapshot.run.request_id,
      turn_id: snapshot.run.turn_id,
      persistence_level: snapshot.run.persistence_level,
    },
    output: {
      run_id: snapshot.run.run_id,
      root_run_id: snapshot.run.root_run_id,
      owner: `${snapshot.run.owner_kind}#${snapshot.run.owner_generation}`,
    },
    rawFact: "execution_run",
    explanationSource: "ui_projection",
    order: 20,
  });

  for (const child of snapshot.lineage.filter((item) => item.parent_run_id)) {
    steps.push({
      id: `feed:child:${child.run_id}`,
      layerId: "kernel",
      layerLabel: "2 · RunKernel",
      title: `委派子 Run · ${child.profile_key}`,
      summary: `${child.driver_kind} · ${humanStatus(child.status)}`,
      decision:
        "可观察决策：父 Run 通过 Harness 委派协议启动子工作流，并等待规范化结果回传。",
      status: child.status,
      time: child.created_at,
      input: {
        parent_run_id: child.parent_run_id,
        profile_key: child.profile_key,
        driver_kind: child.driver_kind,
      },
      output: {
        child_run_id: child.run_id,
        status: child.status,
      },
      rawFact: "execution_run.parent_run_id",
      explanationSource: "ui_projection",
      order: 25,
    });
  }

  for (const [index, invocation] of snapshot.provider_invocations.entries()) {
    const names = toolNamesFromProviderOutput(invocation.output);
    steps.push({
      id: `feed:provider:${invocation.invocation_id}`,
      layerId: "provider",
      layerLabel: "4 · AgentLoop / Provider",
      title: `模型轮次 ${index + 1} · ${invocation.model_id}`,
      summary: names.length
        ? `返回 ${names.length} 个工具调用 · ${formatDuration(invocation.duration_ms)}`
        : `${invocation.provider_id} · ${formatDuration(invocation.duration_ms)}`,
      decision: providerDecision(invocation.output),
      status: invocation.status,
      time: invocation.claimed_at,
      input: invocation.input,
      output: invocation.output,
      missingInput:
        invocation.input == null
          ? "该 Provider 轮次来自旧账本，尚未保存脱敏输入投影；仅保留 request hash。"
          : undefined,
      errorCode: invocation.error_type,
      errorMessage:
        invocation.error_message ?? invocation.audit_reason ?? undefined,
      rawFact: "execution_provider_invocation",
      explanationSource: "ui_projection",
      loopIteration: invocation.iteration ?? index + 1,
      loopPhase: "判断",
      order: 40,
    });
  }

  const effectsByCall = new Map(
    snapshot.effects.map((effect) => [effect.call_id, effect]),
  );
  const providerIterationByCall = new Map<string, number>();
  const providerArgumentsByCall = new Map<string, unknown>();
  const providerResultByCall = new Map<string, unknown>();
  snapshot.provider_invocations.forEach((invocation, index) => {
    const iteration = invocation.iteration ?? index + 1;
    toolCallIdsFromProviderOutput(invocation.output).forEach((callId) => {
      providerIterationByCall.set(callId, iteration);
      const input = toolArgumentsFromProviderOutput(invocation.output, callId);
      if (input != null) providerArgumentsByCall.set(callId, input);
    });
  });
  snapshot.provider_invocations.forEach((invocation) => {
    providerIterationByCall.forEach((_providerIteration, callId) => {
      const result = toolResultFromProviderInput(invocation.input, callId);
      if (result != null) providerResultByCall.set(callId, result);
    });
  });
  for (const call of snapshot.tool_calls) {
    const effect = effectsByCall.get(call.call_id);
    const details = record(effect?.details);
    const status = effect?.status ?? call.outcome_status ?? call.admission_state;
    steps.push({
      id: `feed:tool:${call.call_record_id}`,
      layerId: "tools",
      layerLabel: "5 · Tool Executor",
      title: `执行工具 · ${call.tool_name}`,
      summary: effect
        ? `${effect.effect_type} · ${effect.handoff_state}/${effect.completion_disposition}`
        : `准入 ${humanStatus(call.admission_state)}`,
      decision:
        "可观察决策：Tool Executor 按 prepared call、资源范围和授权结果执行；参数不会从模型侧再次扩大。",
      status,
      time: call.created_at,
      input:
        providerArgumentsByCall.get(call.call_id) ??
        details?.prepared ??
        call.details,
      output:
        providerResultByCall.get(call.call_id) ??
        details?.outcome ?? {
          terminal_outcome_ref: call.terminal_outcome_ref,
          receipt_ref: effect?.receipt_ref,
        },
      missingInput:
        details?.prepared == null && call.details == null
          ? "该工具调用只保存了引用，未保存可展示的 prepared input。"
          : undefined,
      errorCode: call.error_code,
      errorMessage: call.error_message,
      rawFact: effect
        ? "execution_provider_action_call + execution_effect"
        : "execution_provider_action_call",
      explanationSource: "ui_projection",
      loopIteration: providerIterationByCall.get(call.call_id),
      loopPhase: "行动与观察",
      order: 50 + call.order / 100,
    });
  }

  for (const event of snapshot.events) {
    if (
      event.kind !== "run.final" &&
      event.kind !== "run.waiting" &&
      event.kind !== "run.resumed" &&
      !event.error_code &&
      !event.failure_code
    ) {
      continue;
    }
    steps.push({
      id: `feed:event:${event.event_id}`,
      layerId: "canonical",
      layerLabel: "6 · Canonical 投影",
      title: eventTitle(event.kind),
      summary: `Canonical #${event.seq} · ${humanStatus(event.status)}`,
      decision:
        "可观察决策：RunKernel 将唯一事实写入 Canonical 事件，再投影到消息页和 Inspector。",
      status: event.status,
      time: event.created_at,
      input: {
        payload: event.payload,
        correlation: event.correlation,
      },
      output: {
        status: event.status,
        failure_layer: event.failure_layer,
        failure_code: event.failure_code,
      },
      errorCode: event.failure_code ?? event.error_code,
      errorMessage: event.error_message,
      rawFact: `execution_event.${event.kind}`,
      explanationSource: "ui_projection",
      order: 60,
    });
  }

  return steps
    .sort((left, right) => left.time - right.time || left.order - right.order)
    .map(({ order: _order, ...step }) => step);
}

export function buildHarnessReactLoops(
  snapshot: HarnessInspectorSnapshot,
): HarnessReactLoopGroup[] {
  const effectsByCall = new Map(
    snapshot.effects.map((effect) => [effect.call_id, effect]),
  );
  const callsById = new Map(
    snapshot.tool_calls.map((call) => [call.call_id, call]),
  );

  return snapshot.provider_invocations.map((invocation, index) => {
    const callIds = toolCallIdsFromProviderOutput(invocation.output);
    const toolActions = callIds.flatMap((callId): HarnessReactToolAction[] => {
      const call = callsById.get(callId);
      if (!call) return [];
      const effect = effectsByCall.get(callId);
      const details = record(effect?.details);
      return [
        {
          callId,
          toolName: call.tool_name,
          status:
            effect?.status ?? call.outcome_status ?? call.admission_state,
          input: details?.prepared ?? call.details,
          output:
            details?.outcome ??
            (call.terminal_outcome_ref || effect?.receipt_ref
              ? {
                  terminal_outcome_ref: call.terminal_outcome_ref,
                  receipt_ref: effect?.receipt_ref,
                }
              : undefined),
          missingInput:
            details?.prepared == null && call.details == null
              ? "该工具调用没有持久化可展示输入。"
              : undefined,
          errorCode: call.error_code,
          errorMessage: call.error_message,
        },
      ];
    });
    const completedActions = toolActions.filter((action) =>
      ["completed", "succeeded", "failed", "cancelled", "rejected"].includes(
        action.status,
      ),
    ).length;
    const hasNextProvider = index < snapshot.provider_invocations.length - 1;
    const providerError =
      invocation.error_message ?? invocation.audit_reason ?? undefined;
    const errorExplanation =
      invocation.status === "unknown" || invocation.status === "failed"
        ? humanFailureExplanation(
            invocation.audit_reason,
            invocation.error_type,
            providerError,
          )
        : undefined;

    let observation = "本轮没有请求工具。";
    let nextStep = "模型返回文本，等待 Driver 判断并收口本轮。";
    if (callIds.length > 0 && toolActions.length === 0) {
      observation = `模型请求了 ${callIds.length} 个工具调用，工具账本尚未出现对应记录。`;
      nextStep = "等待 Tool Executor 准入、执行并回灌结果。";
    } else if (toolActions.length > 0) {
      observation = `Tool Executor 已记录 ${toolActions.length} 个行动，其中 ${completedActions} 个已有终态观察。`;
      nextStep = hasNextProvider
        ? "工具结果已通过 Driver.signal 回灌，AgentLoop 已进入下一轮判断。"
        : TERMINAL_RUN.has(snapshot.run.status)
          ? "工具观察已回灌；Run 已写入当前 Canonical 终态。"
          : "等待工具结果回灌或下一轮 Provider 判断。";
    } else if (errorExplanation) {
      observation = "本轮 Provider 没有留下可证明的完整输出。";
      nextStep = "Harness 已停止盲目重试，并把失败写入 Canonical 终态。";
    }

    return {
      id: `react-loop:${invocation.invocation_id}`,
      iteration: invocation.iteration ?? index + 1,
      providerId: invocation.provider_id,
      modelId: invocation.model_id,
      providerStatus: invocation.status,
      providerInput: invocation.input,
      providerOutput: invocation.output,
      providerMissingInput:
        invocation.input == null
          ? "该 Provider 轮次来自旧账本，尚未保存脱敏输入投影。"
          : undefined,
      observableDecision: providerDecision(invocation.output),
      toolActions,
      observation,
      nextStep,
      errorExplanation,
    };
  });
}

function stateFromMessages(
  messages: HarnessLayerMessage[],
  options: {
    terminal: boolean;
    terminalFailed: boolean;
    activeWhenOpen?: boolean;
    errorWhenTerminalFailed?: boolean;
  },
): HarnessLayerState {
  const hasFailure = messages.some((item) => FAILED.has(item.status));
  const hasActive = messages.some((item) => ACTIVE.has(item.status));
  if (options.terminalFailed && options.errorWhenTerminalFailed) return "error";
  if (!options.terminal && (hasActive || options.activeWhenOpen)) return "active";
  if (hasFailure) return options.terminalFailed ? "error" : "warning";
  if (!messages.length) return options.terminal ? "completed" : "pending";
  return "completed";
}

export function buildHarnessLayers(
  snapshot: HarnessInspectorSnapshot,
): HarnessLayer[] {
  const terminal = TERMINAL_RUN.has(snapshot.run.status);
  const terminalFailed = ["failed", "cancelled"].includes(snapshot.run.status);
  const finalEvent = [...snapshot.events]
    .reverse()
    .find((event) => event.kind === "run.final");
  const effectByCall = new Map(
    snapshot.effects.map((effect) => [effect.call_id, effect]),
  );
  const failureByCall = new Map(
    snapshot.failures
      .filter((failure) => failure.provider_call_id)
      .map((failure) => [failure.provider_call_id as string, failure]),
  );

  const preparerLabels: Record<string, string> = {
    prepare_context: "准备 Context 与能力快照",
    direct_run: "兼容直通主 Agent（direct_run）",
    route_intent: "模型分析意图与问题类型",
    plan_decision: "模型生成执行计划与准入决定",
  };
  const tracedPrepareMessages: HarnessLayerMessage[] = (
    snapshot.prepared_context.steps ?? []
  ).map((step, index) => ({
    id: `prepared-step:${step.step}:${index}`,
    title: preparerLabels[step.step] ?? step.step,
    detail: `${formatDuration(step.duration_ms)} · 点击查看实际输入与输出`,
    status: "succeeded",
    startedAt: step.started_at,
    endedAt: step.ended_at,
    durationMs: step.duration_ms,
    time: step.ended_at,
    details: {
      input: step.input,
      output: step.output,
    },
  }));
  const prepareMessages: HarnessLayerMessage[] = tracedPrepareMessages.length
    ? tracedPrepareMessages
    : [
        {
          id: "prepared-context",
          title: snapshot.prepared_context.available
            ? "上下文与能力快照已冻结"
            : "缺少 durable start snapshot",
          detail: snapshot.prepared_context.available
            ? `${snapshot.prepared_context.prepared_ref_count} 个上下文引用 · ${snapshot.prepared_context.terminal_delivery_count} 个终态投递`
            : "ProductTurnPreparer 没有留下可审计的启动快照",
          status: snapshot.prepared_context.available
            ? "succeeded"
            : "failed",
          time: snapshot.prepared_context.created_at,
          details: snapshot.prepared_context.details,
        },
      ];

  const kernelMessages: HarnessLayerMessage[] = [
    {
      id: `run:${snapshot.run.run_id}`,
      title: `建立根 Run ${shortHarnessId(snapshot.run.run_id, 12)}`,
      detail: `${snapshot.run.owner_kind}#${snapshot.run.owner_generation} · durable v${snapshot.run.version}`,
      status: terminal ? "succeeded" : snapshot.run.status,
      rawStatus: snapshot.run.status,
      startedAt: snapshot.run.created_at,
      endedAt: snapshot.run.ended_at ?? snapshot.run.updated_at,
      durationMs:
        ((snapshot.run.ended_at ?? snapshot.run.updated_at) -
          snapshot.run.created_at) *
        1000,
      time: snapshot.run.created_at,
      details: snapshot.run,
    },
    ...snapshot.lineage
      .filter((run) => run.parent_run_id)
      .map((run) => ({
        id: `lineage:${run.run_id}`,
        title: `挂接子 Run ${shortHarnessId(run.run_id, 12)}`,
        detail: `${run.driver_kind} · ${run.profile_key}`,
        status: run.status,
        time: run.updated_at,
      })),
    ...snapshot.events
      .filter(
        (event) =>
          event.kind.startsWith("run.") && event.kind !== "run.final",
      )
      .map((event) => ({
        id: `kernel-event:${event.event_id}`,
        title: eventTitle(event.kind),
        detail: `Canonical #${event.seq}`,
        status: event.status,
        time: event.created_at,
        errorCode: event.error_code,
        errorMessage: event.error_message,
      })),
  ];

  const driverMessages: HarnessLayerMessage[] = [
    {
      id: "driver-selection",
      title: `选择 ${snapshot.run.driver_kind} Driver`,
      detail: `Profile ${snapshot.run.profile_key}`,
      status: terminal
        ? terminalFailed
          ? "failed"
          : "succeeded"
        : "running",
      time: snapshot.run.created_at,
      errorCode: terminalFailed
        ? finalEvent?.failure_code ?? finalEvent?.error_code
        : null,
      errorMessage: terminalFailed ? finalEvent?.error_message : null,
    },
    ...(snapshot.goal
      ? [
          {
            id: `goal:${snapshot.goal.goal_id}`,
            title: `任务目标 · ${humanStatus(snapshot.goal.status)}`,
            detail: `Plan v${snapshot.goal.plan_version ?? 1} · ${shortHarnessId(snapshot.goal.task_scope_id, 13)}`,
            status: snapshot.goal.status,
            time: snapshot.goal.updated_at,
          },
        ]
      : []),
    ...snapshot.attempts.map((attempt, index) => {
      const observed = attempt.continuation_status ?? attempt.status;
      return {
        id: `attempt:${attempt.attempt_id}`,
        title: attempt.trigger_failure_set_id
          ? `吸收失败并重规划 · Plan v${attempt.plan_version}`
          : `执行计划 · Plan v${attempt.plan_version}`,
        detail:
          observed === attempt.status
            ? `Attempt ${index + 1} · ${shortHarnessId(attempt.attempt_id, 14)}`
            : `Attempt ${index + 1} · 原始账本=${attempt.status} · 观察结果=${observed}`,
        status: observed,
        rawStatus: attempt.status,
        time: attempt.updated_at,
      };
    }),
  ];

  const providerMessages: HarnessLayerMessage[] =
    snapshot.provider_invocations.map((invocation, index) => ({
      id: `provider:${invocation.invocation_id}`,
      title: `模型轮次 ${index + 1} · ${invocation.model_id}`,
      detail: `${invocation.provider_id} · ${formatDuration(invocation.duration_ms)}`,
      status: invocation.status,
      time: invocation.updated_at,
      startedAt: invocation.dispatch_started_at ?? invocation.claimed_at,
      endedAt: invocation.updated_at,
      durationMs: invocation.duration_ms,
      details: {
        invocation_id: invocation.invocation_id,
        request_hash: invocation.request_hash,
        provider: invocation.provider_id,
        model: invocation.model_id,
        adapter: invocation.adapter_id,
        policy: invocation.policy,
        input: invocation.input,
        output: invocation.output,
        audit_reason: invocation.audit_reason,
      },
      errorCode: invocation.error_type,
      errorMessage:
        [invocation.audit_reason, invocation.error_message]
          .filter(
            (value, valueIndex, values): value is string =>
              Boolean(value) && values.indexOf(value) === valueIndex,
          )
          .join(" · ") || undefined,
    }));

  const toolMessages: HarnessLayerMessage[] = snapshot.tool_calls.map(
    (call) => {
      const effect = effectByCall.get(call.call_id);
      const failure = failureByCall.get(call.call_id);
      const status =
        effect?.status ??
        call.outcome_status ??
        (failure ? "failed" : call.admission_state);
      const rawVsObserved =
        call.outcome_status && call.outcome_status !== call.admission_state
          ? ` · 原始准入=${call.admission_state}`
          : "";
      const effectDetail = effect
        ? ` · handoff=${effect.handoff_state}/${effect.completion_disposition}`
        : "";
      return {
        id: `tool:${call.call_record_id}`,
        title: `工具 · ${call.tool_name}`,
        detail: `Batch ${shortHarnessId(call.batch_id)} · 顺序 ${call.order + 1}${rawVsObserved}${effectDetail}`,
        status,
        rawStatus: call.admission_state,
        startedAt: call.created_at,
        endedAt: effect?.ended_at ?? call.updated_at,
        durationMs:
          ((effect?.ended_at ?? call.updated_at) - call.created_at) * 1000,
        time: call.updated_at,
        details: {
          call: call.details,
          effect: effect?.details,
          receipt_ref: effect?.receipt_ref,
          terminal_outcome_ref: call.terminal_outcome_ref,
        },
        errorCode: call.error_code ?? failure?.error_code,
        errorMessage: call.error_message ?? failure?.error_message,
      };
    },
  );

  const canonicalMessages: HarnessLayerMessage[] = [
    ...(snapshot.projection
      ? [
          {
            id: "task-projection",
            title: `主消息页投影 · ${humanStatus(snapshot.projection.ui_state)}`,
            detail: `任务范围 ${shortHarnessId(snapshot.projection.task_scope_id, 13)} · v${snapshot.projection.version}`,
            status:
              snapshot.projection.ui_state === "closed"
                ? "completed"
                : snapshot.projection.ui_state,
            time: snapshot.projection.updated_at,
          },
        ]
      : []),
    ...snapshot.events
      .filter(
        (event) =>
          event.kind === "run.final" || !event.kind.startsWith("run."),
      )
      .map((event) => ({
        id: `canonical:${event.event_id}`,
        title: eventTitle(event.kind),
        detail: `Canonical #${event.seq} · ${event.driver_kind}${
          event.failure_layer
            ? ` · 发生层=${humanFailureLayer(event.failure_layer)}`
            : ""
        }`,
        status: event.status,
        time: event.created_at,
        errorCode: event.failure_code ?? event.error_code,
        errorMessage: event.error_message,
        details: {
          payload: event.payload,
          correlation: event.correlation,
        },
      })),
  ];

  return [
    {
      id: "prepare",
      label: "1 · ProductTurnPreparer",
      description: "准备上下文、能力快照和启动输入",
      summary: snapshot.prepared_context.available
        ? `${snapshot.prepared_context.prepared_ref_count} 个引用`
        : "启动快照缺失",
      state: snapshot.prepared_context.available ? "completed" : "error",
      messages: prepareMessages,
    },
    {
      id: "kernel",
      label: "2 · RunKernel",
      description: "管理 Run 身份、生命周期和父子关系",
      summary: `${snapshot.lineage.length || 1} 个 Run · ${snapshot.events.filter((event) => event.kind.startsWith("run.")).length} 个生命周期事件`,
      state: terminal ? "completed" : "active",
      messages: kernelMessages,
    },
    {
      id: "driver",
      label: "3 · Driver / Profile",
      description: "执行 ReAct/Workflow，并吸收失败后重规划",
      summary: `${snapshot.run.driver_kind} · ${snapshot.attempts.length} 次计划尝试`,
      state: stateFromMessages(driverMessages, {
        terminal,
        terminalFailed,
        activeWhenOpen: true,
        errorWhenTerminalFailed: true,
      }),
      messages: driverMessages,
    },
    {
      id: "provider",
      label: "4 · AgentLoop / Provider",
      description: "模型思考、流式响应和工具意图生成",
      summary: `${snapshot.provider_invocations.length} 次模型调用`,
      state: stateFromMessages(providerMessages, {
        terminal,
        terminalFailed: false,
        activeWhenOpen: snapshot.provider_invocations.length > 0,
      }),
      messages: providerMessages,
    },
    {
      id: "tools",
      label: "5 · Tool Executor",
      description: "工具准入、执行、结果和失败证据",
      summary: `${toolMessages.filter((item) => item.status === "succeeded").length} 成功 · ${toolMessages.filter((item) => FAILED.has(item.status)).length} 失败`,
      state: stateFromMessages(toolMessages, {
        terminal,
        terminalFailed,
        activeWhenOpen: toolMessages.length > 0,
      }),
      messages: toolMessages,
    },
    {
      id: "canonical",
      label: "6 · Canonical 投影",
      description: "把唯一事实投影到消息页和最终状态",
      summary: `${snapshot.events.length} 个事件 · Run ${humanStatus(snapshot.run.status)}`,
      state: terminal
        ? terminalFailed
          ? "error"
          : "completed"
        : "pending",
      messages: canonicalMessages,
    },
  ];
}

export function formatDuration(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(value < 10_000 ? 2 : 1)} s`;
}
