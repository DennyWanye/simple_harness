// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * ArtifactsView stub（T6 交付合同；T12 填充实现，不回头改 App.tsx）。
 *
 * 合同：无 App props（invoke 自足 — D4：数据源为组 R T5b 新增的
 * Rust `list_artifacts()` command，打开/显示文件夹复用 artifact_ops）。
 */
import React from "react";

import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";

export const ArtifactsView: React.FC = () => {
  return (
    <section
      data-testid="view-artifacts"
      aria-label="产物库"
      style={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: dark.textMuted,
        fontFamily: tokens.font.ui,
        fontSize: tokens.text.base.size,
      }}
    >
      产物库视图（T12 实装）
    </section>
  );
};

export default ArtifactsView;
