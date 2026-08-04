// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useEffect, useState, type CSSProperties, type MouseEvent } from "react";

import { Icon } from "./Icon";

type Props = {
  text: string;
  align?: "left" | "right";
  tone?: "dark" | "blue" | "muted";
  inline?: boolean;
};

async function copyText(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  const ok = document.execCommand("copy");
  textarea.remove();
  if (!ok) throw new Error("copy command failed");
}

export function CopyMessageButton({
  text,
  align = "left",
  tone = "dark",
  inline = false,
}: Props) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  useEffect(() => {
    if (state === "idle") return;
    const timer = window.setTimeout(() => setState("idle"), 1400);
    return () => window.clearTimeout(timer);
  }, [state]);

  const onClick = async (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    try {
      await copyText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
  };

  const message =
    state === "copied" ? "已复制" : state === "failed" ? "复制失败" : "复制消息";

  const button = (
    <button
      type="button"
      aria-label={message}
      title={message}
      className={`deskpet-copy-message-btn deskpet-copy-message-btn--${tone}`}
      data-testid="message-copy-button"
      onClick={onClick}
      style={copyButtonStyle(tone, state)}
    >
      <Icon name={state === "copied" ? "check" : "copy"} size={13} />
    </button>
  );

  if (inline) return button;

  return (
    <div
      style={{
        display: "flex",
        justifyContent: align === "right" ? "flex-end" : "flex-start",
        marginTop: 4,
      }}
    >
      {button}
    </div>
  );
}

function copyButtonStyle(
  tone: NonNullable<Props["tone"]>,
  state: "idle" | "copied" | "failed",
): CSSProperties {
  const success = state === "copied";
  const failed = state === "failed";
  return {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 22,
    height: 22,
    padding: 0,
    borderRadius: 6,
    border: "none",
    background: "transparent",
    color: success
      ? "#86efac"
      : failed
        ? "#fca5a5"
        : tone === "blue"
          ? "#9ccfe8"
          : tone === "muted"
            ? "#64748b"
            : "#7f8794",
    cursor: "pointer",
    opacity: success || failed ? 1 : 0.68,
  };
}
