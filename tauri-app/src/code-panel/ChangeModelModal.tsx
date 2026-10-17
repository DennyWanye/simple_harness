// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Cursor-style per-session model and parameter picker.
 *
 * Layout mirrors Cursor's model switcher. The model dropdown is
 * data-driven from the relay's live /models catalog,
 * NOT a hardcoded preset list. Each model carries a capability map, so
 * the picker only renders the controls that model supports — gpt-5.x
 * shows Effort (reasoning_effort); claude opus/sonnet shows Thinking
 * (no Effort); an embedding model shows nothing. On 保存 we emit
 *   session_set_model { session_id, model, params }
 * with only the caps-supported keys of { thinking, fast, context, effort }.
 *
 * Back-compat: 清空 sends the legacy `{ session_id, model:null }` shape
 * (no `params` key) so the backend clears the binding and falls back to
 * the global chain / provider defaults — exactly the pre-S2 behaviour.
 *
 * Pre-fill: model from `current_model`, controls from `current_params`
 * (the backend echoes the raw picker dict back, so it round-trips).
 */
import { useState, useEffect } from "react";
import { useDepsChanged } from "../hooks/useDepsChanged";

import { controlWS } from "./controlWs";
import { buildSetModelMessage } from "../sessionModelMessages";
import {
  useSessionModelsStore,
  capsForModel,
  buildModelOptionsFromCatalog,
  filterModelOptions,
  contextWindowForModel,
  supportedWindowsForModel,
  formatContextWindow,
} from "./sessionModelsStore";
import { useSessionsStore } from "../stores/sessionsStore";
import { type SessionModelParams } from "../stores/sessionTypes";
import "./ChangeModelModal.styles.css";

type EffortValue = "low" | "medium" | "high" | "extra_high" | "max";
type ReasonMode = "default" | "thinking" | "fast";

const EFFORT_OPTIONS: ReadonlyArray<{ value: EffortValue; label: string }> = [
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "extra_high", label: "Extra High" },
  { value: "max", label: "Max" },
];

export interface ChangeModelModalProps {
  session_id: string;
  /** Current preferred_model value (null/undefined → "follow default"). */
  current_model: string | null | undefined;
  /** Current persisted model_params (null/undefined → picker defaults). */
  current_params?: SessionModelParams | null;
  onClose: () => void;
}

export function ChangeModelModal({
  session_id,
  current_model,
  current_params,
  onClose,
}: ChangeModelModalProps) {
  const catalog = useSessionModelsStore((s) => s.models);
  const default_model = useSessionModelsStore((s) => s.default_model);
  const binding = useSessionsStore((s) => s.sessions[session_id]);
  const model_opts = buildModelOptionsFromCatalog(current_model, catalog, default_model);
  const [model, set_model] = useState<string>(current_model ?? "");
  const [model_filter, set_model_filter] = useState("");
  const filtered_model_opts = filterModelOptions(model_opts, model_filter);
  const selected_model = model_opts.some((o) => o.value === model) ? model : (current_model ?? "");
  const selected_model_option = model_opts.find((option) => option.value === selected_model);
  const selected_model_is_visible = filtered_model_opts.some(
    (option) => option.value === selected_model,
  );
  // Thinking (重推理/慢) and Fast (低延迟/快) are mutually exclusive —
  // a single-select "推理模式" instead of two independent toggles.
  // "默认" = neither (provider default).
  const [reason_mode, set_reason_mode] = useState<ReasonMode>(
    current_params?.thinking ? "thinking" : current_params?.fast ? "fast" : "default",
  );
  const [effort, set_effort] = useState<EffortValue>(current_params?.effort ?? "medium");
  // 用于查 caps / 上下文档位的「生效模型」：用户显式选了就用它,否则会话
  // 固定的 preferred_model,再否则 provider 默认。2026-06-26 修复:此前只取
  // `model || current_model`,「跟随 provider 默认」时两者皆空 → caps/档位
  // 查不到 → 上下文窗口退化成只读「由 provider 决定」、选不了档,且保存时
  // model_context_set 拿空 id 被后端 save_global_window_override 拒。补上
  // default_model 兜底后,gpt-5.5 的 128K/400K/1M 三档正常可选 + 可存。
  const ctx_model = (model || current_model || default_model || "").trim();
  // Per-model capability map — gpt-5.x exposes reasoning_effort, claude
  // opus/sonnet exposes thinking; the picker only renders the controls
  // the chosen model actually supports. Unknown/custom id → permissive.
  const caps = capsForModel(ctx_model, catalog);
  // 2026-06-12: 上下文窗口从「只读」升级为「按型号可选档位」。
  // supported_windows > 1 档(如 gpt-5.5: 128K/400K/1M) → 渲染下拉,
  // 选择经 model_context_set 持久化到 backend 全局 override(压缩阈值/
  // 预算同步生效);单档/未知 → 保持只读 chip。
  const ctx_window = contextWindowForModel(ctx_model, catalog);
  const ctx_options = supportedWindowsForModel(ctx_model, catalog);
  const [ctx_choice, set_ctx_choice] = useState<number | null>(null);

  // Refresh on every open instead of trusting the process-lifetime cache.
  // A Provider can be added or edited after the control socket connected,
  // and the initial catalog request may therefore have legitimately returned
  // an empty list.  The response updates the shared store and React fills the
  // already-open selector without requiring a reconnect or app restart.
  useEffect(() => {
    controlWS.send({ type: "models_list" });
  }, []);

  // 切换模型 → 档位选择回到该模型当前值
  if (useDepsChanged([model])) set_ctx_choice(null);
  // Preserve an already-saved supported tier.  A legacy/heuristic value such
  // as 400K is not one of the temporary user tiers, so present 256K as the
  // default choice and persist it when the user saves the dialog.
  const ctx_default_choice = ctx_options.includes(ctx_window ?? -1)
    ? ctx_window
    : ctx_options.includes(256_000)
      ? 256_000
      : null;
  const ctx_selected = ctx_choice ?? ctx_default_choice ?? "";

  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [onClose]);

  const submit = () => {
    // Only emit the params the selected model supports — sending
    // reasoning_effort to a claude model (or thinking to an embedding
    // model) is meaningless. Backend mapper also clamps, but a clean
    // payload keeps the binding honest per-model.
    const params: SessionModelParams = {};
    // Single-select reasoning mode → the two backend booleans. They are
    // never both true (mutually exclusive by construction).
    if (caps.thinking) params.thinking = reason_mode === "thinking";
    if (caps.fast) params.fast = reason_mode === "fast";
    if (caps.effort) params.effort = effort;
    // 上下文档位变化 → 持久化为该模型的全局 override(非 per-session 参数:
    // 窗口是模型属性,backend 压缩阈值/预算据此对齐),然后刷新 catalog。
    const effective_ctx_choice = ctx_choice ?? ctx_default_choice;
    if (
      effective_ctx_choice != null &&
      effective_ctx_choice !== ctx_window &&
      ctx_options.includes(effective_ctx_choice)
    ) {
      // 完整协议(p4_ipc): {scope, model, fields} — 2026-06-13 修复:
      // 之前发的简版 {model, context_window} 走了一个抢路由的重复
      // handler(已删),现统一走 p4_ipc 完整版。
      controlWS.send({
        type: "model_context_set",
        payload: {
          scope: "global",
          // 用生效模型(含 default_model 兜底),否则跟随默认时拿空 id 被后端拒。
          model: ctx_model,
          fields: { context_window: effective_ctx_choice },
        },
      });
      controlWS.send({ type: "models_list" });
    }
    controlWS.send(
      buildSetModelMessage(session_id, model, params, {
        expected_binding_epoch: binding?.binding_epoch ?? 0,
        expected_provider_incarnation_id: binding?.provider_incarnation_id ?? null,
        expected_provider_config_revision: binding?.provider_config_revision ?? null,
      }),
    );
    onClose();
  };

  const clear_and_submit = () => {
    // Legacy shape (no `params`) ⇒ backend clears the binding row.
    controlWS.send(
      buildSetModelMessage(session_id, null, undefined, {
        expected_binding_epoch: binding?.binding_epoch ?? 0,
        expected_provider_incarnation_id: binding?.provider_incarnation_id ?? null,
        expected_provider_config_revision: binding?.provider_config_revision ?? null,
      }),
    );
    onClose();
  };

  return (
    <div className="sh-ChangeModelModal-1" onClick={onClose} role="dialog" aria-label="改 model">
      <div className="sh-ChangeModelModal-2" onClick={(e) => e.stopPropagation()}>
        <header className="sh-ChangeModelModal-3">模型与参数</header>

        <div className="sh-ChangeModelModal-4">
          {/* Model dropdown */}
          <label className="sh-ChangeModelModal-5" htmlFor="change-model-select">
            模型
          </label>
          <input
            type="search"
            aria-label="筛选模型"
            placeholder="输入模型名称快速筛选…"
            value={model_filter}
            onChange={(event) => set_model_filter(event.target.value)}
            className="sh-ChangeModelModal-6"
            autoFocus
          />
          <select
            id="change-model-select"
            aria-label="模型"
            value={selected_model}
            onChange={(e) => {
              set_model(e.target.value);
              set_model_filter("");
            }}
            className="sh-ChangeModelModal-7"
          >
            {!selected_model_is_visible && selected_model_option && (
              <option value={selected_model_option.value} hidden disabled>
                当前：{selected_model_option.label}
              </option>
            )}
            {filtered_model_opts.length === 0 && (
              <option value="" disabled>
                没有匹配的模型
              </option>
            )}
            {filtered_model_opts.map((o) => (
              <option key={o.value || "__default__"} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {/* Always show the effective model name; binding mechanics stay internal. */}
          {(model.trim() || default_model.trim()) && (
            <span className="sh-ChangeModelModal-8">
              当前生效：{model.trim() || default_model.trim()}
            </span>
          )}

          {/* Reasoning mode — single-select (Thinking ⟺ Fast are
              mutually exclusive: high reasoning/slow vs low latency).
              Only the modes the model supports are offered. */}
          {(caps.thinking || caps.fast) && (
            <>
              <label className="sh-ChangeModelModal-5">推理模式</label>
              <Segmented
                ariaLabel="推理模式"
                options={[
                  { value: "default" as ReasonMode, label: "默认" },
                  ...(caps.thinking
                    ? [{ value: "thinking" as ReasonMode, label: "Thinking" }]
                    : []),
                  ...(caps.fast ? [{ value: "fast" as ReasonMode, label: "Fast" }] : []),
                ]}
                value={reason_mode}
                onChange={set_reason_mode}
              />
            </>
          )}

          {/* Context window — 按型号可选档位(>1 档渲染下拉,保存时持久化
              为全局 override);单档/未知型号保持只读 chip。 */}
          {caps.context && ctx_options.length > 1 && (
            <>
              <label className="sh-ChangeModelModal-5" htmlFor="ctx-window-select">
                上下文窗口
              </label>
              <select
                id="ctx-window-select"
                aria-label="上下文窗口"
                value={String(ctx_selected ?? ctx_options[0])}
                onChange={(e) => set_ctx_choice(Number(e.target.value))}
                className="sh-ChangeModelModal-7"
              >
                {ctx_options.map((w) => (
                  <option key={w} value={String(w)}>
                    {formatContextWindow(w)}
                  </option>
                ))}
              </select>
              <span className="sh-ChangeModelModal-8">
                临时用户预算档位（默认 256K）；实际上限仍由 provider 决定，选择对所有会话生效
              </span>
            </>
          )}
          {caps.context && ctx_options.length <= 1 && (
            <>
              <label className="sh-ChangeModelModal-5">上下文窗口（模型决定）</label>
              <div className="sh-ChangeModelModal-9" aria-label="上下文窗口">
                {ctx_window != null
                  ? formatContextWindow(ctx_window) || "由 provider 决定"
                  : "由 provider 决定"}
              </div>
            </>
          )}

          {/* Effort segmented — gpt-5.x / OpenAI family only */}
          {caps.effort && (
            <>
              <label className="sh-ChangeModelModal-5">推理强度（Effort）</label>
              <Segmented
                ariaLabel="推理强度"
                options={EFFORT_OPTIONS}
                value={effort}
                onChange={set_effort}
              />
              <span className="sh-ChangeModelModal-8">
                Extra High / Max 在仅支持 low/medium/high 的后端会收敛到 High
              </span>
            </>
          )}

          {!caps.thinking && !caps.fast && !caps.context && !caps.effort && (
            <span className="sh-ChangeModelModal-8">该模型无可调参数（仅切换模型本身）</span>
          )}
        </div>

        <footer className="sh-ChangeModelModal-10">
          <button type="button" onClick={onClose} className="sh-ChangeModelModal-11">
            取消
          </button>
          <button type="button" onClick={clear_and_submit} className="sh-ChangeModelModal-11">
            清空（回全局链）
          </button>
          <button type="button" onClick={submit} className="sh-ChangeModelModal-12">
            保存
          </button>
        </footer>
      </div>
    </div>
  );
}

function Segmented<T extends string>({
  ariaLabel,
  options,
  value,
  onChange,
}: {
  ariaLabel: string;
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div role="group" aria-label={ariaLabel} className="sh-ChangeModelModal-13">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(o.value)}
            style={{
              ...segmentBtnStyle,
              background: active ? "#2563eb" : "transparent",
              color: active ? "#fff" : "#cbd5e1",
              fontWeight: active ? 600 : 500,
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

const segmentBtnStyle: React.CSSProperties = {
  flex: 1,
  border: "none",
  borderRadius: 4,
  padding: "5px 6px",
  fontSize: 11.5,
  cursor: "pointer",
  transition: "background 120ms",
};
