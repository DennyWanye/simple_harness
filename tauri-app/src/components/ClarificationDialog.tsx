// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import React, { useEffect, useRef, useState } from "react";

import type { ClarificationRequest } from "../types/skillPlatform";
import { inputStyle } from "../theme/components";
import "./ClarificationDialog.styles.css";

interface Props {
  current: ClarificationRequest["payload"] | null;
  onResolve: (answer: string) => void;
}

export const ClarificationDialog: React.FC<Props> = ({ current, onResolve }) => {
  const [answer, setAnswer] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);
  // 换了一条澄清请求就清空输入（渲染期按上一条比对，不在 effect 里 setState）。
  const [shownRequest, setShownRequest] = useState(current);
  if (shownRequest !== current) {
    setShownRequest(current);
    if (current) setAnswer("");
  }

  useEffect(() => {
    if (!current) return;
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [current]);

  if (!current) return null;

  const submit = () => {
    onResolve(answer.trim());
  };

  const options = Array.isArray(current.options) ? current.options : [];

  return (
    <div
      className="sh-ClarificationDialog-1"
      role="dialog"
      aria-modal="true"
      aria-labelledby="clarification-dialog-title"
    >
      <div className="sh-ClarificationDialog-2">
        <div className="sh-ClarificationDialog-3">
          <div className="sh-ClarificationDialog-4">澄清请求</div>
          <h3 id="clarification-dialog-title" className="sh-ClarificationDialog-5">
            {current.question}
          </h3>
        </div>

        <div className="sh-ClarificationDialog-6">
          {options.length > 0 && (
            <div className="sh-ClarificationDialog-7">
              {options.map((option) => (
                <button
                  key={option}
                  type="button"
                  className="bp-btn-secondary sh-ClarificationDialog-8"
                  onClick={() => onResolve(option)}
                >
                  {option}
                </button>
              ))}
            </div>
          )}

          <div className="sh-ClarificationDialog-9">
            <input
              ref={inputRef}
              value={answer}
              onChange={(event) => setAnswer(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  submit();
                }
              }}
              style={{ ...inputStyle, flex: 1 }}
              aria-label="澄清回答"
            />
            <button
              type="button"
              className="bp-btn-primary sh-ClarificationDialog-10"
              onClick={submit}
            >
              确认
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ClarificationDialog;
