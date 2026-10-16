// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useState } from "react";

/**
 * 渲染期判断这组依赖与上一次渲染是否不同（逐项 Object.is），用于"换了对象就重置状态"：
 *   if (useDepsChanged([sessionId])) setDraft("");
 * 这是 React 文档推荐的"渲染期调整状态"写法，代替在 effect 里同步 setState。
 * 首次渲染返回 false。
 */
export function useDepsChanged(deps: readonly unknown[]): boolean {
  const [previous, setPrevious] = useState(deps);
  const changed =
    previous.length !== deps.length ||
    deps.some((value, index) => !Object.is(value, previous[index]));
  if (changed) setPrevious(deps);
  return changed;
}
