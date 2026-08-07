// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it } from "vitest";

import { formatRelativeMs, formatRelativeSec } from "./relativeTime";

const NOW = 1_786_000_000_000; // 固定 now，避免用例随真实时钟漂移

describe("formatRelativeMs", () => {
  it("秒级：<60s 用 s", () => {
    expect(formatRelativeMs(NOW, NOW)).toBe("0s 前");
    expect(formatRelativeMs(NOW - 59_000, NOW)).toBe("59s 前");
  });

  it("分级：60s 起用 m", () => {
    expect(formatRelativeMs(NOW - 60_000, NOW)).toBe("1m 前");
    expect(formatRelativeMs(NOW - 59 * 60_000, NOW)).toBe("59m 前");
  });

  it("时级：3600s 起用 h", () => {
    expect(formatRelativeMs(NOW - 3_600_000, NOW)).toBe("1h 前");
    expect(formatRelativeMs(NOW - 14 * 3_600_000, NOW)).toBe("14h 前");
  });

  it("天级：86400s 起用 d", () => {
    expect(formatRelativeMs(NOW - 86_400_000, NOW)).toBe("1d 前");
    expect(formatRelativeMs(NOW - 30 * 86_400_000, NOW)).toBe("30d 前");
  });

  it("未来时间戳夹到 0，不出现负数", () => {
    expect(formatRelativeMs(NOW + 10_000, NOW)).toBe("0s 前");
  });
});

describe("formatRelativeSec", () => {
  it("按秒解释入参（后端 last_message_at / created_at 是秒）", () => {
    const nowSec = NOW / 1000;
    expect(formatRelativeSec(nowSec - 14 * 3600, NOW)).toBe("14h 前");
  });

  it("缺失/非法时间戳返回空串，让调用方整块不渲染", () => {
    // 若误按毫秒口径解释秒值，0 会变成 1970 → "20000d 前" 这类噪声。
    expect(formatRelativeSec(0, NOW)).toBe("");
    expect(formatRelativeSec(Number.NaN, NOW)).toBe("");
    expect(formatRelativeSec(-1, NOW)).toBe("");
  });
});
