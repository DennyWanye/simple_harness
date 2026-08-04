// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SettingsPanel } from "./SettingsPanel";

describe("SettingsPanel retired supervisor UI", () => {
  it("does not expose the removed supervisor controls", () => {
    const html = renderToStaticMarkup(
      <SettingsPanel
        open
        onClose={() => undefined}
        getChannel={() => null}
        lastMessage={null}
        secret=""
        petModels={[
          {
            id: "test-pet",
            name: "Test Pet",
            modelPath: "/test/model.json",
          },
        ]}
        currentPetModelId="test-pet"
        onPetModelChange={() => undefined}
      />,
    );

    expect(html).not.toContain("supervisor");
    expect(html).not.toContain("AutoResumeOrchestrator");
    expect(html).not.toContain("auto-resume-toggle");
  });
});
