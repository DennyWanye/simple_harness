// 代码质量整治阶段 9（9I）：内联样式的静态求值器（ts_inline_to_class.mjs 用）。
// 只认可证明与运行时无关的写法：字面量、模板、对象与数组字面量（含展开）、模块级 const、
// src 里的模块级纯函数（参数为静态值时解释执行）、主题令牌。其余一律抛 NotStatic。
// 令牌来源：tokens.ts 里 space / radius / text / weight / font / controlHeight 的叶子包成 Tok，
// 原样传递时保留来源；参与运算、拼进模板即变成普通值（裁决条件 6：按语法树来源，不按数值反查）。
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(new URL("../../tauri-app/package.json", import.meta.url));
const ts = require("typescript");

export class NotStatic extends Error {}
export class Tok {
  constructor(value, varName) {
    this.value = value;
    this.varName = varName;
  }
}
export const un = (x) => (x instanceof Tok ? x.value : x);

class Func {
  constructor(node, env, mod) {
    this.node = node;
    this.env = env;
    this.mod = mod;
  }
}

const TOKENS_FILE = path.join("src", "theme", "tokens.ts");
const TAGGED = {
  space: (k) => `--sh-space-${k}`,
  radius: (k) => `--sh-radius-${k}`,
  weight: (k) => `--sh-weight-${k}`,
  font: (k) => (k === "ui" || k === "mono" ? `--sh-font-${k}` : null),
  text: (k, sub) => (sub === "size" || sub === "lh" ? `--sh-text-${k}-${sub}` : null),
};

class Env {
  constructor(parent) {
    this.parent = parent;
    this.vars = new Map();
  }
  lookup(name) {
    for (let e = this; e; e = e.parent) if (e.vars.has(name)) return { found: true, value: e.vars.get(name) };
    return { found: false };
  }
}

const RETURN = Symbol("return");
const BREAK = Symbol("break");

export class Evaluator {
  constructor(appRoot) {
    this.appRoot = appRoot;
    this.modules = new Map();
  }

  module(file) {
    const abs = path.resolve(file);
    let m = this.modules.get(abs);
    if (m) return m;
    const sf = ts.createSourceFile(abs, readFileSync(abs, "utf8"), ts.ScriptTarget.Latest, true,
      abs.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
    m = { file: abs, sf, decls: new Map(), values: new Map(), pending: new Set(), exports: new Map() };
    for (const st of sf.statements) {
      const exported = st.modifiers?.some((x) => x.kind === ts.SyntaxKind.ExportKeyword);
      if (ts.isImportDeclaration(st)) {
        const c = st.importClause;
        if (!c || c.isTypeOnly) continue;
        const spec = st.moduleSpecifier.text;
        if (c.name) m.decls.set(c.name.text, { kind: "import", spec, name: "default" });
        if (c.namedBindings && ts.isNamedImports(c.namedBindings))
          for (const e of c.namedBindings.elements)
            if (!e.isTypeOnly) m.decls.set(e.name.text, { kind: "import", spec, name: (e.propertyName ?? e.name).text });
        if (c.namedBindings && ts.isNamespaceImport(c.namedBindings))
          m.decls.set(c.namedBindings.name.text, { kind: "namespace", spec });
      } else if (ts.isVariableStatement(st)) {
        const isConst = (st.declarationList.flags & ts.NodeFlags.Const) !== 0;
        for (const d of st.declarationList.declarations) {
          const names = [];
          collectBindingNames(d.name, names);
          for (const n of names) {
            m.decls.set(n, { kind: isConst ? "const" : "mutable", decl: d });
            if (exported) m.exports.set(n, n);
          }
        }
      } else if (ts.isFunctionDeclaration(st) && st.name) {
        m.decls.set(st.name.text, { kind: "func", node: st });
        if (exported) m.exports.set(st.name.text, st.name.text);
      } else if (ts.isExportDeclaration(st) && st.exportClause && ts.isNamedExports(st.exportClause)) {
        for (const e of st.exportClause.elements) {
          const local = (e.propertyName ?? e.name).text;
          if (st.moduleSpecifier) m.exports.set(e.name.text, { reexport: st.moduleSpecifier.text, name: local });
          else m.exports.set(e.name.text, local);
        }
      }
    }
    this.modules.set(abs, m);
    return m;
  }

  resolveSpec(fromFile, spec) {
    if (!spec.startsWith(".")) throw new NotStatic(`外部模块 ${spec}`);
    const base = path.resolve(path.dirname(fromFile), spec);
    for (const cand of [base, `${base}.ts`, `${base}.tsx`, path.join(base, "index.ts"), path.join(base, "index.tsx")])
      if (existsSync(cand) && !cand.endsWith(".css") && /\.tsx?$/.test(cand)) return cand;
    throw new NotStatic(`找不到模块 ${spec}`);
  }

  exportOf(file, name) {
    const m = this.module(file);
    const e = m.exports.get(name);
    if (e === undefined) throw new NotStatic(`${path.basename(file)} 没有导出 ${name}`);
    if (typeof e === "string") return this.topValue(m, e);
    return this.exportOf(this.resolveSpec(file, e.reexport), e.name);
  }

  /** 模块顶层名字的值（const 惰性求值并缓存；函数返回闭包；导入跟到源模块）。 */
  topValue(m, name) {
    if (m.values.has(name)) return m.values.get(name);
    const d = m.decls.get(name);
    if (!d) throw new NotStatic(`未知名字 ${name}`);
    let value;
    if (d.kind === "import") value = this.exportOf(this.resolveSpec(m.file, d.spec), d.name);
    else if (d.kind === "namespace") throw new NotStatic(`命名空间导入 ${name}`);
    else if (d.kind === "func") value = new Func(d.node, new Env(null), m);
    else if (d.kind === "mutable") throw new NotStatic(`可变模块变量 ${name}`);
    else {
      if (m.pending.has(name)) throw new NotStatic(`循环依赖 ${name}`);
      m.pending.add(name);
      try {
        const env = new Env(null);
        if (!d.decl.initializer) throw new NotStatic(`无初值 ${name}`);
        const init = this.eval(d.decl.initializer, env, m);
        bindPattern(this, d.decl.name, init, env, m);
        for (const [k, v] of env.vars) if (!m.values.has(k)) m.values.set(k, k === name ? v : v);
        value = env.vars.get(name);
      } finally {
        m.pending.delete(name);
      }
      if (path.relative(this.appRoot, m.file) === TOKENS_FILE) value = tagTokens(name, value);
    }
    m.values.set(name, value);
    return value;
  }

  lookup(name, env, m) {
    const r = env.lookup(name);
    if (r.found) return r.value;
    if (name === "undefined") return undefined;
    if (m.decls.has(name)) return this.topValue(m, name);
    throw new NotStatic(`未知名字 ${name}`);
  }

  eval(n, env, m) {
    const K = ts.SyntaxKind;
    switch (n.kind) {
      case K.StringLiteral:
      case K.NoSubstitutionTemplateLiteral:
        return n.text;
      case K.NumericLiteral:
        return Number(n.text);
      case K.TrueKeyword:
        return true;
      case K.FalseKeyword:
        return false;
      case K.NullKeyword:
        return null;
      case K.Identifier:
        return this.lookup(n.text, env, m);
      case K.ParenthesizedExpression:
      case K.AsExpression:
      case K.SatisfiesExpression:
      case K.NonNullExpression:
      case K.TypeAssertionExpression:
        return this.eval(n.expression, env, m);
      case K.TemplateExpression: {
        let s = n.head.text;
        for (const span of n.templateSpans) s += str(un(this.eval(span.expression, env, m))) + span.literal.text;
        return s;
      }
      case K.ObjectLiteralExpression: {
        const o = {};
        for (const p of n.properties) {
          if (ts.isPropertyAssignment(p)) o[this.propName(p.name, env, m)] = this.eval(p.initializer, env, m);
          else if (ts.isShorthandPropertyAssignment(p)) o[p.name.text] = this.lookup(p.name.text, env, m);
          else if (ts.isSpreadAssignment(p)) {
            const v = un(this.eval(p.expression, env, m));
            if (v == null || v === false) continue;
            if (typeof v !== "object") throw new NotStatic("展开非对象");
            Object.assign(o, v);
          } else throw new NotStatic("对象里的方法或访问器");
        }
        return o;
      }
      case K.ArrayLiteralExpression: {
        const a = [];
        for (const e of n.elements) {
          if (ts.isSpreadElement(e)) a.push(...un(this.eval(e.expression, env, m)));
          else a.push(this.eval(e, env, m));
        }
        return a;
      }
      case K.PropertyAccessExpression: {
        const o = un(this.eval(n.expression, env, m));
        if (n.questionDotToken && o == null) return undefined;
        return member(o, n.name.text);
      }
      case K.ElementAccessExpression: {
        const o = un(this.eval(n.expression, env, m));
        return member(o, un(this.eval(n.argumentExpression, env, m)));
      }
      case K.PrefixUnaryExpression: {
        const v = un(this.eval(n.operand, env, m));
        if (n.operator === K.MinusToken) return -num(v);
        if (n.operator === K.PlusToken) return +num(v);
        if (n.operator === K.ExclamationToken) return !v;
        throw new NotStatic("一元运算");
      }
      case K.BinaryExpression:
        return this.binary(n, env, m);
      case K.ConditionalExpression:
        return un(this.eval(n.condition, env, m)) ? this.eval(n.whenTrue, env, m) : this.eval(n.whenFalse, env, m);
      case K.ArrowFunction:
      case K.FunctionExpression:
        return new Func(n, env, m);
      case K.CallExpression:
        return this.call(n, env, m);
      default:
        throw new NotStatic(`不支持的写法 ${K[n.kind]}`);
    }
  }

  propName(name, env, m) {
    if (ts.isIdentifier(name) || ts.isStringLiteral(name) || ts.isNumericLiteral(name)) return name.text;
    if (ts.isComputedPropertyName(name)) return str(un(this.eval(name.expression, env, m)));
    throw new NotStatic("属性名");
  }

  binary(n, env, m) {
    const K = ts.SyntaxKind;
    const op = n.operatorToken.kind;
    if (op === K.AmpersandAmpersandToken) { const l = this.eval(n.left, env, m); return un(l) ? this.eval(n.right, env, m) : l; }
    if (op === K.BarBarToken) { const l = this.eval(n.left, env, m); return un(l) ? l : this.eval(n.right, env, m); }
    if (op === K.QuestionQuestionToken) { const l = this.eval(n.left, env, m); return un(l) ?? this.eval(n.right, env, m); }
    const a = un(this.eval(n.left, env, m));
    const b = un(this.eval(n.right, env, m));
    const prim = (x) => { if (x !== null && typeof x === "object") throw new NotStatic("对象参与运算"); return x; };
    prim(a); prim(b);
    switch (op) {
      case K.PlusToken: return typeof a === "string" || typeof b === "string" ? str(a) + str(b) : num(a) + num(b);
      case K.MinusToken: return num(a) - num(b);
      case K.AsteriskToken: return num(a) * num(b);
      case K.SlashToken: return num(a) / num(b);
      case K.PercentToken: return num(a) % num(b);
      case K.EqualsEqualsEqualsToken: return a === b;
      case K.ExclamationEqualsEqualsToken: return a !== b;
      case K.LessThanToken: return num(a) < num(b);
      case K.GreaterThanToken: return num(a) > num(b);
      case K.LessThanEqualsToken: return num(a) <= num(b);
      case K.GreaterThanEqualsToken: return num(a) >= num(b);
      default: throw new NotStatic(`运算符 ${K[op]}`);
    }
  }

  call(n, env, m) {
    if (n.questionDotToken) throw new NotStatic("可选调用");
    const args = n.arguments.map((a) => {
      if (ts.isSpreadElement(a)) throw new NotStatic("展开参数");
      return this.eval(a, env, m);
    });
    if (ts.isPropertyAccessExpression(n.expression)) {
      const target = un(this.eval(n.expression.expression, env, m));
      const meth = n.expression.name.text;
      if (Array.isArray(target) && meth === "join") return target.map((x) => str(un(x))).join(args.length ? str(un(args[0])) : ",");
      if (typeof target === "string" && (meth === "trim" || meth === "toUpperCase" || meth === "toLowerCase")) return target[meth]();
      throw new NotStatic(`方法调用 ${meth}`);
    }
    if (ts.isIdentifier(n.expression) && n.expression.text === "String" && !env.lookup("String").found && !m.decls.has("String"))
      return str(un(args[0]));
    const f = this.eval(n.expression, env, m);
    if (!(f instanceof Func)) throw new NotStatic("调用非函数");
    return this.invoke(f, args);
  }

  invoke(f, args) {
    const node = f.node;
    if (node.asteriskToken || node.modifiers?.some((x) => x.kind === ts.SyntaxKind.AsyncKeyword)) throw new NotStatic("异步或生成器函数");
    const env = new Env(f.env);
    node.parameters.forEach((p, i) => {
      if (p.dotDotDotToken) throw new NotStatic("剩余参数");
      let v = args[i];
      if (un(v) === undefined && p.initializer) v = this.eval(p.initializer, env, f.mod);
      bindPattern(this, p.name, v, env, f.mod);
    });
    if (!ts.isBlock(node.body)) return this.eval(node.body, env, f.mod);
    const r = this.exec(node.body.statements, env, f.mod);
    return r && r.kind === RETURN ? r.value : undefined;
  }

  exec(stmts, env, m) {
    for (const st of stmts) {
      const r = this.stmt(st, env, m);
      if (r) return r;
    }
    return null;
  }

  stmt(st, env, m) {
    const K = ts.SyntaxKind;
    switch (st.kind) {
      case K.VariableStatement:
        for (const d of st.declarationList.declarations) {
          if (!d.initializer) throw new NotStatic("无初值的局部变量");
          bindPattern(this, d.name, this.eval(d.initializer, env, m), env, m);
        }
        return null;
      case K.ReturnStatement:
        return { kind: RETURN, value: st.expression ? this.eval(st.expression, env, m) : undefined };
      case K.IfStatement:
        if (un(this.eval(st.expression, env, m))) return this.stmt(st.thenStatement, new Env(env), m);
        return st.elseStatement ? this.stmt(st.elseStatement, new Env(env), m) : null;
      case K.Block:
        return this.exec(st.statements, new Env(env), m);
      case K.SwitchStatement: {
        const v = un(this.eval(st.expression, env, m));
        const clauses = st.caseBlock.clauses;
        let start = clauses.findIndex((c) => ts.isCaseClause(c) && un(this.eval(c.expression, env, m)) === v);
        if (start < 0) start = clauses.findIndex((c) => ts.isDefaultClause(c));
        if (start < 0) return null;
        const inner = new Env(env);
        for (let i = start; i < clauses.length; i++) {
          const r = this.exec(clauses[i].statements, inner, m);
          if (r && r.kind === BREAK) return null;
          if (r) return r;
        }
        return null;
      }
      case K.BreakStatement:
        if (st.label) throw new NotStatic("带标签的 break");
        return { kind: BREAK };
      case K.EmptyStatement:
        return null;
      default:
        throw new NotStatic(`不支持的语句 ${K[st.kind]}`);
    }
  }
}

function collectBindingNames(name, out) {
  if (ts.isIdentifier(name)) out.push(name.text);
  else for (const e of name.elements) if (!ts.isOmittedExpression(e)) collectBindingNames(e.name, out);
}

function bindPattern(ev, name, value, env, m) {
  if (ts.isIdentifier(name)) { env.vars.set(name.text, value); return; }
  const v = un(value);
  if (ts.isObjectBindingPattern(name)) {
    if (v == null || typeof v !== "object") throw new NotStatic("解构非对象");
    for (const e of name.elements) {
      if (e.dotDotDotToken) throw new NotStatic("剩余解构");
      const key = e.propertyName ? ev.propName(e.propertyName, env, m) : e.name.text;
      let x = v[key];
      if (un(x) === undefined && e.initializer) x = ev.eval(e.initializer, env, m);
      bindPattern(ev, e.name, x, env, m);
    }
    return;
  }
  if (!Array.isArray(v)) throw new NotStatic("解构非数组");
  name.elements.forEach((e, i) => {
    if (ts.isOmittedExpression(e)) return;
    if (e.dotDotDotToken) throw new NotStatic("剩余解构");
    let x = v[i];
    if (un(x) === undefined && e.initializer) x = ev.eval(e.initializer, env, m);
    bindPattern(ev, e.name, x, env, m);
  });
}

function member(o, key) {
  if (o == null) throw new NotStatic("读空值的属性");
  if (Array.isArray(o) && key === "length") return o.length;
  if (typeof o === "string" && key === "length") return o.length;
  if (typeof o !== "object") throw new NotStatic("读非对象的属性");
  if (!Object.prototype.hasOwnProperty.call(o, key)) {
    if (Array.isArray(o)) throw new NotStatic("数组越界或方法");
    return undefined;
  }
  return o[key];
}

function num(x) {
  if (typeof x !== "number") throw new NotStatic("非数字运算");
  return x;
}

function str(x) {
  if (x === undefined || x === null || typeof x === "object" || typeof x === "function") throw new NotStatic("拼接非基本值");
  return String(x);
}

function tagTokens(name, value) {
  const rule = TAGGED[name];
  if (name === "controlHeight") return new Tok(value, "--sh-control-height");
  if (!rule || value == null || typeof value !== "object") return value;
  const out = {};
  for (const [k, v] of Object.entries(value)) {
    if (name === "text" && v && typeof v === "object") {
      const inner = {};
      for (const [sub, x] of Object.entries(v)) { const vn = rule(k, sub); inner[sub] = vn ? new Tok(x, vn) : x; }
      out[k] = inner;
    } else {
      const vn = rule(k);
      out[k] = vn && (typeof v === "number" || typeof v === "string") ? new Tok(v, vn) : v;
    }
  }
  return out;
}
