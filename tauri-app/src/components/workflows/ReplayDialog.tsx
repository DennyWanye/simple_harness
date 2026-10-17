import { useEffect, useRef, useState } from "react";
import { Icon } from "../Icon";
import type { CheckpointView } from "./types";
import "./ReplayDialog.styles.css";

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

export function ReplayDialog({
  open,
  checkpoint,
  submitting = false,
  error,
  onClose,
  onSubmit,
}: Props) {
  const [patchText, setPatchText] = useState("");
  const [confirmEffects, setConfirmEffects] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const locked = useRef(false);
  // 打开或换了 Checkpoint 就清空草稿：渲染期按上一次比对，不在 effect 里 setState。
  const replayKey = JSON.stringify([open, checkpoint?.checkpoint_id]);
  const [shownReplay, setShownReplay] = useState(replayKey);
  if (shownReplay !== replayKey) {
    setShownReplay(replayKey);
    if (open) {
      setPatchText("");
      setConfirmEffects(false);
      setValidationError(null);
    }
  }
  useEffect(() => {
    if (!open) return;
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
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
          throw new Error("状态补丁必须是 JSON object");
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
    <div
      role="presentation"
      className="sh-ReplayDialog-1"
      onMouseDown={(event) => event.target === event.currentTarget && !submitting && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="从 Checkpoint 创建分支"
        className="sh-ReplayDialog-2"
      >
        <div className="sh-ReplayDialog-3">
          <span className="sh-ReplayDialog-4">
            <Icon name="refresh" size={15} />
            <strong>创建分支运行</strong>
          </span>
          <button
            type="button"
            aria-label="关闭"
            title="关闭"
            disabled={submitting}
            onClick={onClose}
            className="sh-ReplayDialog-5"
          >
            <Icon name="close" size={14} />
          </button>
        </div>
        <div className="sh-ReplayDialog-6">
          <div className="sh-ReplayDialog-7">
            <span>Checkpoint</span>
            <strong className="sh-ReplayDialog-8">{checkpoint.checkpoint_id}</strong>
            <span>
              {checkpoint.node_id ?? "root"} · {checkpoint.status}
            </span>
          </div>
          {!forkable && (
            <div role="alert" className="sh-ReplayDialog-9">
              {checkpoint.fork_reason ?? "这个 checkpoint 不能用于分支"}
            </div>
          )}
          <label className="sh-ReplayDialog-10">
            <span>状态补丁（可选）</span>
            <textarea
              aria-label="状态补丁"
              value={patchText}
              disabled={submitting || !forkable}
              onChange={(event) => setPatchText(event.target.value)}
              rows={5}
              placeholder={'{"values": {}}'}
              className="sh-ReplayDialog-11"
            />
          </label>
          {requiresConfirmation && (
            <label className="sh-ReplayDialog-12">
              <input
                type="checkbox"
                checked={confirmEffects}
                disabled={submitting}
                onChange={(event) => setConfirmEffects(event.target.checked)}
              />
              <span>
                <strong>确认重新执行副作用</strong>
                {checkpoint.effect_summary && <small>{checkpoint.effect_summary}</small>}
              </span>
            </label>
          )}
          {(validationError || error) && (
            <div role="alert" className="sh-ReplayDialog-9">
              {validationError ?? error}
            </div>
          )}
        </div>
        <div className="sh-ReplayDialog-13">
          <button
            type="button"
            disabled={submitting}
            onClick={onClose}
            style={buttonStyle(false, submitting)}
          >
            取消
          </button>
          <button
            type="button"
            disabled={submitting || !forkable}
            onClick={submit}
            style={buttonStyle(true, submitting || !forkable)}
          >
            <Icon name={submitting ? "loader" : "plus"} size={13} />
            创建分支
          </button>
        </div>
      </div>
    </div>
  );
}

function buttonStyle(primary: boolean, disabled: boolean): React.CSSProperties {
  return {
    minHeight: 31,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    padding: "5px 11px",
    border: `1px solid ${primary ? "rgba(56,189,248,0.35)" : "rgba(148,163,184,0.25)"}`,
    borderRadius: 6,
    background: primary ? "rgba(3,105,161,0.28)" : "transparent",
    color: primary ? "#bae6fd" : "#cbd5e1",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.55 : 1,
    fontSize: 11,
  };
}
