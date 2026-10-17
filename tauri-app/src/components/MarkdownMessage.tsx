// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 共享 Markdown 渲染组件。
 *
 * 背景：code 模式的 `AssistantBubble`（code-panel/MessageBubble.tsx）一直用
 * react-markdown 渲染 LLM 回复，而消息流（components/MessageStreamRows.tsx
 * 的 ChatRow）此前把 assistant 文本当**纯文本** div 渲染 → 用户看到的是 raw
 * markdown（```code```、**bold**、列表标记全裸露）。
 *
 * 把那套 react-markdown + 自定义 components（代码块高亮、本地文件链接走
 * artifact_open、列表/段落间距）抽到这里，两处共用，保证 code 模式和消息流
 * 的渲染一致。
 */
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { invoke } from "@tauri-apps/api/core";

import { CodeBlock, InlineCode } from "../code-panel/CodeBlock";
import "./MarkdownMessage.styles.css";

// 判断 markdown 链接 href 是否指向本地文件（而非 http(s)/mailto 等网络链接）。
// 命中：Windows 盘符路径 (C:\... / C:/...)、UNC (\\server\...)、file:// 协议、POSIX 绝对路径 (/...)。
function isLocalFilePath(href: string): boolean {
  if (!href) return false;
  const h = href.trim();
  if (/^[a-zA-Z]:[\\/]/.test(h)) return true; // C:\... or C:/...
  if (h.startsWith("\\\\")) return true; // UNC \\server\share
  if (/^file:\/\//i.test(h)) return true; // file:// 协议
  if (h.startsWith("/")) return true; // POSIX 绝对路径
  return false;
}

// 把 href 规整成 artifact_open 可用的本地路径（剥掉 file:// 前缀）。
function toLocalPath(href: string): string {
  const h = href.trim();
  if (/^file:\/\//i.test(h)) {
    try {
      // file:///C:/x.pptx → C:/x.pptx ; file://server/share → //server/share
      return decodeURIComponent(h.replace(/^file:\/\//i, "").replace(/^\/([a-zA-Z]:)/, "$1"));
    } catch {
      return h.replace(/^file:\/\//i, "");
    }
  }
  return h;
}

export function MarkdownMessage({ children }: { children: string }) {
  return (
    <ReactMarkdown
      // remark-gfm: 表格 / 删除线 / 任务列表 / 自动链接（LLM 回复里大量用 GFM 表格，
      // 没它会把 `| 列 | 列 |` 当裸文本显示）。
      remarkPlugins={[remarkGfm]}
      components={{
        code: ({
          inline,
          className,
          children,
        }: {
          inline?: boolean;
          className?: string;
          children?: ReactNode;
        }) => {
          const match = /language-(\w+)/.exec(className || "");
          if (!inline && match) {
            return <CodeBlock language={match[1]}>{String(children).replace(/\n$/, "")}</CodeBlock>;
          }
          return <InlineCode>{children}</InlineCode>;
        },
        p: ({ children }) => <p className="sh-MarkdownMessage-1">{children}</p>,
        ul: ({ children }) => <ul className="sh-MarkdownMessage-2">{children}</ul>,
        ol: ({ children }) => <ol className="sh-MarkdownMessage-2">{children}</ol>,
        // GFM 表格：深色主题描边 + 横向滚动（宽表不撑爆气泡）。
        table: ({ children }) => (
          <div className="sh-MarkdownMessage-3">
            <table className="sh-MarkdownMessage-4">{children}</table>
          </div>
        ),
        th: ({ children }) => <th className="sh-MarkdownMessage-5">{children}</th>,
        td: ({ children }) => <td className="sh-MarkdownMessage-6">{children}</td>,
        a: ({ href, children }) => {
          const url = typeof href === "string" ? href : "";
          if (isLocalFilePath(url)) {
            // 本地文件链接（如 LLM 写的 [打开 PPT](C:\...\xxx.pptx)）：
            // webview 里 href 打不开 → 改走 Tauri artifact_open 用系统默认应用打开。
            const localPath = toLocalPath(url);
            return (
              <a
                href={url}
                onClick={(e) => {
                  e.preventDefault();
                  void invoke("artifact_open", { path: localPath }).catch((err) =>
                    console.error("[artifact_open] failed", err),
                  );
                }}
                className="sh-MarkdownMessage-7"
                title={localPath}
              >
                {children}
              </a>
            );
          }
          return (
            <a
              href={url}
              target="_blank"
              rel="noreferrer noopener"
              className="sh-MarkdownMessage-8"
            >
              {children}
            </a>
          );
        },
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
