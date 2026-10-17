// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 测试读样式用：元素的有效声明 = 生成类的声明 + 内联样式。
 *
 * 9I 把静态内联样式换成了生成的 CSS 类（各组件旁的 `*.styles.css`）。
 * vitest 不加载 CSS，元素的 `.style` 里不再有这些声明；断言改读
 * `styleOf(el).x`，生成类与内联样式都算，所以"某属性为空"的断言
 * 在声明挪进类之后仍然能失败。令牌变量换回 tokens.ts 的值，再交给
 * jsdom 的 CSSStyleDeclaration 规整（与原先读 `.style` 的规整相同）。
 */
import { TOKEN_CSS_VARS } from "../theme/tokens";

const sheets = import.meta.glob("../**/*.styles.css", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const RULES = new Map<string, string>();
for (const css of Object.values(sheets)) {
  for (const m of css.matchAll(/\.(sh-[A-Za-z0-9]+-\d+)\s*\{([^}]*)\}/g)) {
    RULES.set(
      m[1],
      m[2].replace(/var\((--sh-[a-z0-9-]+)\)/g, (all, name: string) => TOKEN_CSS_VARS[name] ?? all),
    );
  }
}

export function styleOf(el: Element): CSSStyleDeclaration {
  const probe = document.createElement("div");
  const fromClasses = [...el.classList].flatMap((c) => (RULES.has(c) ? [RULES.get(c)!] : []));
  probe.style.cssText = [...fromClasses, (el as HTMLElement).style?.cssText ?? ""].join(";");
  return probe.style;
}
