// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  CompanionDetailErrorResponse,
  CompanionDetailItem,
  CompanionDetailResponse,
  CompanionDetailSection,
  CompanionEvent,
} from "../../types/messages";
import "./CompanionDetailModal.styles.css";

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

export function CompanionDetailModal({ event, query, onClose }: CompanionDetailModalProps) {
  const [section, setSection] = useState<CompanionDetailSection>("overview");
  const [items, setItems] = useState<CompanionDetailItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  // 打开即读第一页，所以初始就是加载中。
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestTokenRef = useRef(0);

  const load = useCallback(
    async (cursor: string | null) => {
      // 加载中与清错由调用方先设好（渲染期重置或点击处理），这里只发请求、收结果。
      const token = ++requestTokenRef.current;
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
    },
    [event.notification.detail_version, event.notification.notification_id, query, section],
  );

  // 读取条件变了（换通知、换版本、换分区）：渲染期清空并进入加载中，effect 只发请求。
  // 比对项与 load 的依赖一致。
  const loadKey = {
    query,
    notificationId: event.notification.notification_id,
    detailVersion: event.notification.detail_version,
    section,
  };
  const [shownKey, setShownKey] = useState(loadKey);
  if (
    shownKey.query !== loadKey.query ||
    shownKey.notificationId !== loadKey.notificationId ||
    shownKey.detailVersion !== loadKey.detailVersion ||
    shownKey.section !== loadKey.section
  ) {
    setShownKey(loadKey);
    setItems([]);
    setNextCursor(null);
    setLoading(true);
    setError(null);
  }
  const reload = (cursor: string | null) => {
    setLoading(true);
    setError(null);
    void load(cursor);
  };

  useEffect(() => {
    void load(null);
  }, [load]);

  useEffect(
    () => () => {
      requestTokenRef.current += 1;
    },
    [],
  );

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="伙伴成长详情"
      data-testid="companion-detail-modal"
      className="sh-CompanionDetailModal-1"
    >
      <section className="sh-CompanionDetailModal-2">
        <header className="sh-CompanionDetailModal-3">
          <div>
            <strong>成长详情</strong>
            <div className="sh-CompanionDetailModal-4">{event.notification.summary}</div>
          </div>
          <button type="button" aria-label="关闭详情" onClick={onClose}>
            ×
          </button>
        </header>

        <nav aria-label="详情章节" className="sh-CompanionDetailModal-5">
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

        <div className="sh-CompanionDetailModal-6">
          {error && (
            <div role="alert" className="sh-CompanionDetailModal-7">
              {error}
            </div>
          )}
          {!error && items.length === 0 && !loading && (
            <div className="sh-CompanionDetailModal-8">本章节暂无可见内容。</div>
          )}
          {items.map((item) => (
            <article key={item.id} className="sh-CompanionDetailModal-9">
              {item.title && <strong>{item.title}</strong>}
              {item.summary && <div>{item.summary}</div>}
              {item.status && <div>状态：{item.status}</div>}
              {item.hash && <div>摘要：{item.hash}</div>}
              {item.occurred_at && <time>{item.occurred_at}</time>}
              {detailMetadata(item) && (
                <pre className="sh-CompanionDetailModal-10">{detailMetadata(item)}</pre>
              )}
            </article>
          ))}
        </div>

        <footer className="sh-CompanionDetailModal-11">
          {error && (
            <button type="button" disabled={loading} onClick={() => reload(null)}>
              从第一页重新加载
            </button>
          )}
          {nextCursor && !error && (
            <button type="button" disabled={loading} onClick={() => reload(nextCursor)}>
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
    Object.entries(item).filter(
      ([key, value]) =>
        !["id", "title", "summary", "status", "hash", "occurred_at"].includes(key) &&
        value !== undefined,
    ),
  );
  return Object.keys(metadata).length > 0 ? JSON.stringify(metadata, null, 2) : "";
}
