// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 画廊用的假 Tauri 内核：组件里的 invoke(...) 在浏览器里没有原生层，这里按命令名返回固定夹具值。
// 必须在任何组件模块求值前导入（scenes.tsx 的第一个导入）。未登记的命令一律返回 null。
type Answer = unknown | ((args: Record<string, unknown> | undefined) => unknown);
const answers = new Map<string, Answer>();
let callbackId = 0;

/** 登记某个 invoke 命令的固定返回值（或按参数返回的函数）。 */
export function setTauriAnswer(cmd: string, answer: Answer) {
  answers.set(cmd, answer);
}

const internals = {
  invoke(cmd: string, args?: Record<string, unknown>) {
    const a = answers.get(cmd);
    const value = typeof a === "function" ? (a as (x: typeof args) => unknown)(args) : (a ?? null);
    return Promise.resolve(value);
  },
  transformCallback() {
    callbackId += 1;
    return callbackId;
  },
  unregisterCallback() {},
  convertFileSrc(path: string) {
    return path;
  },
  metadata: { currentWindow: { label: "main" }, currentWebview: { windowLabel: "main", label: "main" } },
};
(window as unknown as { __TAURI_INTERNALS__: typeof internals }).__TAURI_INTERNALS__ = internals;
export {};
