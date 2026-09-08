from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from typing import Any


_DEFAULT_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "cookie",
        "device_key",
    }
)
# 与 ``resources/diagnostic-redaction.json`` 的权威规则保持同一份口径。
#
# 旧的单条模式把**空格**放进了字符类（``[-_ A-Za-z0-9]{12,}``），于是任何含
# "key/sk/tsk/Bearer" 的普通日志文本都会被跨词整段吞掉：线上实测
# ``sdk_tool_handler_failed tool=memory_forget error_type=KeyError stable_code=unclassified``
# 被压成 ``error_type=[REDACTED]=unclassified``，Host 侧再也看不出异常类型
# （事故 J）。真实凭据本身不含空白，因此按分隔符拆成两条：``sk/tsk/key`` 前缀的
# 令牌不再跨空白，``Bearer`` 头单独保留其后的空白分隔。对真实密钥的覆盖面不变。
_TOKEN = re.compile(r"\b(?:sk|tsk|key)[-_A-Za-z0-9]{12,}\b", re.IGNORECASE)
_BEARER = re.compile(r"\bBearer\s+[-._~+/A-Za-z0-9=]{12,}\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class TraceRedactor:
    sensitive_keys: frozenset[str] = field(default_factory=lambda: _DEFAULT_KEYS)
    replacement: str = "[REDACTED]"
    value_patterns: tuple[re.Pattern[str], ...] = field(
        default_factory=lambda: (_TOKEN, _BEARER)
    )

    @classmethod
    def from_file(cls, path: str) -> "TraceRedactor":
        """Load the shared rules; malformed input deliberately fails closed."""

        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict):
                raise TypeError("redaction rules must be an object")
            if int(raw.get("version", 0)) != 1:
                raise ValueError("unsupported redaction rules version")
            raw_keys = raw["sensitive_keys"]
            raw_patterns = raw["sensitive_value_patterns"]
            if not isinstance(raw_keys, list) or not all(
                isinstance(item, str) and item.strip() for item in raw_keys
            ):
                raise ValueError("invalid sensitive_keys")
            if not isinstance(raw_patterns, list) or not all(
                isinstance(item, str) and item.strip() for item in raw_patterns
            ):
                raise ValueError("invalid sensitive_value_patterns")
            keys = frozenset(item.casefold() for item in raw_keys)
            if not _DEFAULT_KEYS.issubset(keys):
                raise ValueError("redaction rules omit required sensitive keys")
            patterns = tuple(re.compile(item) for item in raw_patterns)
            if not patterns:
                raise ValueError("redaction rules require a value pattern")
            replacement = raw.get("replacement", "[REDACTED]")
            if not isinstance(replacement, str) or not replacement:
                raise ValueError("invalid redaction replacement")
            return cls(keys, replacement, patterns)
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError, re.error):
            # Empty-key mode plus a match-all pattern means no raw detail can
            # escape when packaging/configuration is broken.
            return cls(frozenset(), "[REDACTED]", (re.compile(r"[\s\S]+"),))

    def redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): self.replacement if str(key).casefold() in self.sensitive_keys else self.redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, tuple):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            redacted = value
            for pattern in self.value_patterns:
                redacted = pattern.sub(self.replacement, redacted)
            return redacted
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self.replacement
