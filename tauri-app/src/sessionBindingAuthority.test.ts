import { describe, expect, it } from "vitest";

import {
  buildRemoveMessage,
  buildReorderMessage,
  buildSetDefaultModelMessage,
  buildToggleEnabledMessage,
  type Provider,
} from "./components/providersStore";

const provider: Provider = {
  id: "relay",
  name: "Relay",
  base_url: "https://example.invalid/v1",
  models: ["kimi-k3"],
  default_model: "kimi-k3",
  api_key: "********",
  priority: 1,
  enabled: true,
  incarnation_id: "inc-1",
  config_revision: 4,
};

describe("versioned provider mutation authority", () => {
  it("registry update/remove/reorder requests carry provider CAS versions", () => {
    expect(buildToggleEnabledMessage("relay", false, provider).payload).toMatchObject({
      expected_incarnation_id: "inc-1",
      expected_config_revision: 4,
    });
    expect(buildSetDefaultModelMessage(provider, "kimi-k3").payload).toMatchObject({
      patch: { default_model: "kimi-k3" },
      expected_incarnation_id: "inc-1",
      expected_config_revision: 4,
    });
    expect(buildRemoveMessage("relay", provider).payload).toMatchObject({
      expected_incarnation_id: "inc-1",
      expected_config_revision: 4,
    });
    expect(buildReorderMessage(["relay"], [provider]).payload).toMatchObject({
      expected_versions: {
        relay: { incarnation_id: "inc-1", config_revision: 4 },
      },
    });
  });
});
