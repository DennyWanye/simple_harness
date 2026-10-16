// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// ---------------------------------------------------------------------------
// Pure ws message builders + edit→fields 解析。导出供 vitest（node env，不
// mount React，同 SettingsProviders.test.tsx 约定）单测线格式。
// ---------------------------------------------------------------------------

export function buildModelContextGetMessage(model: string) {
  return { type: "model_context_get" as const, payload: { model } };
}

export function buildContextCompactionGetMessage() {
  return { type: "context_compaction_get" as const, payload: {} };
}

export function buildContextCompactionSetMessage(model: string) {
  return { type: "context_compaction_set" as const, payload: { model } };
}

export function buildModelContextSetMessage(
  scope: "global" | "project",
  model: string,
  fields: Partial<{
    context_window: number;
    effective_pct: number;
    compact_at_pct: number;
    recall_sweet_tokens: number;
  }>,
  projectRoot?: string,
) {
  return {
    type: "model_context_set" as const,
    payload: {
      scope,
      model,
      fields,
      ...(projectRoot ? { project_root: projectRoot } : {}),
    },
  };
}

/** 把受控输入字符串解析成合法的 fields（白名单 + 范围校验）。
 *  返回 null 表示无有效改动（UI 据此提示，不发 ws）。
 *
 *  2026-06-13 用户需求改造: context_window 在本卡改【只读展示】(模型
 *  属性,自由手输危险;档位选择在「模型与参数」modal),卡片只允许调
 *  「压缩触发阈值」compact_at_pct,范围收紧 0.50–0.95(低于 0.5 疯狂
 *  压缩,高于 0.95 撞 budget block)。windowEdit 参数保留签名兼容
 *  (空串=忽略)。 */
export function parseModelContextEdits(
  windowEdit: string,
  compactEdit: string,
): { context_window?: number; compact_at_pct?: number } | null {
  const fields: { context_window?: number; compact_at_pct?: number } = {};
  const w = Number(windowEdit);
  const c = Number(compactEdit);
  if (windowEdit.trim() !== "" && Number.isFinite(w) && w > 0) {
    fields.context_window = Math.round(w);
  }
  if (Number.isFinite(c) && c >= 0.5 && c <= 0.95) {
    fields.compact_at_pct = c;
  }
  return Object.keys(fields).length === 0 ? null : fields;
}
