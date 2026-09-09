// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
} from "react";

import type { IncomingMessage } from "../types/messages";
import {
  buildCapabilityMutationMessage,
  buildCapabilityOperationActionMessage,
  isCapabilityListResponse,
  isCapabilityOperationEventMessage,
  isCapabilityOperationsResponse,
  reduceCapabilityOperationEvent,
  type CapabilityAuthorizationMode,
  type CapabilityCategory,
  type CapabilityDescriptor,
  type CapabilityOperation,
  type CapabilityOperationAction,
} from "../types/capabilities";
import type { ControlChannel } from "../ws/ControlChannel";
import {
  INTERACTIVE_CLASS,
  dark,
  emptyState as emptyStateStyle,
  metaText,
  titleText,
  transition,
} from "../theme/components";
import { tokens } from "../theme/tokens";
import { CapabilityOperationCard } from "./CapabilityOperationCard";

type CapabilityChannel = Pick<ControlChannel, "send" | "onMessage">;
type Tab = "capabilities" | "operations";
type CategoryFilter = "all" | CapabilityCategory;
const EMPTY_CAPABILITIES: CapabilityDescriptor[] = [];
const EMPTY_OPERATIONS: CapabilityOperation[] = [];

function capabilityMatchesFilters(
  capability: CapabilityDescriptor,
  category: CategoryFilter,
  query: string,
): boolean {
  if (category !== "all" && !capability.categories.includes(category)) {
    return false;
  }
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  return [
    capability.name,
    capability.description,
    capability.capability_id,
    capability.source.label,
  ]
    .filter(Boolean)
    .some((value) => String(value).toLocaleLowerCase().includes(needle));
}

interface Props {
  open: boolean;
  channel: CapabilityChannel | null;
  sessionId?: string | null;
  /**
   * 渲染形态（D5 宿主模式）：
   * - "overlay"（默认）：既有浮层 —— fixed backdrop + 居中 dialog；
   * - "page"：工作台视图内嵌 —— 去 backdrop/fixed，填满内容区
   *   （SkillsView 宿主，WB-6）。page 模式无关闭钮，onClose 可省略。
   */
  variant?: "overlay" | "page";
  onClose?: () => void;
  onOpenLegacySkillStore: () => void;
  initialCapabilities?: CapabilityDescriptor[];
  initialOperations?: CapabilityOperation[];
}

const CATEGORY_LABEL: Record<CapabilityCategory, string> = {
  instruction: "说明",
  tool: "工具",
  mcp: "MCP",
  pack: "能力包",
};

const HEALTH_LABEL: Record<CapabilityDescriptor["health"], string> = {
  healthy: "健康",
  degraded: "降级",
  unavailable: "不可用",
  validating: "验证中",
  unknown: "待确认",
};

/** 状态色只用于状态，不当装饰色。 */
const HEALTH_COLOR: Record<CapabilityDescriptor["health"], string> = {
  healthy: dark.success,
  degraded: dark.warning,
  unavailable: dark.danger,
  validating: dark.info,
  unknown: dark.textFaint,
};

function readCachedAuthorizationMode(): CapabilityAuthorizationMode {
  try {
    const cached = localStorage.getItem("deskpet.auto_mode");
    return cached === null || cached === "true" ? "auto" : "manual";
  } catch {
    return "auto";
  }
}

export function CapabilityCenterPanel({
  open,
  channel,
  sessionId = null,
  variant = "overlay",
  onClose,
  onOpenLegacySkillStore,
  initialCapabilities = EMPTY_CAPABILITIES,
  initialOperations = EMPTY_OPERATIONS,
}: Props) {
  const [tab, setTab] = useState<Tab>("capabilities");
  const [capabilities, setCapabilities] =
    useState<CapabilityDescriptor[]>(initialCapabilities);
  const [operations, setOperations] =
    useState<CapabilityOperation[]>(initialOperations);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<CategoryFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(
    initialCapabilities[0]?.capability_id ?? null,
  );
  const [authorizationMode, setAuthorizationMode] =
    useState<CapabilityAuthorizationMode>(readCachedAuthorizationMode);
  const [notice, setNotice] = useState<string | null>(null);

  // One initial snapshot request + the existing push stream. There is no
  // interval, background polling loop, or second job/progress protocol.
  useEffect(() => {
    if (!open || !channel) return undefined;
    channel.send({
      type: "capability_list",
      payload: { session_id: sessionId || null },
    });
    channel.send({ type: "capability_operations_list", payload: {} });
    channel.send({ type: "permission_auto_mode_get", payload: {} });

    const off = channel.onMessage((message: IncomingMessage) => {
      // Capability wire messages are being added compatibly and may arrive
      // before older IncomingMessage unions know their literals.
      const capabilityMessage: unknown = message;
      if (isCapabilityListResponse(capabilityMessage)) {
        setCapabilities(capabilityMessage.payload.capabilities);
        setSelectedId((current) =>
          current &&
          capabilityMessage.payload.capabilities.some(
            (item) => item.capability_id === current,
          )
            ? current
            : capabilityMessage.payload.capabilities[0]?.capability_id ?? null,
        );
        return;
      }
      if (isCapabilityOperationsResponse(capabilityMessage)) {
        setOperations(capabilityMessage.payload.operations);
        return;
      }
      if (isCapabilityOperationEventMessage(capabilityMessage)) {
        setOperations((current) =>
          reduceCapabilityOperationEvent(current, capabilityMessage.payload),
        );
        return;
      }
      if (message.type === "permission_auto_mode_response") {
        const mode = message.payload.enabled ? "auto" : "manual";
        setAuthorizationMode(mode);
        try {
          localStorage.setItem(
            "deskpet.auto_mode",
            String(message.payload.enabled),
          );
        } catch {
          // The backend response remains authoritative if storage is disabled.
        }
      }
    });
    return off;
  }, [open, channel, sessionId]);

  const visibleCapabilities = useMemo(() => {
    return capabilities.filter((capability) =>
      capabilityMatchesFilters(capability, category, query),
    );
  }, [capabilities, category, query]);

  // Filtering must never leave the detail pane pointing at an item that is no
  // longer present in the visible list. Persist the fallback selection so the
  // list's aria state and the detail pane share one source of truth even when
  // the capability snapshot arrives asynchronously after the filter changes.
  useEffect(() => {
    setSelectedId((current) =>
      current &&
      visibleCapabilities.some((item) => item.capability_id === current)
        ? current
        : visibleCapabilities[0]?.capability_id ?? null,
    );
  }, [visibleCapabilities]);

  const selected =
    visibleCapabilities.find((item) => item.capability_id === selectedId) ?? null;

  const sendOperationAction = (
    action: CapabilityOperationAction,
    operation: CapabilityOperation,
  ) => {
    if (!channel) {
      setNotice("控制通道未连接，暂时不能执行操作。");
      return;
    }
    channel.send(buildCapabilityOperationActionMessage(action, operation));
    setNotice(`已提交${action === "cancel" ? "取消" : action === "retry" ? "重试" : action === "rollback" ? "回滚" : "卸载"}请求。`);
  };

  const sendCapabilityMutation = (
    action: "install" | "activate" | "repair" | "uninstall",
    capability: CapabilityDescriptor,
  ) => {
    if (!channel) {
      setNotice("控制通道未连接，暂时不能执行操作。");
      return;
    }
    channel.send(buildCapabilityMutationMessage(action, capability.capability_id));
    setNotice(
      authorizationMode === "auto"
        ? "已按 Auto 模式直接提交；操作卡会保留授权审计状态。"
        : "请求已提交；若需要敏感权限，Simple Harness 会按 Manual 模式确认。",
    );
  };

  if (!open) return null;

  const isPage = variant === "page";

  const panel = (
      <section
        role={isPage ? "region" : "dialog"}
        aria-modal={isPage ? undefined : "true"}
        aria-label="能力中心"
        style={isPage ? pagePanelStyle : panelStyle}
      >
        <header style={headerStyle}>
          <div>
            <h2 style={titleText}>能力中心</h2>
            <p style={subtitleStyle}>
              查看可用能力、运行健康与安装/生成进度
            </p>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              onClick={onOpenLegacySkillStore}
              className={INTERACTIVE_CLASS}
              style={secondaryButtonStyle}
            >
              打开旧 Skill Store
            </button>
            {!isPage && onClose ? (
              <button
                type="button"
                aria-label="关闭能力中心"
                onClick={onClose}
                style={iconButtonStyle}
              >
                ×
              </button>
            ) : null}
          </div>
        </header>

        <div style={statusBarStyle}>
          <span>
            当前授权：{" "}
            <strong>
              {authorizationMode === "auto"
                ? "Auto（直接执行）"
                : "Manual（按需确认）"}
            </strong>
          </span>
          <span>
            进度来源：工作流推送
          </span>
          {!channel ? <span style={{ color: dark.warning }}>离线快照</span> : null}
        </div>

        <nav aria-label="能力中心视图" style={tabsStyle}>
          <button
            type="button"
            aria-selected={tab === "capabilities"}
            onClick={() => setTab("capabilities")}
            className="bp-tab"
            style={tabButtonStyle(tab === "capabilities")}
          >
            能力 ({capabilities.length})
          </button>
          <button
            type="button"
            aria-selected={tab === "operations"}
            onClick={() => setTab("operations")}
            className="bp-tab"
            style={tabButtonStyle(tab === "operations")}
          >
            操作 ({operations.length})
          </button>
        </nav>

        {notice ? (
          <div role="status" style={noticeStyle}>
            <span>{notice}</span>
            <button
              type="button"
              aria-label="关闭提示"
              onClick={() => setNotice(null)}
              style={inlineDismissStyle}
            >
              ×
            </button>
          </div>
        ) : null}

        {tab === "capabilities" ? (
          <div style={bodyGridStyle}>
            <aside style={listPaneStyle}>
              <label style={fieldLabelStyle}>
                搜索
                <input
                  aria-label="搜索能力"
                  value={query}
                  onChange={(event) => {
                    const nextQuery = event.target.value;
                    setQuery(nextQuery);
                    setSelectedId(
                      capabilities.find((capability) =>
                        capabilityMatchesFilters(
                          capability,
                          category,
                          nextQuery,
                        ),
                      )?.capability_id ?? null,
                    );
                  }}
                  placeholder="名称、来源或 ID"
                  style={inputStyle}
                />
              </label>
              <label style={fieldLabelStyle}>
                类别
                <select
                  aria-label="能力类别"
                  value={category}
                  onChange={(event) => {
                    const nextCategory = event.target.value as CategoryFilter;
                    setCategory(nextCategory);
                    setSelectedId(
                      capabilities.find((capability) =>
                        capabilityMatchesFilters(
                          capability,
                          nextCategory,
                          query,
                        ),
                      )?.capability_id ?? null,
                    );
                  }}
                  style={inputStyle}
                >
                  <option value="all">全部</option>
                  <option value="instruction">说明</option>
                  <option value="tool">工具</option>
                  <option value="mcp">MCP</option>
                  <option value="pack">能力包</option>
                </select>
              </label>

              <div
                data-testid="capability-list"
                style={{
                  display: "grid",
                  // minmax(0,1fr)：隐式轨道默认以行内 nowrap 文本的
                  // min-content 为下限，min 尺寸(800×560)下会撑破列宽
                  // 出横向滚动条（WBUI-DEF-S03-01）
                  gridTemplateColumns: "minmax(0, 1fr)",
                  gap: 7,
                  overflowY: "auto",
                }}
              >
                {visibleCapabilities.map((capability) => (
                  <button
                    key={capability.capability_id}
                    type="button"
                    aria-pressed={selectedId === capability.capability_id}
                    onClick={() => setSelectedId(capability.capability_id)}
                    style={capabilityListItemStyle(
                      selectedId === capability.capability_id,
                    )}
                  >
                    <span style={{ minWidth: 0, textAlign: "left" }}>
                      <strong style={ellipsisStyle}>{capability.name}</strong>
                      <span style={listMetaStyle}>
                        {capability.version} · {capability.source.label}
                      </span>
                    </span>
                    <span
                      style={{
                        color: HEALTH_COLOR[capability.health],
                        fontSize: tokens.text.sm.size,
                      }}
                    >
                      {HEALTH_LABEL[capability.health]}
                    </span>
                  </button>
                ))}
                {visibleCapabilities.length === 0 ? (
                  <div style={emptyStyle}>没有符合筛选条件的能力。</div>
                ) : null}
              </div>
            </aside>

            <main style={detailPaneStyle}>
              {selected ? (
                <CapabilityDetail
                  capability={selected}
                  onMutation={sendCapabilityMutation}
                />
              ) : (
                <div style={emptyStyle}>选择一项能力查看详情。</div>
              )}
            </main>
          </div>
        ) : (
          <main
            data-testid="capability-operation-list"
            style={operationListStyle}
          >
            {operations.map((operation) => (
              <CapabilityOperationCard
                key={operation.operation_id}
                operation={operation}
                onAction={sendOperationAction}
              />
            ))}
            {operations.length === 0 ? (
              <div style={emptyStyle}>目前没有能力安装或生成操作。</div>
            ) : null}
          </main>
        )}
      </section>
  );

  if (isPage) return panel;

  return (
    <div
      role="presentation"
      style={backdropStyle}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose?.();
      }}
    >
      {panel}
    </div>
  );
}

function CapabilityDetail({
  capability,
  onMutation,
}: {
  capability: CapabilityDescriptor;
  onMutation: (
    action: "install" | "activate" | "repair" | "uninstall",
    capability: CapabilityDescriptor,
  ) => void;
}) {
  return (
    // 同 WBUI-DEF-S03-01：轨道钉死 minmax(0,1fr)，否则明细 dl 的
    // min-content(≈192px) 在 min 尺寸下顶破详情列，健康文案逐行硬裁
    <article
      data-testid="capability-detail"
      style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 14 }}
    >
      <header>
        <h3 style={{ margin: 0 }}>{capability.name}</h3>
        <p style={{ ...subtitleStyle, marginTop: 5 }}>
          {capability.description || "暂无说明"}
        </p>
      </header>
      <dl style={detailGridStyle}>
        <dt>类别</dt>
        <dd>
          {capability.categories.map((item) => CATEGORY_LABEL[item]).join("、")}
        </dd>
        <dt>版本</dt>
        <dd>{capability.version}</dd>
        <dt>来源</dt>
        <dd>{capability.source.label}</dd>
        <dt>Scope</dt>
        <dd>{capability.scope}</dd>
        <dt>健康</dt>
        <dd style={{ color: HEALTH_COLOR[capability.health] }}>
          {HEALTH_LABEL[capability.health]}
          {capability.health_summary ? ` · ${capability.health_summary}` : ""}
        </dd>
      </dl>
      {capability.manifest ? (
        <section
          data-testid="capability-manifest-detail"
          style={{
            display: "grid",
            gap: 10,
            padding: 12,
            borderRadius: 10,
            background: dark.inset,
            border: `1px solid ${dark.insetBorder}`,
          }}
        >
          <h4 style={{ margin: 0 }}>清单详情</h4>
          <dl style={detailGridStyle}>
            <dt>兼容 Simple Harness</dt>
            <dd>{capability.manifest.compatibility.deskpet}</dd>
            <dt>操作系统</dt>
            <dd>{capability.manifest.compatibility.os.join("、") || "未声明"}</dd>
            <dt>架构</dt>
            <dd>
              {capability.manifest.compatibility.architectures.join("、") ||
                "未声明"}
            </dd>
            <dt>Python</dt>
            <dd>{capability.manifest.compatibility.python}</dd>
            <dt>权限</dt>
            <dd>{capability.manifest.permissions.join("、") || "无"}</dd>
            <dt>Effect</dt>
            <dd>{capability.manifest.effects.join("、") || "无"}</dd>
          </dl>
          <div>
            <strong>Skills</strong>
            <div style={manifestValueStyle}>
              {capability.manifest.entries.skills
                .map((item) => item.path)
                .join("、") || "无"}
            </div>
          </div>
          <div>
            <strong>Tools</strong>
            {capability.manifest.entries.tools.length ? (
              <ul style={manifestListStyle}>
                {capability.manifest.entries.tools.map((tool) => (
                  <li key={`${tool.id}:${tool.provider_name}`}>
                    <code>{tool.provider_name}</code> · {tool.runtime} ·{" "}
                    {tool.execution_profile}
                    <div style={manifestValueStyle}>
                      入口 {tool.entry} · Schema {tool.schema} · 健康检查{" "}
                      {tool.healthcheck}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <div style={manifestValueStyle}>无</div>
            )}
          </div>
          <div>
            <strong>MCP Servers</strong>
            <div style={manifestValueStyle}>
              {capability.manifest.entries.mcp_servers
                .map((item) => `${item.id} (${item.config_ref})`)
                .join("、") || "无"}
            </div>
          </div>
          <div>
            <strong>依赖</strong>
            <div style={manifestValueStyle}>
              Python：
              {capability.manifest.dependencies.python.join("、") || "无"}
              <br />
              命令：
              {capability.manifest.dependencies.commands
                .map((item) => `${item.name} ${item.version}`)
                .join("、") || "无"}
            </div>
          </div>
          <details>
            <summary style={{ cursor: "pointer", fontWeight: 600 }}>
              文件哈希 ({capability.manifest.files.length})
            </summary>
            <ul style={manifestListStyle}>
              {capability.manifest.files.map((file) => (
                <li key={file.path}>
                  <div>{file.path}</div>
                  <code style={{ overflowWrap: "anywhere" }}>{file.sha256}</code>
                </li>
              ))}
            </ul>
          </details>
          <div style={manifestValueStyle}>
            卸载：停止服务{" "}
            {capability.manifest.uninstall.stop_servers ? "是" : "否"}；
            无引用时移除环境{" "}
            {capability.manifest.uninstall
              .remove_environment_when_unreferenced
              ? "是"
              : "否"}
          </div>
          <div style={manifestValueStyle}>
            Manifest SHA-256：
            <code style={{ overflowWrap: "anywhere" }}>
              {capability.manifest.manifest_hash}
            </code>
          </div>
        </section>
      ) : null}
      {capability.available_actions?.length ? (
        <footer style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {capability.available_actions.map((action) => (
            <button
              key={action}
              type="button"
              onClick={() => onMutation(action, capability)}
              style={{
                ...secondaryButtonStyle,
                color: action === "uninstall" ? dark.danger : dark.text,
              }}
            >
              {action === "install"
                ? "安装"
                : action === "activate"
                  ? "启用"
                  : action === "repair"
                    ? "修复"
                    : "卸载"}
            </button>
          ))}
        </footer>
      ) : null}
    </article>
  );
}

const backdropStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 9997,
  display: "grid",
  placeItems: "center",
  padding: tokens.space.xl,
  background: dark.scrim,
};
const panelStyle: CSSProperties = {
  width: "min(920px, 96vw)",
  height: "min(680px, 92vh)",
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
  border: `1px solid ${dark.hairline}`,
  borderRadius: tokens.radius.lg,
  background: dark.panel,
  color: dark.text,
  boxShadow: tokens.shadow.overlay,
};
/**
 * page variant（D5 宿主模式）：无 fixed/backdrop，flex 填满工作台内容区；
 * 背景交给 WorkbenchShell（dark.bgSolid），零新增硬编码色值（T15）。
 */
const pagePanelStyle: CSSProperties = {
  flex: 1,
  width: "100%",
  height: "100%",
  minWidth: 0,
  minHeight: 0,
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
  background: "transparent",
  color: dark.text,
};
const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "space-between",
  gap: tokens.space.md,
  padding: `${tokens.space.lg}px ${tokens.space.xl}px ${tokens.space.md}px`,
};
const subtitleStyle: CSSProperties = {
  ...metaText,
  margin: `${tokens.space.xs}px 0 0`,
};
const statusBarStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  justifyContent: "space-between",
  gap: tokens.space.sm,
  padding: `${tokens.space.sm}px ${tokens.space.xl}px`,
  borderTop: `1px solid ${dark.hairline}`,
  borderBottom: `1px solid ${dark.hairline}`,
  background: "transparent",
  color: dark.textMuted,
  fontSize: tokens.text.sm.size,
  fontVariantNumeric: tokens.font.numeric,
};
const tabsStyle: CSSProperties = {
  display: "flex",
  gap: tokens.space.lg,
  padding: `${tokens.space.sm}px ${tokens.space.xl}px 0`,
  borderBottom: `1px solid ${dark.hairline}`,
};
/** 下划线 tab：选中态 2px 强调下划线 + 正文色，不换色相。 */
const tabButtonStyle = (active: boolean): CSSProperties => ({
  border: 0,
  borderBottom: `2px solid ${active ? dark.accent : "transparent"}`,
  height: tokens.controlHeight,
  padding: 0,
  background: "transparent",
  color: active ? dark.text : dark.textMuted,
  cursor: "pointer",
  fontFamily: tokens.font.ui,
  fontSize: tokens.text.base.size,
  fontWeight: active ? tokens.weight.semibold : tokens.weight.medium,
  transition,
});
const noticeStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: 8,
  margin: `${tokens.space.md}px ${tokens.space.xl}px 0`,
  padding: `${tokens.space.sm}px ${tokens.space.md}px`,
  border: `1px solid ${dark.hairline}`,
  borderLeft: `2px solid ${dark.accent}`,
  borderRadius: tokens.radius.md,
  background: "transparent",
  color: dark.textMuted,
  fontSize: tokens.text.sm.size,
};
const bodyGridStyle: CSSProperties = {
  minHeight: 0,
  flex: 1,
  display: "grid",
  // 左列下限 200px：250px 会在 min 窗口(800×560)把详情值列挤到 ~47px，
  // CJK/长词被逐字断行。列表行自带 ellipsis，200px 仍可读。
  gridTemplateColumns: "minmax(200px, 0.38fr) minmax(0, 0.62fr)",
  gap: 0,
};
const listPaneStyle: CSSProperties = {
  minHeight: 0,
  display: "flex",
  flexDirection: "column",
  gap: tokens.space.md,
  padding: tokens.space.lg,
  borderRight: `1px solid ${dark.hairline}`,
  background: "transparent",
};
const detailPaneStyle: CSSProperties = {
  minWidth: 0,
  minHeight: 0,
  overflowY: "auto",
  padding: tokens.space.xl,
  background: "transparent",
};
const operationListStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  display: "grid",
  // 与能力列表同类：钉死轨道下限，防操作卡片长 token 在 min 尺寸下撑宽
  gridTemplateColumns: "minmax(0, 1fr)",
  alignContent: "start",
  gap: tokens.space.md,
  overflowY: "auto",
  padding: tokens.space.xl,
};
const fieldLabelStyle: CSSProperties = {
  display: "grid",
  gap: tokens.space.xs,
  color: dark.textMuted,
  fontSize: tokens.text.sm.size,
  fontWeight: tokens.weight.medium,
};
const inputStyle: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  border: `1px solid ${dark.borderStrong}`,
  borderRadius: tokens.radius.md,
  height: tokens.controlHeight,
  padding: `0 ${tokens.space.md}px`,
  background: dark.card,
  color: dark.text,
  fontFamily: tokens.font.ui,
  fontSize: tokens.text.base.size,
  outline: "none",
  transition,
};
const capabilityListItemStyle = (active: boolean): CSSProperties => ({
  width: "100%",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: tokens.space.sm,
  padding: `${tokens.space.sm}px ${tokens.space.md}px`,
  border: `1px solid ${active ? dark.accentBorder : dark.cardBorder}`,
  borderRadius: tokens.radius.md,
  background: active ? dark.accentSoft : dark.card,
  color: dark.text,
  cursor: "pointer",
  textAlign: "left",
  transition,
});
const ellipsisStyle: CSSProperties = {
  display: "block",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontSize: tokens.text.base.size,
  fontWeight: tokens.weight.medium,
};
const listMetaStyle: CSSProperties = {
  display: "block",
  marginTop: 2,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  color: dark.textFaint,
  fontSize: tokens.text.sm.size,
};
const detailGridStyle: CSSProperties = {
  display: "grid",
  // 1fr 的隐式 min 是 auto（= 值列最长单词宽）；min 尺寸下改用
  // minmax(0,1fr) 让值列可收窄，配合 overflowWrap 断长 token
  gridTemplateColumns: "72px minmax(0, 1fr)",
  gap: `${tokens.space.md}px ${tokens.space.lg}px`,
  margin: 0,
  fontSize: tokens.text.base.size,
  overflowWrap: "anywhere",
};
const manifestValueStyle: CSSProperties = {
  marginTop: 3,
  color: dark.textMuted,
  fontSize: tokens.text.sm.size,
  lineHeight: tokens.text.sm.lh,
  overflowWrap: "anywhere",
};
const manifestListStyle: CSSProperties = {
  display: "grid",
  gap: 6,
  margin: `${tokens.space.xs}px 0 0`,
  paddingLeft: tokens.space.xl,
  color: dark.textMuted,
  fontSize: tokens.text.sm.size,
  lineHeight: tokens.text.sm.lh,
};
const emptyStyle: CSSProperties = {
  ...emptyStateStyle,
  padding: `${tokens.space.xxl}px ${tokens.space.lg}px`,
};
const secondaryButtonStyle: CSSProperties = {
  border: `1px solid ${dark.borderStrong}`,
  borderRadius: tokens.radius.md,
  height: 28,
  padding: `0 ${tokens.space.md}px`,
  background: "transparent",
  color: dark.text,
  cursor: "pointer",
  fontFamily: tokens.font.ui,
  fontSize: tokens.text.sm.size,
  fontWeight: tokens.weight.medium,
  transition,
};
const iconButtonStyle: CSSProperties = {
  width: tokens.controlHeight,
  height: tokens.controlHeight,
  border: `1px solid ${dark.border}`,
  borderRadius: tokens.radius.md,
  background: "transparent",
  color: dark.textMuted,
  cursor: "pointer",
  fontSize: tokens.text.lg.size,
  lineHeight: 1,
  transition,
};
const inlineDismissStyle: CSSProperties = {
  border: 0,
  padding: 0,
  background: "transparent",
  color: "inherit",
  cursor: "pointer",
};
