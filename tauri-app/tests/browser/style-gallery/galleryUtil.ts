// 画廊场景的小工具（非组件模块）。
import "./tauriStub";
import { useState } from "react";

/** 场景首次渲染前只执行一次（用来给状态库灌固定夹具数据）。每次页面只挂载一个场景，不会互相串。 */
export function useOnce(fn: () => void) {
  useState(() => {
    fn();
    return true;
  });
}

export const noop = () => undefined;
export const noopAsync = async () => undefined;
