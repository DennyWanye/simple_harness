export const CONTROL_COMMAND_SCHEMA = "control-command-canonical-v1" as const;
const DOMAIN = new TextEncoder().encode("control-command-canonical-v1\0");
const MAX_U64 = (1n << 64n) - 1n;

export type CanonicalJson =
  | null
  | boolean
  | string
  | number
  | CanonicalJson[]
  | { [key: string]: CanonicalJson };

export class CanonicalCommandError extends Error {}

function concat(...parts: Uint8Array[]): Uint8Array {
  const result = new Uint8Array(parts.reduce((n, part) => n + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    result.set(part, offset);
    offset += part.length;
  }
  return result;
}

function u32(value: number): Uint8Array {
  if (!Number.isInteger(value) || value < 0 || value > 0xffff_ffff) {
    throw new CanonicalCommandError("length_or_count_overflow");
  }
  const result = new Uint8Array(4);
  new DataView(result.buffer).setUint32(0, value, false);
  return result;
}

function u64(value: bigint): Uint8Array {
  if (value < 0n || value > MAX_U64) {
    throw new CanonicalCommandError("u64_overflow");
  }
  const result = new Uint8Array(8);
  new DataView(result.buffer).setBigUint64(0, value, false);
  return result;
}

export function parseCanonicalU64(value: string, field: string): bigint {
  if (!/^(?:0|[1-9][0-9]*)$/.test(value)) {
    throw new CanonicalCommandError(`${field}:non_canonical_u64`);
  }
  const parsed = BigInt(value);
  if (parsed > MAX_U64) {
    throw new CanonicalCommandError(`${field}:u64_overflow`);
  }
  return parsed;
}

function utf8(value: string): Uint8Array {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) {
        throw new CanonicalCommandError("lone_surrogate");
      }
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      throw new CanonicalCommandError("lone_surrogate");
    }
  }
  return new TextEncoder().encode(value);
}

function lp(value: string): Uint8Array {
  const bytes = utf8(value);
  return concat(u32(bytes.length), bytes);
}

export function encodeControlBody(value: CanonicalJson): Uint8Array {
  if (value === null) return Uint8Array.of(0);
  if (value === false) return Uint8Array.of(1);
  if (value === true) return Uint8Array.of(2);
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value) || Object.is(value, -0)) {
      throw new CanonicalCommandError("float_or_unsafe_integer");
    }
    const bytes = new Uint8Array(8);
    new DataView(bytes.buffer).setBigInt64(0, BigInt(value), false);
    return concat(Uint8Array.of(3), bytes);
  }
  if (typeof value === "string") {
    const bytes = utf8(value);
    return concat(Uint8Array.of(4), u32(bytes.length), bytes);
  }
  if (Array.isArray(value)) {
    return concat(
      Uint8Array.of(5),
      u32(value.length),
      ...value.map(encodeControlBody),
    );
  }
  if (typeof value === "object") {
    const entries = Object.entries(value).map(([key, item]) => ({
      key,
      item,
      encoded: utf8(key),
    }));
    entries.sort((left, right) => {
      const length = Math.min(left.encoded.length, right.encoded.length);
      for (let index = 0; index < length; index += 1) {
        const delta = left.encoded[index] - right.encoded[index];
        if (delta !== 0) return delta;
      }
      return left.encoded.length - right.encoded.length;
    });
    return concat(
      Uint8Array.of(6),
      u32(entries.length),
      ...entries.flatMap(({ key, item }) => [
        encodeControlBody(key),
        encodeControlBody(item),
      ]),
    );
  }
  throw new CanonicalCommandError("unsupported_body_type");
}

export function canonicalRequestBytes(
  commandKind: string,
  requestSeq: string,
  bindingEpoch: string,
  body: CanonicalJson,
): Uint8Array {
  return concat(
    DOMAIN,
    lp(commandKind),
    u64(parseCanonicalU64(requestSeq, "request_seq")),
    u64(parseCanonicalU64(bindingEpoch, "binding_epoch")),
    encodeControlBody(body),
  );
}

export async function canonicalRequestHash(
  commandKind: string,
  requestSeq: string,
  bindingEpoch: string,
  body: CanonicalJson,
): Promise<string> {
  const payload = canonicalRequestBytes(commandKind, requestSeq, bindingEpoch, body);
  const exactBuffer = payload.buffer.slice(
    payload.byteOffset,
    payload.byteOffset + payload.byteLength,
  ) as ArrayBuffer;
  const digest = await crypto.subtle.digest("SHA-256", exactBuffer);
  return bytesToHex(new Uint8Array(digest));
}

export function bytesToHex(value: Uint8Array): string {
  return Array.from(value, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Strict parser used by shared test vectors and raw command ingress. */
export function parseCanonicalJson(raw: string): CanonicalJson {
  let cursor = 0;
  const whitespace = () => {
    while (/\s/.test(raw[cursor] ?? "")) cursor += 1;
  };
  const parseString = (): string => {
    const start = cursor;
    cursor += 1;
    let escaped = false;
    while (cursor < raw.length) {
      const char = raw[cursor++];
      if (!escaped && char === '"') {
        const value = JSON.parse(raw.slice(start, cursor)) as string;
        utf8(value);
        return value;
      }
      if (!escaped && char === "\\") escaped = true;
      else escaped = false;
    }
    throw new CanonicalCommandError("unterminated_string");
  };
  const parseValue = (): CanonicalJson => {
    whitespace();
    const char = raw[cursor];
    if (char === '"') return parseString();
    if (raw.startsWith("null", cursor)) {
      cursor += 4;
      return null;
    }
    if (raw.startsWith("true", cursor)) {
      cursor += 4;
      return true;
    }
    if (raw.startsWith("false", cursor)) {
      cursor += 5;
      return false;
    }
    if (char === "[") {
      cursor += 1;
      const result: CanonicalJson[] = [];
      whitespace();
      if (raw[cursor] === "]") {
        cursor += 1;
        return result;
      }
      for (;;) {
        result.push(parseValue());
        whitespace();
        if (raw[cursor] === "]") {
          cursor += 1;
          return result;
        }
        if (raw[cursor++] !== ",") throw new CanonicalCommandError("invalid_array");
      }
    }
    if (char === "{") {
      cursor += 1;
      const result: Record<string, CanonicalJson> = {};
      const seen = new Set<string>();
      whitespace();
      if (raw[cursor] === "}") {
        cursor += 1;
        return result;
      }
      for (;;) {
        whitespace();
        if (raw[cursor] !== '"') throw new CanonicalCommandError("invalid_object_key");
        const key = parseString();
        if (seen.has(key)) throw new CanonicalCommandError("duplicate_object_key");
        seen.add(key);
        whitespace();
        if (raw[cursor++] !== ":") throw new CanonicalCommandError("invalid_object");
        result[key] = parseValue();
        whitespace();
        if (raw[cursor] === "}") {
          cursor += 1;
          return result;
        }
        if (raw[cursor++] !== ",") throw new CanonicalCommandError("invalid_object");
      }
    }
    const match = raw.slice(cursor).match(/^-?(?:0|[1-9][0-9]*)/);
    if (!match) throw new CanonicalCommandError("invalid_json");
    const token = match[0];
    cursor += token.length;
    if (token === "-0") throw new CanonicalCommandError("negative_zero");
    const number = Number(token);
    if (!Number.isSafeInteger(number)) {
      throw new CanonicalCommandError("integer_outside_safe_range");
    }
    return number;
  };
  const result = parseValue();
  whitespace();
  if (cursor !== raw.length) throw new CanonicalCommandError("trailing_json");
  encodeControlBody(result);
  return result;
}
