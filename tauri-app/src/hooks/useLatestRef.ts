// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useLayoutEffect, useRef } from "react";

/**
 * 保存某个值最近一次提交的版本，供订阅回调、异步回调、卸载清理读取。
 * 在布局阶段同步，不在渲染期写 ref：这些回调都在提交之后触发，读到的就是屏幕上那一版。
 */
export function useLatestRef<T>(value: T) {
  const ref = useRef(value);
  useLayoutEffect(() => {
    ref.current = value;
  });
  return ref;
}
