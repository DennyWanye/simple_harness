import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Toolbar } from "./Toolbar";

describe("Toolbar primary entries", () => {
  it("opens the Capability Center without exposing a product mode", () => {
    const onSkillStore = vi.fn();
    render(
      <Toolbar
        onMemory={vi.fn()}
        onTrace={vi.fn()}
        onSettings={vi.fn()}
        onSkillStore={onSkillStore}
        onFeedback={vi.fn()}
        onExit={vi.fn()}
        autostartReady
        autostartEnabled={false}
        onToggleAutostart={vi.fn()}
        vadStatus="idle"
        isPlaying={false}
        isRecording={false}
        connectionState="connected"
        routeKind="cloud"
      />,
    );

    expect(screen.queryByTestId("code-mode-toggle")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "能力中心" }));
    expect(onSkillStore).toHaveBeenCalledOnce();
  });
});
