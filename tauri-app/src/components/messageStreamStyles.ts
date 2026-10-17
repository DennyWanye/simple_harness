import { type CSSProperties } from "react";

export const PALETTE = {
  warn: { accent: "#f59e0b", soft: "rgba(245, 158, 11, 0.18)", border: "rgba(245, 158, 11, 0.45)" },
  err: { accent: "#ef4444", soft: "rgba(239, 68, 68, 0.18)", border: "rgba(239, 68, 68, 0.45)" },
  user: {
    bg: "linear-gradient(135deg, rgba(15, 76, 129, 0.96), rgba(12, 58, 105, 0.96))",
    fg: "#eaf6ff",
  },
  asst: { bg: "rgba(255, 255, 255, 0.055)", fg: "#e8edf6" },
} as const;

export const rowBaseStyle: CSSProperties = {
  maxWidth: "84%",
  padding: "9px 13px",
  borderRadius: 14,
  border: "1px solid transparent",
  display: "flex",
  flexDirection: "column",
  gap: 4,
  wordBreak: "break-word",
  whiteSpace: "pre-wrap",
};

export const metaStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  fontSize: 10,
  opacity: 0.65,
  color: "#9ca3af",
};
