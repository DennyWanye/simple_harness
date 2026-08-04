"""Core, declarative and default-deny tool presentation interpreter V1."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from deskpet.security.redaction import TraceRedactor
from deskpet.security.sensitive_text import redact_sensitive_text


_ACTIVITY_KINDS = frozenset(
    {"inspect", "plan", "mutate", "verify", "communicate", "wait"}
)
_POINTER = re.compile(r"(?:/(?:[^~/]|~[01])*)*")
_DATA_URI = re.compile(r"(?i)^data:[^,]{0,256},")
_REDACTOR = TraceRedactor()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _pointer_get(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    current = value
    for raw in pointer.split("/")[1:]:
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and key in current:
            current = current[key]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            try:
                current = current[int(key)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _bound_string(value: str, cap: int) -> tuple[str, str | None]:
    cleaned = redact_sensitive_text(value)
    if _DATA_URI.match(cleaned):
        return "[REDACTED:DATA_URI]", hashlib.sha256(value.encode("utf-8")).hexdigest()
    encoded = cleaned.encode("utf-8")
    if len(encoded) <= cap:
        return cleaned, None
    clipped = encoded[:cap]
    while clipped:
        try:
            text = clipped.decode("utf-8")
            break
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    else:
        text = ""
    return text + "…", hashlib.sha256(encoded).hexdigest()


def _bound_value(value: Any, cap: int) -> tuple[Any, str | None]:
    redacted = _REDACTOR.redact(value)
    if isinstance(redacted, str):
        return _bound_string(redacted, cap)
    if redacted is None or isinstance(redacted, (bool, int, float)):
        return redacted, None
    serialized = _canonical(redacted)
    bounded, digest = _bound_string(serialized, cap)
    if digest is None:
        return redacted, None
    return bounded, digest


@dataclass(frozen=True, slots=True)
class ToolPresentationPolicyV1:
    tool_name: str
    activity_kind: str
    action_code: str
    safe_arg_paths: tuple[str, ...]
    safe_result_paths: tuple[str, ...]
    target_label_template: str
    field_byte_cap: int = 2048
    total_byte_cap: int = 8192
    redaction_version: str = "trace+sensitive-v1"
    interpreter_version: str = "core-v1"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.tool_name.strip() or not self.action_code.strip():
            raise ValueError("tool presentation identity is required")
        if self.activity_kind not in _ACTIVITY_KINDS:
            raise ValueError("unsupported tool activity_kind")
        if any(not _POINTER.fullmatch(path) for path in (*self.safe_arg_paths, *self.safe_result_paths)):
            raise ValueError("invalid tool presentation JSON pointer")
        if not 64 <= self.field_byte_cap <= 16_384:
            raise ValueError("invalid field byte cap")
        if not self.field_byte_cap <= self.total_byte_cap <= 65_536:
            raise ValueError("invalid total byte cap")

    @property
    def policy_hash(self) -> str:
        return _hash(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["safe_arg_paths"] = list(self.safe_arg_paths)
        result["safe_result_paths"] = list(self.safe_result_paths)
        result["policy_hash"] = self.policy_hash
        return result


def compile_tool_presentation_policy(spec: Any) -> ToolPresentationPolicyV1:
    return ToolPresentationPolicyV1(
        tool_name=str(spec.name),
        activity_kind=str(getattr(spec, "activity_kind", "mutate")),
        action_code=str(getattr(spec, "public_action_code", "execute")),
        safe_arg_paths=tuple(getattr(spec, "public_safe_arg_paths", ())),
        safe_result_paths=tuple(getattr(spec, "public_safe_result_paths", ())),
        target_label_template=str(
            getattr(spec, "public_target_label_template", "{tool_name}")
        ),
        field_byte_cap=int(getattr(spec, "public_field_byte_cap", 2048)),
        total_byte_cap=int(getattr(spec, "public_total_byte_cap", 8192)),
    )


def legacy_unknown_tool_policy(tool_name: str) -> ToolPresentationPolicyV1:
    return ToolPresentationPolicyV1(
        tool_name=tool_name.strip() or "unknown_tool",
        activity_kind="mutate",
        action_code="execute",
        safe_arg_paths=(),
        safe_result_paths=(),
        target_label_template="{tool_name}",
    )


@dataclass(frozen=True, slots=True)
class ToolPublicProjectionV1:
    tool_name: str
    activity_kind: str
    action_code: str
    safe_target_label: str
    status: str
    safe_input: Mapping[str, Any]
    bounded_result: Mapping[str, Any]
    truncation_hashes: Mapping[str, str]
    policy_hash: str
    interpreter_version: str = "core-v1"
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolPublicProjectorV1:
    def project(
        self,
        policy: ToolPresentationPolicyV1,
        *,
        arguments: Any,
        result: Any,
        status: str,
    ) -> ToolPublicProjectionV1:
        hashes: dict[str, str] = {}

        def select(source: Any, paths: tuple[str, ...], prefix: str) -> dict[str, Any]:
            selected: dict[str, Any] = {}
            for pointer in paths:
                raw = _pointer_get(source, pointer)
                leaf = pointer.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")
                raw = _REDACTOR.redact({leaf: raw}).get(leaf)
                bounded, digest = _bound_value(raw, policy.field_byte_cap)
                selected[pointer] = bounded
                if digest:
                    hashes[f"{prefix}:{pointer}"] = digest
            return selected

        safe_input = select(arguments, policy.safe_arg_paths, "arg")
        safe_result = select(result, policy.safe_result_paths, "result")
        label_values = {"tool_name": policy.tool_name}
        for pointer, value in safe_input.items():
            label_values[pointer.rsplit("/", 1)[-1] or "value"] = value
        try:
            label = policy.target_label_template.format_map(_SafeFormat(label_values))
        except (ValueError, KeyError):
            label = policy.tool_name
        label, label_hash = _bound_string(label, min(policy.field_byte_cap, 512))
        if label_hash:
            hashes["target_label"] = label_hash

        aggregate = {"input": safe_input, "result": safe_result}
        encoded = _canonical(aggregate).encode("utf-8")
        if len(encoded) > policy.total_byte_cap:
            hashes["tool_total"] = hashlib.sha256(encoded).hexdigest()
            # A total overflow fails closed instead of leaking a raw prefix.
            safe_input = {}
            safe_result = {"summary": "[TRUNCATED:TOOL_DETAIL]"}

        return ToolPublicProjectionV1(
            tool_name=policy.tool_name,
            activity_kind=policy.activity_kind,
            action_code=policy.action_code,
            safe_target_label=label,
            status=str(status),
            safe_input=safe_input,
            bounded_result=safe_result,
            truncation_hashes=hashes,
            policy_hash=policy.policy_hash,
        )


class _SafeFormat(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "?"


@dataclass(frozen=True, slots=True)
class ToolPresentationSnapshotExtension:
    """Start-commit extension that freezes presentation-only tool metadata."""

    policies: tuple[ToolPresentationPolicyV1, ...]

    @property
    def presentation_only(self) -> bool:
        return True

    @property
    def descriptor(self) -> Any:
        from deskpet.harness.contracts import HostExtensionRefV1

        digest = _hash(tuple(policy.to_dict() for policy in self.policies))
        return HostExtensionRefV1(
            kind="deskpet.tool-presentation.v1",
            ref=f"tool-presentation:{digest}",
            content_hash=digest,
        )

    async def apply_start_commit(self, transaction: Any, *, spec: Any, start_snapshot: Any) -> dict[str, Any]:
        root_run_id = str(spec.context.root_run_id)
        if str(spec.run_id) != root_run_id:
            raise ValueError("tool presentation snapshot must be frozen by the Root")
        descriptors = tuple(policy.to_dict() for policy in self.policies)
        await transaction.freeze_tool_presentation_specs(
            root_run_id=root_run_id,
            policies=descriptors,
            created_at=float(start_snapshot.created_at),
        )
        return self.descriptor


__all__ = [
    "ToolPresentationPolicyV1",
    "ToolPublicProjectionV1",
    "ToolPublicProjectorV1",
    "ToolPresentationSnapshotExtension",
    "compile_tool_presentation_policy",
    "legacy_unknown_tool_policy",
]
