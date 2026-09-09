// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 主题变量注入 —— 把 tokens.ts 的 `THEME_CSS` 写进 `<style id="sh-theme">`。
 *
 * 之所以由 TS 注入而不是写死在 index.css：令牌只有一个真相源
 * （tokens.ts 的 `palette`），CSS 变量表由它生成，避免两处漂移。
 * 深浅两套是同一组变量的镜像；`:root` 是浅色基线，系统深色与显式
 * `data-theme="dark"` 覆盖，显式 `data-theme="light"` 永远赢。
 */
import { palette, THEME_CSS } from "./tokens";

export const THEME_STYLE_ID = "sh-theme";

/** 当前生效主题（只读系统偏好与显式覆盖，不做持久化）。 */
export function resolvedTheme(): "light" | "dark" {
  const explicit = document.documentElement.getAttribute("data-theme");
  if (explicit === "light" || explicit === "dark") return explicit;
  return typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

/**
 * 注入令牌变量并把窗体底色刷成当前主题的底 —— 冷启动不闪白/闪黑。
 * 幂等：重复调用只更新同一个 style 节点。
 */
export function applyTheme(): void {
  if (typeof document === "undefined") return;
  let node = document.getElementById(THEME_STYLE_ID);
  if (!node) {
    node = document.createElement("style");
    node.id = THEME_STYLE_ID;
    document.head.prepend(node);
  }
  node.textContent = THEME_CSS;
  paintShellBackground();
}

/** 把 html/body 背景刷成当前主题的底色（React 挂载前就要正确）。 */
export function paintShellBackground(): void {
  if (typeof document === "undefined") return;
  const base = palette[resolvedTheme()].bg;
  document.documentElement.style.backgroundColor = base;
  document.body.style.backgroundColor = base;
}

/** 跟随系统深浅切换重刷底色（变量本身由 CSS media query 负责）。 */
export function watchSystemTheme(): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return () => undefined;
  }
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const onChange = () => paintShellBackground();
  query.addEventListener?.("change", onChange);
  return () => query.removeEventListener?.("change", onChange);
}
