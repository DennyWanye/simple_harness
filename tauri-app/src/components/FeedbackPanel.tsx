// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * in-app feedback panel.
 *
 * Lets a beta user describe a problem and one-click package a redacted
 * diagnostic zip (crash reports + recent logs + anonymous metrics).
 * The "打包诊断" button stays disabled until the note is ≥ 10 chars so
 * we don't collect contentless reports.
 *
 * Privacy: the bundle is built Rust-side and never contains the API
 * key (see `src-tauri/src/diagnostics.rs`). The panel just shows the
 * resulting path + the feedback channel.
 *
 * Presentation + local state only — `onBuildBundle` is injected so the
 * panel is unit-testable without Tauri.
 */
import { memo, useState, useCallback } from "react";
import { Icon } from "./Icon";
import { isFeedbackNoteValid } from "./feedbackValidation";
import "./FeedbackPanel.styles.css";

export interface FeedbackBundleResult {
  zip_path: string;
  size_bytes: number;
  collected: Record<string, string>;
}
export interface FeedbackPanelProps {
  /** Build the diagnostic zip for the given note. */
  onBuildBundle: (note: string) => Promise<FeedbackBundleResult>;
  /** Close the panel. */
  onClose: () => void;
  /** Where users should send the zip — shown verbatim. */
  feedbackChannel?: string;
}
type BuildState = "idle" | "building" | "done" | "error";
function FeedbackPanelImpl({
  onBuildBundle,
  onClose,
  feedbackChannel = "内测交流群文件 / GitHub Issues",
}: FeedbackPanelProps) {
  const [note, setNote] = useState("");
  const [state, setState] = useState<BuildState>("idle");
  const [result, setResult] = useState<FeedbackBundleResult | null>(null);
  const [error, setError] = useState("");

  const noteOk = isFeedbackNoteValid(note);

  const handleBuild = useCallback(async () => {
    if (!noteOk) return;
    setState("building");
    setError("");
    try {
      const r = await onBuildBundle(note.trim());
      setResult(r);
      setState("done");
    } catch (e) {
      setError(String(e));
      setState("error");
    }
  }, [noteOk, note, onBuildBundle]);

  return (
    <div data-testid="feedback-panel" className="sh-FeedbackPanel-1">
      <div className="sh-FeedbackPanel-2" role="dialog" aria-label="反馈问题">
        <div className="sh-FeedbackPanel-3">
          <span className="sh-FeedbackPanel-4">
            <span className="sh-FeedbackPanel-5">
              <Icon name="bug" size={18} />
            </span>
            <h2 className="sh-FeedbackPanel-6">反馈问题</h2>
          </span>
          <button
            data-testid="feedback-close-btn"
            className="sh-FeedbackPanel-7"
            onClick={onClose}
            aria-label="关闭"
          >
            <Icon name="close" size={16} />
          </button>
        </div>

        <p className="sh-FeedbackPanel-8">
          描述你遇到的问题，Simple Harness 会打包诊断信息（崩溃记录 + 最近日志 + 匿名使用计数）。
          <strong>诊断包不含你的 API 密钥</strong>。
        </p>

        <textarea
          data-testid="feedback-note"
          className="sh-FeedbackPanel-9"
          value={note}
          placeholder="例如：点击设置保存后没反应，重启也没用…（至少 10 个字）"
          rows={5}
          onChange={(e) => setNote(e.target.value)}
        />
        <div className="sh-FeedbackPanel-10">
          {note.trim().length} 字
          {!noteOk && note.length > 0 && (
            <span className="sh-FeedbackPanel-11">{"\u3000（再写一点，至少 10 字）"}</span>
          )}
        </div>

        <button
          data-testid="feedback-build-btn"
          style={{
            ...buildBtnStyle,
            opacity: noteOk && state !== "building" ? 1 : 0.5,
            cursor: noteOk && state !== "building" ? "pointer" : "not-allowed",
          }}
          disabled={!noteOk || state === "building"}
          onClick={handleBuild}
        >
          {state === "building" ? "打包中…" : "一键打包诊断"}
        </button>

        {state === "done" && result && (
          <div data-testid="feedback-result" className="sh-FeedbackPanel-12">
            <p className="sh-FeedbackPanel-13">
              <Icon name="check" size={15} />
              诊断包已生成（{(result.size_bytes / 1024).toFixed(0)} KB）
            </p>
            <code className="sh-FeedbackPanel-14">{result.zip_path}</code>
            <p className="sh-FeedbackPanel-15">
              文件已在资源管理器中高亮显示。请把它发送到：
              <strong> {feedbackChannel}</strong>
            </p>
          </div>
        )}

        {state === "error" && (
          <p data-testid="feedback-error" className="sh-FeedbackPanel-16">
            ✗ 打包失败：{error}
            <br />
            你也可以手动到安装目录的 <code>crash_reports/</code> 取文件。
          </p>
        )}
      </div>
    </div>
  );
}
export const FeedbackPanel = memo(FeedbackPanelImpl);
// --------------------------- styles ----------------------------------

const buildBtnStyle: React.CSSProperties = {
  width: "100%",
  padding: "11px 0",
  fontSize: 14,
  fontWeight: 700,
  background: "linear-gradient(180deg, #3b82f6 0%, #2563eb 100%)",
  color: "#fff",
  border: "1px solid rgba(37,99,235,0.5)",
  borderRadius: 11,
  boxShadow: "0 6px 18px rgba(37,99,235,0.32)",
};
