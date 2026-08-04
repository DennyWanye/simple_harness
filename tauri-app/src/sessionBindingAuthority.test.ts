import { describe, expect, it } from "vitest";

import {
  buildSetModelMessage,
  buildSetProviderMessage,
} from "./sessionModelMessages";
import {
  buildRemoveMessage,
  buildReorderMessage,
  buildToggleEnabledMessage,
  type Provider,
} from "./components/SettingsProviders";

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

describe("versioned Session/provider mutation authority", () => {
  it("bind/model requests carry the exact state observed by the user", () => {
    const authority = {
      expected_binding_epoch: 7,
      expected_provider_incarnation_id: "inc-1",
      expected_provider_config_revision: 4,
    };
    expect(buildSetProviderMessage("session", "relay", authority).payload).toMatchObject(
      authority,
    );
    expect(
      buildSetModelMessage("session", "kimi-k3", { fast: true }, authority).payload,
    ).toMatchObject(authority);
  });

  it("registry update/remove/reorder requests carry provider CAS versions", () => {
    expect(buildToggleEnabledMessage("relay", false, provider).payload).toMatchObject({
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
