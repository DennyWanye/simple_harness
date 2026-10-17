// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
// 代码质量整治阶段 10：TS/TSX 只改了注释的核对（阶段10-切片设计.md 裁决执行条件 3）。
// 用法：node scripts/code_quality/ts_comment_only_check.mjs <ref> <文件...>   （在仓库根目录运行）
// 取语法树的全部词法叶子（getChildren 递归到叶子；注释是空白杂项，不在其中；JSX 文本与字面量按原文），
// 两版序列逐个相同才通过。不做任何规范化（括号、空白之外的一切都算）。
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(new URL("../../tauri-app/package.json", import.meta.url));
const ts = require("typescript");
const [ref, ...files] = process.argv.slice(2);

function tokens(name, text) {
  const kind = name.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true, kind);
  const out = [];
  const walk = (n) => {
    const kids = n.getChildren(sf);
    if (kids.length === 0) {
      if (n.kind !== ts.SyntaxKind.EndOfFileToken) out.push([ts.SyntaxKind[n.kind], n.getText(sf), sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1]);
      return;
    }
    kids.forEach(walk);
  };
  walk(sf);
  return out;
}

let bad = 0;
for (const f of files) {
  const before = tokens(f, execFileSync("git", ["show", `${ref}:${f}`], { encoding: "utf8" }));
  const after = tokens(f, readFileSync(f, "utf8"));
  let i = 0;
  while (i < before.length && i < after.length && before[i][0] === after[i][0] && before[i][1] === after[i][1]) i++;
  if (i === before.length && i === after.length) continue;
  bad++;
  console.log(`  ${f}：注释之外有变化（原第 ${before[i]?.[2] ?? "?"} 行 ${JSON.stringify(before[i]?.[1] ?? null)} → 现第 ${after[i]?.[2] ?? "?"} 行 ${JSON.stringify(after[i]?.[1] ?? null)}）`);
}
console.log(`TS 只改注释核对：${files.length} 个文件，${bad ? "不通过" : "通过"}`);
process.exit(bad ? 1 : 0);
