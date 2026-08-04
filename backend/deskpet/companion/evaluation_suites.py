"""Strict loader for the evaluation suites shipped with DeskPet.

The loader deliberately validates the complete resource set and hashes before
returning a case.  A frozen build therefore cannot silently run a stale,
partially copied, or locally extended suite.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

_DEFAULT_PACKAGE = "deskpet.companion.eval_suites"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _immutable_json_object(value: Mapping[str, Any]) -> Mapping[str, Any]:
    # A JSON round trip also rejects values that cannot be represented in the
    # packaged, language-neutral contract.
    cloned = json.loads(_canonical_json(dict(value)))
    frozen = _freeze_json(cloned)
    if not isinstance(frozen, Mapping):  # pragma: no cover - defensive
        raise ValueError("expected JSON object")
    return frozen


class PackagedEvaluationSuiteError(RuntimeError):
    """A release-resource validation failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        message = code if not detail else f"{code}: {detail}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PackagedEvaluationCaseV1:
    case_id: str
    required: bool
    input: Mapping[str, Any]
    assertions: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class PackagedEvaluationSuiteV1:
    suite_id: str
    kind: str
    resource: str
    resource_hash: str
    assertions_hash: str
    cases: tuple[PackagedEvaluationCaseV1, ...]


@dataclass(frozen=True, slots=True)
class PackagedEvaluationSuiteManifestV1:
    manifest_id: str
    suites: tuple[PackagedEvaluationSuiteV1, ...]

    def suite(self, suite_id: str) -> PackagedEvaluationSuiteV1:
        for suite in self.suites:
            if suite.suite_id == suite_id:
                return suite
        raise PackagedEvaluationSuiteError(
            "evaluation_suite_unknown",
            suite_id,
        )


class PackagedEvaluationSuiteLoader:
    """Load and attest packaged suites through :mod:`importlib.resources`."""

    def __init__(
        self,
        *,
        package: str = _DEFAULT_PACKAGE,
        resource_root: Any | None = None,
    ) -> None:
        self._package = package
        self._resource_root = resource_root

    def _root(self) -> Any:
        if self._resource_root is not None:
            return self._resource_root
        return importlib.resources.files(self._package)

    @staticmethod
    def _read_json(root: Any, name: str) -> tuple[bytes, Mapping[str, Any]]:
        try:
            raw = root.joinpath(name).read_bytes()
        except (FileNotFoundError, OSError) as exc:
            raise PackagedEvaluationSuiteError(
                "evaluation_suite_resource_missing",
                name,
            ) from exc
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackagedEvaluationSuiteError(
                "evaluation_suite_invalid_json",
                name,
            ) from exc
        if not isinstance(value, dict):
            raise PackagedEvaluationSuiteError(
                "evaluation_suite_invalid_document",
                name,
            )
        return raw, value

    @staticmethod
    def _text(value: object, *, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise PackagedEvaluationSuiteError(
                "evaluation_suite_invalid_manifest",
                field,
            )
        return value

    @staticmethod
    def _hash(value: object, *, field: str) -> str:
        digest = PackagedEvaluationSuiteLoader._text(value, field=field)
        if _SHA256_RE.fullmatch(digest) is None:
            raise PackagedEvaluationSuiteError(
                "evaluation_suite_invalid_manifest",
                field,
            )
        return digest

    @staticmethod
    def _resource_names(root: Any) -> set[str]:
        try:
            return {
                entry.name
                for entry in root.iterdir()
                if entry.is_file()
                and entry.name.endswith(".json")
                and entry.name != "manifest.json"
            }
        except OSError as exc:
            raise PackagedEvaluationSuiteError(
                "evaluation_suite_resource_unavailable"
            ) from exc

    def load_manifest(self) -> PackagedEvaluationSuiteManifestV1:
        root = self._root()
        _, manifest = self._read_json(root, "manifest.json")
        if manifest.get("schema_version") != 1:
            raise PackagedEvaluationSuiteError(
                "evaluation_suite_invalid_manifest",
                "schema_version",
            )
        manifest_id = self._text(manifest.get("manifest_id"), field="manifest_id")
        entries = manifest.get("suites")
        if not isinstance(entries, list) or not entries:
            raise PackagedEvaluationSuiteError(
                "evaluation_suite_invalid_manifest",
                "suites",
            )

        declared_resources: set[str] = set()
        declared_suite_ids: set[str] = set()
        suites: list[PackagedEvaluationSuiteV1] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise PackagedEvaluationSuiteError(
                    "evaluation_suite_invalid_manifest",
                    "suite_entry",
                )
            suite_id = self._text(entry.get("suite_id"), field="suite_id")
            kind = self._text(entry.get("kind"), field=f"{suite_id}.kind")
            resource = self._text(
                entry.get("resource"),
                field=f"{suite_id}.resource",
            )
            if (
                "/" in resource
                or "\\" in resource
                or resource == "manifest.json"
                or not resource.endswith(".json")
            ):
                raise PackagedEvaluationSuiteError(
                    "evaluation_suite_invalid_manifest",
                    f"{suite_id}.resource",
                )
            if suite_id in declared_suite_ids or resource in declared_resources:
                raise PackagedEvaluationSuiteError(
                    "evaluation_suite_duplicate_manifest_entry",
                    suite_id,
                )
            declared_suite_ids.add(suite_id)
            declared_resources.add(resource)
            resource_hash = self._hash(
                entry.get("resource_hash"),
                field=f"{suite_id}.resource_hash",
            )
            assertions_hash = self._hash(
                entry.get("assertions_hash"),
                field=f"{suite_id}.assertions_hash",
            )
            required_ids = entry.get("required_case_ids")
            if (
                not isinstance(required_ids, list)
                or not required_ids
                or any(not isinstance(item, str) or not item for item in required_ids)
                or len(set(required_ids)) != len(required_ids)
            ):
                raise PackagedEvaluationSuiteError(
                    "evaluation_suite_invalid_manifest",
                    f"{suite_id}.required_case_ids",
                )

            suites.append(
                self._load_suite(
                    root=root,
                    suite_id=suite_id,
                    kind=kind,
                    resource=resource,
                    resource_hash=resource_hash,
                    assertions_hash=assertions_hash,
                    required_case_ids=set(required_ids),
                )
            )

        actual_resources = self._resource_names(root)
        if actual_resources != declared_resources:
            missing = sorted(declared_resources - actual_resources)
            extra = sorted(actual_resources - declared_resources)
            raise PackagedEvaluationSuiteError(
                "evaluation_suite_resource_set_mismatch",
                f"missing={missing!r},extra={extra!r}",
            )
        return PackagedEvaluationSuiteManifestV1(
            manifest_id=manifest_id,
            suites=tuple(suites),
        )

    def load_suite(self, suite_id: str) -> PackagedEvaluationSuiteV1:
        return self.load_manifest().suite(suite_id)

    def validate_release(self) -> PackagedEvaluationSuiteManifestV1:
        """Validate every resource and return the attested manifest."""

        return self.load_manifest()

    def _load_suite(
        self,
        *,
        root: Any,
        suite_id: str,
        kind: str,
        resource: str,
        resource_hash: str,
        assertions_hash: str,
        required_case_ids: set[str],
    ) -> PackagedEvaluationSuiteV1:
        raw, document = self._read_json(root, resource)
        if hashlib.sha256(raw).hexdigest() != resource_hash:
            raise PackagedEvaluationSuiteError(
                "evaluation_suite_resource_hash_mismatch",
                resource,
            )
        if (
            document.get("schema_version") != 1
            or document.get("suite_id") != suite_id
            or document.get("kind") != kind
        ):
            raise PackagedEvaluationSuiteError(
                "evaluation_suite_identity_mismatch",
                suite_id,
            )
        raw_cases = document.get("cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            raise PackagedEvaluationSuiteError(
                "evaluation_suite_invalid_document",
                f"{suite_id}.cases",
            )

        seen: set[str] = set()
        cases: list[PackagedEvaluationCaseV1] = []
        assertions_payload: list[dict[str, object]] = []
        for raw_case in raw_cases:
            if not isinstance(raw_case, dict):
                raise PackagedEvaluationSuiteError(
                    "evaluation_suite_invalid_document",
                    f"{suite_id}.case",
                )
            case_id = self._text(raw_case.get("case_id"), field="case_id")
            if case_id in seen:
                raise PackagedEvaluationSuiteError(
                    "evaluation_suite_duplicate_case",
                    case_id,
                )
            seen.add(case_id)
            required = raw_case.get("required")
            case_input = raw_case.get("input")
            assertions = raw_case.get("assertions")
            if (
                not isinstance(required, bool)
                or not isinstance(case_input, dict)
                or not isinstance(assertions, list)
                or not assertions
                or any(not isinstance(item, dict) or not item for item in assertions)
            ):
                raise PackagedEvaluationSuiteError(
                    "evaluation_suite_invalid_document",
                    case_id,
                )
            immutable_assertions = tuple(
                _immutable_json_object(item) for item in assertions
            )
            cases.append(
                PackagedEvaluationCaseV1(
                    case_id=case_id,
                    required=required,
                    input=_immutable_json_object(case_input),
                    assertions=immutable_assertions,
                )
            )
            assertions_payload.append(
                {
                    "case_id": case_id,
                    "assertions": assertions,
                }
            )

        actual_required_ids = {case.case_id for case in cases if case.required}
        if actual_required_ids != required_case_ids:
            raise PackagedEvaluationSuiteError(
                "evaluation_suite_required_cases_mismatch",
                suite_id,
            )
        assertions_payload.sort(key=lambda item: str(item["case_id"]))
        actual_assertions_hash = hashlib.sha256(
            _canonical_json(assertions_payload)
        ).hexdigest()
        if actual_assertions_hash != assertions_hash:
            raise PackagedEvaluationSuiteError(
                "evaluation_suite_assertions_hash_mismatch",
                suite_id,
            )
        return PackagedEvaluationSuiteV1(
            suite_id=suite_id,
            kind=kind,
            resource=resource,
            resource_hash=resource_hash,
            assertions_hash=assertions_hash,
            cases=tuple(cases),
        )


__all__ = [
    "PackagedEvaluationCaseV1",
    "PackagedEvaluationSuiteError",
    "PackagedEvaluationSuiteLoader",
    "PackagedEvaluationSuiteManifestV1",
    "PackagedEvaluationSuiteV1",
]
