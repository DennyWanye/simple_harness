import { useEffect, useRef, useState } from "react";
import { Icon } from "../Icon";
import type { CheckpointView } from "./types";

export type ReplayForkInput = {
  statePatch?: Record<string, unknown>;
  confirmEffects: boolean;
};

type Props = {
  open: boolean;
  checkpoint?: CheckpointView | null;
  submitting?: boolean;
  error?: string | null;
  onClose: () => void;
  onSubmit: (input: ReplayForkInput) => void;
};

export function ReplayDialog({ open, checkpoint, submitting = false, error, onClose, onSubmit }: Props) {
  const [patchText, setPatchText] = useState("");
  const [confirmEffects, setConfirmEffects] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const locked = useRef(false);
  useEffect(() => {
    if (!open) return;
    setPatchText("");
    setConfirmEffects(false);
    setValidationError(null);
    locked.current = false;
  }, [open, checkpoint?.checkpoint_id]);
  useEffect(() => {
    locked.current = submitting;
  }, [submitting]);
  useEffect(() => {
    if (error) locked.current = false;
  }, [error]);
  if (!open || !checkpoint) return null;

  const forkable = checkpoint.can_fork !== false;
  const requiresConfirmation = checkpoint.requires_effect_confirmation === true;
  const submit = () => {
    if (locked.current || submitting || !forkable) return;
    let statePatch: Record<string, unknown> | undefined;
    if (patchText.trim()) {
      try {
        const parsed: unknown = JSON.parse(patchText);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("状态补丁必须是 JSON object");
        statePatch = parsed as Record<string, unknown>;
      } catch (cause) {
        setValidationError(cause instanceof Error ? cause.message : "状态补丁格式无效");
        return;
      }
    }
    if (requiresConfirmation && !confirmEffects) {
      setValidationError("请先确认可能重新执行的副作用");
      return;
    }
    setValidationError(null);
    locked.current = true;
    onSubmit({ statePatch, confirmEffects });
  };

  return (
    <div role="presentation" style={backdropStyle} onMouseDown={(event) => event.target === event.currentTarget && !submitting && onClose()}>
      <div role="dialog" aria-modal="true" aria-label="从 Checkpoint 创建分支" style={dialogStyle}>
        <div style={headerStyle}>
          <span style={{ display: "flex", alignItems: "center", gap: 7 }}><Icon name="refresh" size={15} /><strong>创建分支运行</strong></span>
          <button type="button" aria-label="关闭" title="关闭" disabled={submitting} onClick={onClose} style={iconButtonStyle}><Icon name="close" size={14} /></button>
        </div>
        <div style={contentStyle}>
          <div style={checkpointStyle}>
            <span>Checkpoint</span>
            <strong style={{ overflowWrap: "anywhere" }}>{checkpoint.checkpoint_id}</strong>
            <span>{checkpoint.node_id ?? "root"} · {checkpoint.status}</span>
          </div>
          {!forkable && <div role="alert" style={errorStyle}>{checkpoint.fork_reason ?? "这个 checkpoint 不能用于分支"}</div>}
          <label style={fieldStyle}>
            <span>状态补丁（可选）</span>
            <textarea aria-label="状态补丁" value={patchText} disabled={submitting || !forkable} onChange={(event) => setPatchText(event.target.value)} rows={5} placeholder={'{"values": {}}'} style={textareaStyle} />
          </label>
          {requiresConfirmation && (
            <label style={confirmStyle}>
              <input type="checkbox" checked={confirmEffects} disabled={submitting} onChange={(event) => setConfirmEffects(event.target.checked)} />
              <span><strong>确认重新执行副作用</strong>{checkpoint.effect_summary && <small>{checkpoint.effect_summary}</small>}</span>
            </label>
          )}
          {(validationError || error) && <div role="alert" style={errorStyle}>{validationError ?? error}</div>}
        </div>
        <div style={footerStyle}>
          <button type="button" disabled={submitting} onClick={onClose} style={buttonStyle(false, submitting)}>取消</button>
          <button type="button" disabled={submitting || !forkable} onClick={submit} style={buttonStyle(true, submitting || !forkable)}><Icon name={submitting ? "loader" : "plus"} size={13} />创建分支</button>
        </div>
      </div>
    </div>
  );
}

const backdropStyle: React.CSSProperties = { position: "absolute", inset: 0, zIndex: 3, display: "grid", placeItems: "center", padding: 12, background: "rgba(2,6,23,0.72)" };
const dialogStyle: React.CSSProperties = { width: "min(460px,100%)", maxHeight: "min(620px,100%)", display: "grid", gridTemplateRows: "auto minmax(0,1fr) auto", overflow: "hidden", border: "1px solid rgba(148,163,184,0.25)", borderRadius: 8, background: "#0b1120", color: "#e5e7eb", boxShadow: "0 18px 55px rgba(0,0,0,0.42)" };
const headerStyle: React.CSSProperties = { minHeight: 44, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "7px 10px", borderBottom: "1px solid rgba(148,163,184,0.14)", fontSize: 12 };
const contentStyle: React.CSSProperties = { minHeight: 0, overflow: "auto", display: "grid", alignContent: "start", gap: 10, padding: 10 };
const checkpointStyle: React.CSSProperties = { display: "grid", gap: 3, paddingBottom: 9, borderBottom: "1px solid rgba(148,163,184,0.12)", color: "#94a3b8", fontSize: 10 };
const fieldStyle: React.CSSProperties = { display: "grid", gap: 6, color: "#cbd5e1", fontSize: 11 };
const textareaStyle: React.CSSProperties = { width: "100%", minWidth: 0, resize: "vertical", padding: 8, border: "1px solid rgba(148,163,184,0.25)", borderRadius: 6, background: "#0f172a", color: "#e5e7eb", fontFamily: "ui-monospace, monospace", fontSize: 10 };
const confirmStyle: React.CSSProperties = { display: "flex", alignItems: "flex-start", gap: 8, padding: 8, borderLeft: "3px solid #f59e0b", background: "rgba(120,53,15,0.15)", color: "#fde68a", fontSize: 11 };
const errorStyle: React.CSSProperties = { padding: 8, borderLeft: "3px solid #f87171", background: "rgba(127,29,29,0.15)", color: "#fca5a5", fontSize: 10, overflowWrap: "anywhere" };
const footerStyle: React.CSSProperties = { minHeight: 48, display: "flex", justifyContent: "flex-end", gap: 7, padding: 8, borderTop: "1px solid rgba(148,163,184,0.14)" };
const iconButtonStyle: React.CSSProperties = { width: 30, height: 30, display: "grid", placeItems: "center", border: "1px solid rgba(148,163,184,0.2)", borderRadius: 6, background: "transparent", color: "#cbd5e1", cursor: "pointer" };

function buttonStyle(primary: boolean, disabled: boolean): React.CSSProperties {
  return { minHeight: 31, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6, padding: "5px 11px", border: `1px solid ${primary ? "rgba(56,189,248,0.35)" : "rgba(148,163,184,0.25)"}`, borderRadius: 6, background: primary ? "rgba(3,105,161,0.28)" : "transparent", color: primary ? "#bae6fd" : "#cbd5e1", cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.55 : 1, fontSize: 11 };
}
