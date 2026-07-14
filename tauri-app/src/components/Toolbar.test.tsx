import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Toolbar } from "./Toolbar";

describe("Toolbar code workflow entry", () => {
  it("keeps Code mode reachable while durable workflows are enabled", () => {
    const onCodeMode = vi.fn();
    render(
      <Toolbar
        onMemory={vi.fn()}
        onTrace={vi.fn()}
        onSettings={vi.fn()}
        onSkillStore={vi.fn()}
        onFeedback={vi.fn()}
        onExit={vi.fn()}
        onCodeMode={onCodeMode}
        autostartReady
        autostartEnabled={false}
        onToggleAutostart={vi.fn()}
        vadStatus="idle"
        isPlaying={false}
        isRecording={false}
        fps={30}
        connectionState="connected"
        routeKind="cloud"
      />,
    );

    const entry = screen.getByRole("button", { name: /Code/ });
    fireEvent.click(entry);
    expect(onCodeMode).toHaveBeenCalledOnce();
  });
});
