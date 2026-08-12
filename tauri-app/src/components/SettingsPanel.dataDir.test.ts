import { describe, expect, it } from "vitest";

import {
  type DataDirSetting,
  isDataDirPreferenceNoop,
  withNextLaunchDataDirPreference,
} from "./SettingsPanel";

describe("data directory next-launch preference", () => {
  it("does not claim the saved directory is effective before restart", () => {
    const current: DataDirSetting = {
      effective: "/data/current",
      default: "/data/default",
      env_override: null,
      preference: "/data/current",
      externally_pinned: false,
      effective_exists: true,
      effective_size_bytes: 42,
    };
    const commandReply: DataDirSetting = {
      ...current,
      effective: "/data/next",
      preference: "/data/next",
      effective_exists: false,
      effective_size_bytes: 0,
    };

    expect(withNextLaunchDataDirPreference(current, commandReply)).toEqual({
      ...current,
      preference: "/data/next",
    });
  });

  it("allows restoring the next launch to the currently effective directory", () => {
    const current: DataDirSetting = {
      effective: "/data/current",
      default: "/data/default",
      env_override: null,
      preference: "/data/next",
      externally_pinned: false,
      effective_exists: true,
      effective_size_bytes: 42,
    };

    expect(isDataDirPreferenceNoop(current, "/data/current")).toBe(false);
    expect(
      isDataDirPreferenceNoop(
        { ...current, preference: "/data/current" },
        "/data/current",
      ),
    ).toBe(true);
  });
});
