# SPDX-License-Identifier: Apache-2.0
"""测试用的 JSON Schema 子集核对器（第 2 批 T06）。

只为"八份对外合同 Schema 与 Python 边界 codec 对同一正负样本一致"这一条用例服务：Schema 是对外
发布的合同文本，codec 是生产代码里唯一的校验路径；两边要用同一批样本对账，就得有一个不依赖
``jsonschema``（用户 2026-09-18 裁定不引入）的读 Schema 的一方。这里只实现八份 Schema 实际用到的
Draft 2020-12 关键字，碰到没实现的关键字直接报错，不猜。生产代码不得引用本模块。
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_SUPPORTED = {
    "$schema", "$id", "type", "const", "enum", "required", "properties", "additionalProperties",
    "items", "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "minimum", "maximum",
    "pattern", "anyOf", "allOf", "contains", "minContains", "maxContains",
}


def _is_type(value: Any, name: str) -> bool:
    if name == "object":
        return isinstance(value, Mapping)
    if name == "array":
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return type(value) is int or (isinstance(value, float) and value.is_integer())
    if name == "number":
        return type(value) in (int, float)
    if name == "boolean":
        return type(value) is bool
    if name == "null":
        return value is None
    raise ValueError(f"unsupported JSON Schema type {name!r}")


def _equal(left: Any, right: Any) -> bool:
    """JSON 相等：true/1 不相等，1/1.0 相等。"""
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(_equal(left[k], right[k]) for k in left)
    if _is_type(left, "array") and _is_type(right, "array"):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def violations(schema: Mapping[str, Any], value: Any, path: str = "$") -> list[str]:
    """``value`` 违反 ``schema`` 的地方（空列表即合规）。"""
    unknown = set(schema) - _SUPPORTED
    if unknown:
        raise ValueError(f"unsupported JSON Schema keywords at {path}: {sorted(unknown)}")
    found: list[str] = []
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, name) for name in names):
            return [f"{path}: type is not {names}"]
    if "const" in schema and not _equal(value, schema["const"]):
        found.append(f"{path}: const mismatch")
    if "enum" in schema and not any(_equal(value, item) for item in schema["enum"]):
        found.append(f"{path}: not in enum")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            found.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            found.append(f"{path}: longer than maxLength")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            found.append(f"{path}: pattern mismatch")
    if type(value) in (int, float):
        if "minimum" in schema and value < schema["minimum"]:
            found.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            found.append(f"{path}: above maximum")
    if isinstance(value, Mapping):
        for name in schema.get("required", ()):
            if name not in value:
                found.append(f"{path}: missing required {name!r}")
        properties = schema.get("properties", {})
        for name, item in value.items():
            if name in properties:
                found.extend(violations(properties[name], item, f"{path}.{name}"))
            else:
                extra = schema.get("additionalProperties", True)
                if extra is False:
                    found.append(f"{path}: unknown property {name!r}")
                elif isinstance(extra, Mapping):
                    found.extend(violations(extra, item, f"{path}.{name}"))
    if _is_type(value, "array"):
        if "minItems" in schema and len(value) < schema["minItems"]:
            found.append(f"{path}: fewer than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            found.append(f"{path}: more than maxItems")
        if schema.get("uniqueItems") and any(
                _equal(a, b) for i, a in enumerate(value) for b in value[i + 1:]):
            found.append(f"{path}: items are not unique")
        if "items" in schema:
            for index, item in enumerate(value):
                found.extend(violations(schema["items"], item, f"{path}[{index}]"))
        if "contains" in schema:
            matching = sum(1 for item in value if not violations(schema["contains"], item, path))
            if matching < schema.get("minContains", 1):
                found.append(f"{path}: contains fewer than minContains")
            if "maxContains" in schema and matching > schema["maxContains"]:
                found.append(f"{path}: contains more than maxContains")
    if "anyOf" in schema and all(violations(item, value, path) for item in schema["anyOf"]):
        found.append(f"{path}: matches no anyOf branch")
    for item in schema.get("allOf", ()):
        found.extend(violations(item, value, path))
    return found


def conforms(schema: Mapping[str, Any], value: Any) -> bool:
    return not violations(schema, value)


__all__ = ("conforms", "violations")
