// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ControlMessage, IncomingMessage } from "../types/messages";
import {
  buildCapabilityMutationMessage,
  buildCapabilityOperationActionMessage,
  type CapabilityDescriptor,
  type CapabilityOperation,
} from "../types/capabilities";
import { CapabilityCenterPanel } from "./CapabilityCenterPanel";
import { SkillStorePanel } from "./SkillStorePanel";

class FakeCapabilityChannel {
  readonly sent: ControlMessage[] = [];
  private listener: ((message: IncomingMessage) => void) | null = null;

  send = (message: ControlMessage) => {
    this.sent.push(message);
    return true;
  };

  onMessage = (listener: (message: IncomingMessage) => void) => {
    this.listener = listener;
    return () => {
      if (this.listener === listener) this.listener = null;
    };
  };

  emit(message: unknown) {
    this.listener?.(message as IncomingMessage);
  }
}

const capabilities: CapabilityDescriptor[] = [
  {
    capability_id: "godot",
    name: "Godot",
    description: "Godot 4 项目检测与 headless 检查",
    categories: ["instruction", "tool", "pack"],
    version: "1.0.0",
    source: { type: "builtin", label: "DeskPet 内置" },
    scope: "builtin",
    health: "healthy",
    installed: true,
    manifest: {
      schema_version: 1,
      manifest_hash: "a".repeat(64),
      compatibility: {
        deskpet: ">=0.6.0-beta.9",
        os: ["windows"],
        architectures: ["x86_64"],
        python: ">=3.11",
      },
      entries: {
        skills: [{ path: "skills/godot/SKILL.md" }],
        tools: [
          {
            id: "detect",
            provider_name: "godot__detect",
            runtime: "deskpet-json-tool-v1",
            execution_profile: "native-adapter",
            input_views: [],
            entry: "tools/godot/main.py",
            schema: "tools/godot/detect.schema.json",
            healthcheck: "healthcheck",
          },
        ],
        mcp_servers: [],
      },
      permissions: [
        "filesystem_read",
        "filesystem_write",
        "process_execute",
      ],
      effects: ["read_only", "staged_file", "opaque_manual"],
      dependencies: {
        python: [],
        commands: [{ name: "godot", version: ">=4.0" }],
      },
      files: [
        {
          path: "tools/godot/main.py",
          sha256: "b".repeat(64),
        },
      ],
      uninstall: {
        stop_servers: true,
        remove_environment_when_unreferenced: true,
      },
    },
    available_actions: ["activate", "repair", "uninstall"],
  },
  {
    capability_id: "blender",
    name: "Blender",
    description: "3D 资产处理",
    categories: ["tool"],
    version: "0.2.0",
    source: { type: "marketplace", label: "官方市场" },
    scope: "user",
    health: "degraded",
    health_summary: "缺少可执行文件",
    installed: false,
    available_actions: ["install"],
  },
];

function operation(
  overrides: Partial<CapabilityOperation> = {},
): CapabilityOperation {
  return {
    operation_id: "operation-godot",
    capability_id: "godot",
    capability_name: "Godot",
    kind: "install",
    phase: "verified",
    status: "running",
    authorization_mode: "manual",
    current_validation: "验证清单哈希",
    latest_result: "清单有效",
    available_actions: ["cancel", "rollback", "uninstall"],
    artifacts: [],
    verification_receipts: [],
    event_seq: 1,
    ...overrides,
  };
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(cleanup);

describe("CapabilityCenterPanel", () => {
  it("lists, filters, and opens safe capability detail", () => {
    const channel = new FakeCapabilityChannel();
    render(
      <CapabilityCenterPanel
        open
        channel={channel}
        onClose={vi.fn()}
        onOpenLegacySkillStore={vi.fn()}
        initialCapabilities={capabilities}
      />,
    );

    expect(screen.getByText("Godot 4 项目检测与 headless 检查")).toBeTruthy();
    expect(
      screen.getByTestId("capability-manifest-detail").textContent,
    ).toContain("godot__detect");
    expect(
      screen.getByTestId("capability-manifest-detail").textContent,
    ).toContain("godot >=4.0");
    fireEvent.change(screen.getByLabelText("搜索能力"), {
      target: { value: "Blender" },
    });
    expect(screen.queryByRole("button", { name: /Godot/ })).toBeNull();
    expect(screen.getByTestId("capability-detail").textContent).toContain(
      "Blender",
    );
    expect(
      screen.getByRole("button", { name: /Blender/ }).getAttribute("aria-pressed"),
    ).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /Blender/ }));
    expect(screen.getByTestId("capability-detail").textContent).toContain(
      "缺少可执行文件",
    );

    fireEvent.change(screen.getByLabelText("能力类别"), {
      target: { value: "pack" },
    });
    expect(screen.getByText("没有符合筛选条件的能力。")).toBeTruthy();

    expect(channel.sent.map((message) => message.type)).toEqual([
      "capability_list",
      "capability_operations_list",
      "permission_auto_mode_get",
    ]);
  });

  it("reduces operation pushes and sends cancel, rollback, and uninstall", () => {
    const channel = new FakeCapabilityChannel();
    render(
      <CapabilityCenterPanel
        open
        channel={channel}
        onClose={vi.fn()}
        onOpenLegacySkillStore={vi.fn()}
        initialCapabilities={capabilities}
        initialOperations={[operation()]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /操作 \(1\)/ }));
    act(() => {
      channel.emit({
        type: "capability_operation_event",
        payload: {
          operation: {
            operation_id: "operation-godot",
            event_seq: 2,
            phase: "candidate_ready",
            current_validation: "Godot headless healthcheck",
            latest_result: "候选运行时健康",
          },
        },
      });
    });
    expect(
      screen.getByTestId("capability-operation-operation-godot").textContent,
    ).toContain("候选运行时健康");

    for (const action of ["取消", "回滚", "卸载"]) {
      fireEvent.click(screen.getByRole("button", { name: action }));
    }
    expect(channel.sent.slice(-3).map((message) => message.type)).toEqual([
      "capability_operation_cancel",
      "capability_rollback",
      "capability_uninstall",
    ]);
  });

  it("shows Auto as direct DeskPet authorization without a waiting popup", () => {
    localStorage.setItem("deskpet.auto_mode", "true");
    const channel = new FakeCapabilityChannel();
    render(
      <CapabilityCenterPanel
        open
        channel={channel}
        onClose={vi.fn()}
        onOpenLegacySkillStore={vi.fn()}
        initialCapabilities={capabilities}
        initialOperations={[
          operation({
            authorization_mode: "auto",
            status: "running",
          }),
        ]}
      />,
    );

    act(() => {
      channel.emit({
        type: "permission_auto_mode_response",
        payload: { enabled: true },
      });
    });
    expect(screen.getByText(/Auto（直接执行）/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /操作 \(1\)/ }));
    expect(screen.getByText("Auto 已授权（审计记录）")).toBeTruthy();
    expect(screen.queryByText("Manual：等待授权")).toBeNull();
    expect(screen.queryByRole("dialog", { name: /授权/ })).toBeNull();
  });

  it("page variant 内嵌渲染：无 dialog/backdrop/关闭钮，快照请求照发（WB-6）", () => {
    const channel = new FakeCapabilityChannel();
    render(
      <CapabilityCenterPanel
        open
        variant="page"
        channel={channel}
        onOpenLegacySkillStore={vi.fn()}
        initialCapabilities={capabilities}
      />,
    );

    // 页面化：不再是模态浮层（D5 page 分支去 backdrop/fixed）。
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("presentation")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "关闭能力中心" }),
    ).toBeNull();

    // 填满内容区（非 fixed 定位 + 100% 宽高）。
    const region = screen.getByRole("region", { name: "能力中心" });
    expect(region.style.position).toBe("");
    expect(region.style.width).toBe("100%");
    expect(region.style.height).toBe("100%");

    // 列表/详情与快照请求行为与 overlay 完全一致（能力不减）。
    expect(screen.getByTestId("capability-list")).toBeTruthy();
    expect(screen.getByTestId("capability-detail")).toBeTruthy();
    expect(channel.sent.map((message) => message.type)).toEqual([
      "capability_list",
      "capability_operations_list",
      "permission_auto_mode_get",
    ]);
  });

  it("keeps the legacy Skill Store reachable", () => {
    const openLegacy = vi.fn();
    const rendered = render(
      <CapabilityCenterPanel
        open
        channel={null}
        onClose={vi.fn()}
        onOpenLegacySkillStore={openLegacy}
        initialCapabilities={capabilities}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "打开旧 Skill Store" }));
    expect(openLegacy).toHaveBeenCalledOnce();

    rendered.rerender(
      <SkillStorePanel
        open
        channel={null}
        onClose={vi.fn()}
        onOpenCapabilityCenter={openLegacy}
      />,
    );
    expect(screen.getByRole("button", { name: "已安装" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "市场" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "返回能力中心" }));
    expect(openLegacy).toHaveBeenCalledTimes(2);
  });
});

describe("Capability Center wire builders", () => {
  it("maps lifecycle actions to explicit backend messages", () => {
    expect(buildCapabilityMutationMessage("install", "godot")).toEqual({
      type: "capability_install",
      payload: { capability_id: "godot" },
    });
    expect(
      buildCapabilityOperationActionMessage("rollback", operation()),
    ).toEqual({
      type: "capability_rollback",
      payload: {
        operation_id: "operation-godot",
        capability_id: "godot",
      },
    });
  });
});
