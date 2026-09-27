// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 新建任务表单里的「完成后发布一个文件」（NEXT-TG-1.0 §9，遗留 2A.1c）。
 *
 * 系统只认 `action:file_publish.publish:<文件名>` 这种写法的发布要求——执行步骤据此拿到"发布候选"格式说明，
 * 发布候选也只在这条要求的范围内才被接受。以前用普通中文写"发布到…"，任务会一直等一个永远不来的候选。
 * 这里填文件名就替你加上那一行；要求里出现"发布/上传"等字样却没有这一行时，就地提醒。
 */
import { useState } from "react";
import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import { plainPublishMention, publishCriterion } from "./publishCriterion";

export function PublishCriterionHelper({ criteria, onChange, disabled, publish }: {
  criteria: string; onChange: (next: string) => void; disabled: boolean;
  publish: { enabled?: boolean; reason?: string } | null | undefined;
}) {
  const [file, setFile] = useState("");
  const enabled = publish?.enabled === true;
  const add = () => {
    const line = publishCriterion(file);
    const lines = criteria.split("\n").map((l) => l.trim()).filter(Boolean);
    if (!lines.includes(line)) onChange([...lines, line].join("\n"));
    setFile("");
  };
  const muted = { color: dark.textMuted, fontSize: tokens.text.sm.size };
  return (
    <div data-testid="publish-criterion-helper" style={{ display: "grid", gap: 4 }}>
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <label htmlFor="publish-file" style={muted}>完成后发布文件（可选）</label>
        <input id="publish-file" aria-label="完成后发布的文件名" placeholder="例如 NOTES.md" value={file}
          disabled={disabled || !enabled} onChange={(e) => setFile(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && file.trim()) { e.preventDefault(); add(); } }}
          style={{ flex: 1, padding: "4px 8px", borderRadius: 4, border: `1px solid ${dark.border}`, background: dark.card, color: dark.text }} />
        <button type="button" disabled={disabled || !enabled || !file.trim()} onClick={add}>加入成功条件</button>
      </div>
      {!enabled && <div style={muted}>发布目录还没授权（设置 → 任务发布目录），带发布的任务暂时不能建。{publish?.reason ? `（${publish.reason}）` : ""}</div>}
      {enabled && plainPublishMention(criteria) && (
        <div role="status" style={{ color: dark.warning, fontSize: tokens.text.sm.size }}>
          成功条件里提到了发布，但系统只认"完成后发布文件"加进去的那一行；想真的发布，请在上面填文件名加进去。
        </div>
      )}
    </div>
  );
}
