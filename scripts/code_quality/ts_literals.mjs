// 代码质量整治：用 TypeScript 编译器提取前端源码里的字符串字面量（供 guard.py 调用）。
// 用法：node scripts/code_quality/ts_literals.mjs <ref|WORKTREE>   输出 JSON 数组（字面量的值）
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const require = createRequire(new URL("../../tauri-app/package.json", import.meta.url));
const ts = require("typescript");
const ref = process.argv[2] || "WORKTREE";
const list = ref === "WORKTREE"
  ? execFileSync("git", ["ls-files", "--cached", "--others", "--exclude-standard", "tauri-app/src"], { encoding: "utf8" })
  : execFileSync("git", ["ls-tree", "-r", "--name-only", ref, "tauri-app/src"], { encoding: "utf8" });
const files = list.split("\n").filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\.tsx?$/.test(f));
const out = [];
for (const f of files) {
  let src;
  try {
    src = ref === "WORKTREE" ? readFileSync(f, "utf8")
      : execFileSync("git", ["show", `${ref}:${f}`], { encoding: "utf8", maxBuffer: 64 << 20 });
  } catch { continue; }
  const sf = ts.createSourceFile(f, src, ts.ScriptTarget.Latest, true,
    f.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const visit = (n) => {
    // JSX 里的 {" "} 是保空格的写法，不是数据（等价性由 ts_ast_equiv.mjs 按 JSX 空白规则证明）
    if (ts.isStringLiteral(n) && n.parent && ts.isJsxExpression(n.parent) && !n.text.trim()) { return; }
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) || ts.isTemplateHead(n)
        || ts.isTemplateMiddle(n) || ts.isTemplateTail(n)) out.push(n.text);
    else if (ts.isNumericLiteral(n)) out.push(Number(n.text));
    ts.forEachChild(n, visit);
  };
  visit(sf);
}
process.stdout.write(JSON.stringify(out));
