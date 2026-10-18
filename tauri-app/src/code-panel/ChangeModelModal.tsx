// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 主对话的「切换模型」弹窗。
 *
 * 主对话的前台运行固定使用提供方链首（排第一的启用提供方）的默认模型，不按会话记模型，
 * 也不读推理参数。所以这里改的就是链首提供方的 default_model（与设置页同一处），对所有
 * 对话生效：保存时发 settings_providers_update，所选模型不在该提供方的模型列表里时先追加
 * 再设默认（后台只接受列表里的默认模型）；上下文档位照旧经 model_context_set 写全局覆盖。
 */
import { useState, useEffect } from "react";
import { useDepsChanged } from "../hooks/useDepsChanged";

import { controlWS } from "./controlWs";
import { buildListRequestMessage, buildSetDefaultModelMessage } from "../components/providersStore";
import { effective_provider_model, useProvidersStore } from "./providersStore";
import {
  useSessionModelsStore,
  capsForModel,
  buildModelOptionsFromCatalog,
  filterModelOptions,
  contextWindowForModel,
  supportedWindowsForModel,
  formatContextWindow,
} from "./sessionModelsStore";
import "./ChangeModelModal.styles.css";

export interface ChangeModelModalProps {
  onClose: () => void;
}

export function ChangeModelModal({ onClose }: ChangeModelModalProps) {
  const catalog = useSessionModelsStore((s) => s.models);
  const default_model = useSessionModelsStore((s) => s.default_model);
  const providers = useProvidersStore((s) => s.providers);
  // 链首：按优先级排第一的启用提供方（与后台 registry.get_chain()[0] 同口径）。
  const head = providers.find((provider) => provider.enabled !== false) ?? null;
  const current_model = effective_provider_model(null, providers) || default_model || null;
  // 第一项（值为空）就是当前默认模型，表示不改；其余为目录里的其他模型。
  const model_opts = buildModelOptionsFromCatalog(null, catalog, current_model);
  const [model, set_model] = useState<string>("");
  const [model_filter, set_model_filter] = useState("");
  const filtered_model_opts = filterModelOptions(model_opts, model_filter);
  const selected_model = model_opts.some((o) => o.value === model) ? model : (current_model ?? "");
  const selected_model_option = model_opts.find((option) => option.value === selected_model);
  const selected_model_is_visible = filtered_model_opts.some(
    (option) => option.value === selected_model,
  );
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
    controlWS.send(buildListRequestMessage());
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
    // 上下文档位变化 → 持久化为该模型的全局 override（窗口是模型属性），然后刷新目录。
    const effective_ctx_choice = ctx_choice ?? ctx_default_choice;
    if (
      effective_ctx_choice != null &&
      effective_ctx_choice !== ctx_window &&
      ctx_options.includes(effective_ctx_choice)
    ) {
      controlWS.send({
        type: "model_context_set",
        payload: {
          scope: "global",
          model: ctx_model,
          fields: { context_window: effective_ctx_choice },
        },
      });
      controlWS.send({ type: "models_list" });
    }
    const chosen = model.trim();
    if (head && chosen && chosen !== (current_model ?? "")) {
      controlWS.send(buildSetDefaultModelMessage(head, chosen));
    }
    onClose();
  };

  return (
    <div className="sh-ChangeModelModal-1" onClick={onClose} role="dialog" aria-label="切换模型">
      <div className="sh-ChangeModelModal-2" onClick={(e) => e.stopPropagation()}>
        <header className="sh-ChangeModelModal-3">切换模型</header>

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

          <span className="sh-ChangeModelModal-8">
            改的是默认模型（排第一的提供方），所有对话都会使用它
          </span>
        </div>

        <footer className="sh-ChangeModelModal-10">
          <button type="button" onClick={onClose} className="sh-ChangeModelModal-11">
            取消
          </button>
          <button type="button" onClick={submit} className="sh-ChangeModelModal-12">
            保存
          </button>
        </footer>
      </div>
    </div>
  );
}
