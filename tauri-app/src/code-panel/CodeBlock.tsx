// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * code block renderer.
 *
 * react-syntax-highlighter's Prism *light* build has been shipping
 * a broken `register` export in recent versions; trying to register
 * any language at module-load time throws `Prism.register is not a
 * function`. That used to crash the panel root → vite triggered
 * full-page reload → WebSocket reconnect storm.
 *
 * We sidestep the whole light-build mess by using the bundle's
 * default async `Prism` export, which loads languages on demand
 * via Prism core (lazy + correct API). It's slightly bigger
 * (around +60 KB gzipped vs light) but reliably highlights every
 * language without registration footwork.
 */
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import vscDarkPlus from "react-syntax-highlighter/dist/esm/styles/prism/vsc-dark-plus";
import "./CodeBlock.styles.css";

interface Props {
  language?: string;
  children: string;
}

export function CodeBlock({ language, children }: Props) {
  // Strip trailing newline that markdown parsers commonly tack on.
  const code = children.replace(/\n$/, "");
  return (
    <div className="sh-CodeBlock-1">
      {language && <span className="sh-CodeBlock-2">{language}</span>}
      <SyntaxHighlighter
        language={language || "text"}
        style={vscDarkPlus}
        customStyle={{
          margin: 0,
          padding: "10px 12px",
          fontSize: 12.5,
          lineHeight: 1.5,
          borderRadius: 6,
          background: "#1e1e1e",
        }}
        wrapLongLines
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}

/** Inline `code` (no language, single line). */
export function InlineCode({ children }: { children: React.ReactNode }) {
  return <code className="sh-CodeBlock-3">{children}</code>;
}
