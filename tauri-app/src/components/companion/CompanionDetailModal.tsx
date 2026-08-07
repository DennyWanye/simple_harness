// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";

import type {
  CompanionDetailErrorResponse,
  CompanionDetailItem,
  CompanionDetailResponse,
  CompanionDetailSection,
  CompanionEvent,
} from "../../types/messages";

export type CompanionDetailQuery = (request: {
  notification_id: string;
  section: CompanionDetailSection;
  cursor: string | null;
  page_size: number;
  expected_detail_version: string;
}) => Promise<CompanionDetailResponse | CompanionDetailErrorResponse>;

export interface CompanionDetailModalProps {
  event: CompanionEvent;
  query: CompanionDetailQuery;
  onClose: () => void;
}

const sections: Array<{ id: CompanionDetailSection; label: string }> = [
  { id: "overview", label: "概览" },
  { id: "evidence", label: "证据" },
  { id: "diff", label: "变更" },
  { id: "evaluation", label: "评测" },
  { id: "decision", label: "决策" },
  { id: "operation_receipt", label: "回执" },
  { id: "current_binding", label: "当前状态" },
  { id: "audit", label: "审计" },
];

function errorText(code: CompanionDetailErrorResponse["payload"]["code"]): string {
  switch (code) {
    case "detail_changed":
      return "详情在读取期间发生变化，请从第一页重新加载。";
    case "cursor_invalid":
      return "分页游标已失效，请从第一页重新加载。";
    default:
      return "这条详情已不可用，可能已被遗忘、撤回或属于旧身份。";
  }
}

export function CompanionDetailModal({
  event,
  query,
  onClose,
}: CompanionDetailModalProps) {
  const [section, setSection] = useState<CompanionDetailSection>("overview");
  const [items, setItems] = useState<CompanionDetailItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestTokenRef = useRef(0);

  const load = useCallback(async (cursor: string | null) => {
    const token = ++requestTokenRef.current;
    setLoading(true);
    setError(null);
    try {
      const response = await query({
        notification_id: event.notification.notification_id,
        section,
        cursor,
        page_size: 20,
        expected_detail_version: event.notification.detail_version,
      });
      if (requestTokenRef.current !== token) return;
      if (response.type === "companion_detail_error") {
        setError(errorText(response.payload.code));
        if (
          response.payload.code === "detail_changed" ||
          response.payload.code === "cursor_invalid"
        ) {
          setItems([]);
          setNextCursor(null);
        }
        return;
      }
      setItems((previous) =>
        cursor ? [...previous, ...response.payload.items] : response.payload.items,
      );
      setNextCursor(response.payload.next_cursor);
    } finally {
      if (requestTokenRef.current === token) setLoading(false);
    }
  }, [event.notification.detail_version, event.notification.notification_id, query, section]);

  useEffect(() => {
    setItems([]);
    setNextCursor(null);
    void load(null);
  }, [load]);

  useEffect(() => () => {
    requestTokenRef.current += 1;
  }, []);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="伙伴成长详情"
      data-testid="companion-detail-modal"
      style={overlayStyle}
    >
      <section style={modalStyle}>
        <header style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
          <div>
            <strong>成长详情</strong>
            <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 3 }}>
              {event.notification.summary}
            </div>
          </div>
          <button type="button" aria-label="关闭详情" onClick={onClose}>×</button>
        </header>

        <nav aria-label="详情章节" style={tabsStyle}>
          {sections.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-pressed={section === item.id}
              onClick={() => setSection(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div style={{ overflow: "auto", flex: 1, minHeight: 120 }}>
          {error && <div role="alert" style={{ color: "#fca5a5" }}>{error}</div>}
          {!error && items.length === 0 && !loading && (
            <div style={{ color: "#94a3b8" }}>本章节暂无可见内容。</div>
          )}
          {items.map((item) => (
            <article key={item.id} style={itemStyle}>
              {item.title && <strong>{item.title}</strong>}
              {item.summary && <div>{item.summary}</div>}
              {item.status && <div>状态：{item.status}</div>}
              {item.hash && <div>摘要：{item.hash}</div>}
              {item.occurred_at && <time>{item.occurred_at}</time>}
              {detailMetadata(item) && (
                <pre style={metadataStyle}>{detailMetadata(item)}</pre>
              )}
            </article>
          ))}
        </div>

        <footer style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          {error && (
            <button type="button" disabled={loading} onClick={() => void load(null)}>
              从第一页重新加载
            </button>
          )}
          {nextCursor && !error && (
            <button
              type="button"
              disabled={loading}
              onClick={() => void load(nextCursor)}
            >
              {loading ? "加载中…" : "加载更多"}
            </button>
          )}
        </footer>
      </section>
    </div>
  );
}

function detailMetadata(item: CompanionDetailItem): string {
  const metadata = Object.fromEntries(
    Object.entries(item).filter(([key, value]) =>
      !["id", "title", "summary", "status", "hash", "occurred_at"].includes(key) &&
      value !== undefined
    ),
  );
  return Object.keys(metadata).length > 0
    ? JSON.stringify(metadata, null, 2)
    : "";
}

const overlayStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 10050,
  display: "grid",
  placeItems: "center",
  background: "rgba(2,6,23,.68)",
  padding: 18,
};

const modalStyle: CSSProperties = {
  width: "min(720px, 94vw)",
  maxHeight: "86vh",
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: 16,
  border: "1px solid rgba(129,140,248,.4)",
  borderRadius: 14,
  background: "#111827",
  color: "#e5e7eb",
  boxShadow: "0 24px 80px rgba(0,0,0,.45)",
};

const tabsStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
};

const itemStyle: CSSProperties = {
  display: "grid",
  gap: 4,
  padding: 10,
  marginBottom: 8,
  borderRadius: 8,
  background: "rgba(255,255,255,.05)",
  fontSize: 13,
};

const metadataStyle: CSSProperties = {
  margin: 0,
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  color: "#cbd5e1",
  fontSize: 11,
};
