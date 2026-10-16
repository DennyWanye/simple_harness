// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// ─── Data types (与 backend/deskpet/tools/artifact.py 对应) ─

export type ArtifactKind = "file" | "url" | "text" | "image" | "table";

export type ActionId = "open" | "show_in_folder" | "copy_path" | "save_as" | "preview";

export interface ArtifactAction {
  id: ActionId;
  label?: string;
}

export interface ToolArtifact {
  kind: ArtifactKind;
  path?: string | null;
  url?: string | null;
  mime?: string | null;
  title: string;
  preview?: string | null;
  size_bytes?: number | null;
  sha256?: string | null;
  created_at?: string;
  actions?: ArtifactAction[];
}

// ─── Result 解析：从 tool envelope 字符串提取 artifacts ────────

export function extractArtifactsFromResult(resultRaw: string): ToolArtifact[] {
  try {
    const obj = JSON.parse(resultRaw);
    if (obj && typeof obj === "object") {
      // 优先 envelope.artifacts（registry 包装后）
      if (Array.isArray(obj.artifacts)) {
        return obj.artifacts as ToolArtifact[];
      }
      // 兼容历史行：presenter 曾泄漏 NormalizedToolOutcome 包装
      // {state, value: {artifacts}}，已持久化的旧会话仍是这个形状
      const outcomeValue = (obj as { value?: unknown }).value;
      if (
        outcomeValue &&
        typeof outcomeValue === "object" &&
        Array.isArray((outcomeValue as { artifacts?: unknown }).artifacts)
      ) {
        return (outcomeValue as { artifacts: ToolArtifact[] }).artifacts;
      }
      // 兜底：从工具自身的 result.artifacts 嵌套（dry_run 等场景）
      if (typeof obj.result === "string") {
        try {
          const inner = JSON.parse(obj.result);
          if (inner && Array.isArray(inner.artifacts)) {
            return inner.artifacts as ToolArtifact[];
          }
        } catch {
          /* ignore */
        }
      }
    }
  } catch {
    return [];
  }
  return [];
}
