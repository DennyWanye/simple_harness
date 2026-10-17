// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 基础小组件场景。
import { RuntimeBackendBanner } from "../../../src/components/RuntimeBackendBanner";
import { noop } from "./galleryUtil";

export function RuntimeBannerScene() {
  return <RuntimeBackendBanner message="后台意外退出（夹具文本）" onRetry={noop} onOpenLogDir={noop} onDismiss={noop} />;
}
