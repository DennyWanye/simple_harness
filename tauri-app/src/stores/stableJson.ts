// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

export function stable_json(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable_json).join(",")}]`;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stable_json(record[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}
