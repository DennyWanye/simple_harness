// 代码质量整治阶段 9：前端"跨文件纯移动"核对（阶段 9 裁决执行条件 2）。
// 用法：node scripts/code_quality/ts_move_check.mjs [ref]   （在仓库根目录运行，默认 ref=HEAD）
// 取 ref 与工作区里改动、新增、删除的 tauri-app/src 下 .ts/.tsx 文件，收集顶层定义
// （函数、类、变量、接口、类型别名、枚举），按"名字 + 种类"配对，去掉 export 修饰后按语法树比较（与排版、注释无关）。
// 报告：内容变了的定义、消失的定义、新增的定义。纯移动时三类都应为空（导入语句不算定义，不比较）。
import { execFileSync } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(new URL("../../tauri-app/package.json", import.meta.url));
const ts = require("typescript");
const ref = process.argv[2] || "HEAD";
const git = (args) => execFileSync("git", args, { encoding: "utf8" });
const changed = new Set(
  [
    ...git(["diff", "--name-only", ref, "--", "tauri-app/src"]).split("\n"),
    ...git(["ls-files", "--others", "--exclude-standard", "--", "tauri-app/src"]).split("\n"),
  ].filter((f) => /\.(ts|tsx)$/.test(f)),
);
// 定义按语法树序列化后比较（节点种类 + 标识符与字面量文本，每项一行）：与排版、注释、引号风格无关；
// 去掉 export / default 修饰（搬到新模块要加 export）。JSX 文本按行去首尾空白后比较。
function serialize(node, sf) {
  const items = [];
  const walk = (n) => {
    if (n.kind === ts.SyntaxKind.ExportKeyword || n.kind === ts.SyntaxKind.DefaultKeyword) return;
    const kind = ts.SyntaxKind[n.kind];
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) || ts.isTemplateHead(n) ||
        ts.isTemplateMiddle(n) || ts.isTemplateTail(n)) { items.push(`${kind} ${JSON.stringify(n.text)}`); return; }
    if (ts.isJsxText(n)) {
      const t = n.text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean).join(" ");
      if (t) items.push(`${kind} ${JSON.stringify(t)}`);
      return;
    }
    const kids = [];
    n.forEachChild((c) => { kids.push(c); });
    const textual = ts.isIdentifier(n) || ts.isPrivateIdentifier(n) || ts.isNumericLiteral(n) ||
      ts.isBigIntLiteral(n) || ts.isRegularExpressionLiteral(n);
    if (kids.length === 0) items.push(textual ? `${kind} ${n.getText(sf)}` : kind);
    else { items.push(kind); kids.forEach(walk); }
  };
  walk(node);
  return items.join("\n");
}

function defs(text, file) {
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true,
    file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const out = new Map();
  for (const st of sf.statements) {
    let names = [];
    let kind = ts.SyntaxKind[st.kind];
    if ((ts.isFunctionDeclaration(st) || ts.isClassDeclaration(st) || ts.isInterfaceDeclaration(st) ||
         ts.isTypeAliasDeclaration(st) || ts.isEnumDeclaration(st)) && st.name) names = [st.name.text];
    else if (ts.isVariableStatement(st))
      names = st.declarationList.declarations.filter((d) => ts.isIdentifier(d.name)).map((d) => d.name.text);
    else continue;
    const body = serialize(st, sf);
    for (const n of names) {
      const key = `${kind}:${n}`;
      if (out.has(key)) out.set(key, out.get(key) + "\n" + body);  // 同名重载
      else out.set(key, body);
    }
  }
  return out;
}

const before = new Map();
const after = new Map();
const where = new Map();
for (const f of changed) {
  let old = null;
  try {
    old = execFileSync("git", ["show", `${ref}:${f}`], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
  } catch { old = null; }
  if (old !== null) for (const [k, v] of defs(old, f)) before.set(k, (before.get(k) ?? "") + v);
  if (existsSync(f)) for (const [k, v] of defs(readFileSync(f, "utf8"), f)) {
    after.set(k, (after.get(k) ?? "") + v);
    where.set(k, f);
  }
}
const diff = [], gone = [], added = [];
for (const [k, v] of before) {
  if (!after.has(k)) gone.push(k);
  else if (after.get(k) !== v) diff.push(`${k}（现在在 ${where.get(k)}）`);
}
for (const k of after.keys()) if (!before.has(k)) added.push(`${k}（${where.get(k)}）`);
console.log(`核对文件 ${changed.size} 个；定义：原 ${before.size}，现 ${after.size}`);
for (const k of diff) console.log("内容变了：" + k);
for (const k of gone) console.log("消失：" + k);
for (const k of added) console.log("新增：" + k);
const ok = diff.length === 0 && gone.length === 0 && added.length === 0;
console.log(ok ? "纯移动核对：通过" : "纯移动核对：不通过");
process.exit(ok ? 0 : 1);
