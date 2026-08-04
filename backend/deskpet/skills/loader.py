# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""SkillLoader — SKILL.md hot-reload engine (P4-S10, tasks 15.1-15.9).

Scans only directories explicitly supplied by a compatibility caller.
``SkillLoader()`` has no default source. Production Skill discovery and body
resolution use ``SkillPackSnapshotResolver`` over Manager-owned immutable
versions; this loader remains a read-only migration/test reader and never
publishes, binds, or writes a Skill.

Each ``SKILL.md`` starts with a YAML frontmatter block delimited by
``---`` lines. Required fields: ``name``, ``description``, ``version``,
``author``. Missing a required field → warn + skip (the rest of the set
keeps loading — soft failure, spec §15.2).

Runtime:

* ``reload()`` is synchronous and rescans every directory.
* ``start()`` kicks off a ``watchdog`` observer on the *user* dir with
  a 1 second debounce timer so a burst of 5 events triggers exactly one
  reload (D3: hot reload must never regress on watch failures — a
  reload crash keeps the prior skill set intact).
* discovery reload freezes each Markdown body under an immutable scope ref;
  ``execute(name, args)`` resolves that snapshot and substitutes
  ``${args[0]}``-style placeholders.
* executable ``script.py`` payloads are rejected; this loader never executes
  code owned by a Skill.

Design: this module does NOT import from ``deskpet.agent.*`` — the
skill registry stays orthogonal to the assembler. The SkillComponent
(see ``deskpet/agent/assembler/components/skill.py``) duck-types
``.select(task_type, prefer)``; we honour that contract here.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import structlog
import yaml

from deskpet.capabilities.contracts import fingerprint_json
from deskpet.capabilities.manifest import load_and_validate_pack
from deskpet.companion.skills import (
    PreparedSkillInvocationScopeV1,
    ResolvedSkillInstructionV1,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
_REQUIRED_FIELDS: tuple[str, ...] = ("name", "description", "version", "author")


@dataclass
class SkillMeta:
    """Parsed SKILL.md metadata (single skill).

    P4-S20 v1 additions (all optional with defaults so legacy callers
    are unaffected): ``when_to_use``, ``disable_model_invocation``,
    ``user_invocable``, ``allowed_tools``, ``paths``,
    ``argument_hint``, ``source_format``. ``version`` + ``author``
    keep their slots but legacy parsing now defaults them to ""
    when the frontmatter omits them — that's the v1 contract.
    """

    name: str
    description: str
    version: str
    author: str
    scope: str  # "built-in" | "user"
    path: str
    task_types: list[str] = field(default_factory=list)
    requires_script: bool = False
    # Any frontmatter keys beyond the known set land here so the UI
    # (P4-S11 MemoryPanel) can surface them without a loader change.
    meta: dict[str, Any] = field(default_factory=dict)
    # P4-S20 v1 fields
    when_to_use: str = ""
    # TC-5.1 (2026-06-11)：显式触发词表 — query 含任一触发词即视为强匹配
    # （混合匹配的 lexical 路，embedding 兜 paraphrase）。
    triggers: list[str] = field(default_factory=list)
    disable_model_invocation: bool = False
    user_invocable: bool = True
    allowed_tools: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    argument_hint: str = ""
    source_format: str = "deskpet-legacy"  # "deskpet-legacy" | "claude-code-v1"

    @property
    def summary(self) -> str:
        """Alias consumed by SkillComponent via ``_skill_attr``."""
        return self.description

    def to_dict(self) -> dict[str, Any]:
        """UI-friendly dict (task 15.9 IPC surface)."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "scope": self.scope,
            "path": self.path,
            "task_types": list(self.task_types),
            "requires_script": bool(self.requires_script),
            # meta preserves unknown keys without leaking internals.
            "meta": dict(self.meta),
            # P4-S20 v1 fields
            "when_to_use": self.when_to_use,
            "triggers": list(self.triggers),
            "disable_model_invocation": self.disable_model_invocation,
            "user_invocable": self.user_invocable,
            "allowed_tools": list(self.allowed_tools),
            "paths": list(self.paths),
            "argument_hint": self.argument_hint,
            "source_format": self.source_format,
        }


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------
def _split_frontmatter(text: str) -> tuple[Optional[dict[str, Any]], str]:
    """Return (frontmatter_dict | None, body_text).

    Accepts either ``---\\n...\\n---\\n<body>`` or a bare body (no
    frontmatter → returns ``(None, body)``). Malformed YAML raises
    ``yaml.YAMLError`` which the caller converts to a warning.
    """
    stripped = text.lstrip("\ufeff")  # BOM tolerance
    if not stripped.startswith("---"):
        return None, text
    # Locate the closing ``---`` on its own line.
    lines = stripped.splitlines(keepends=True)
    if not lines or not lines[0].strip() == "---":
        return None, text
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx == -1:
        # Unterminated frontmatter — treat as malformed.
        raise yaml.YAMLError("unterminated YAML frontmatter")
    fm_raw = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1 :])
    data = yaml.safe_load(fm_raw) or {}
    if not isinstance(data, dict):
        raise yaml.YAMLError(f"frontmatter must be a mapping, got {type(data).__name__}")
    return data, body


def _substitute_args(body: str, args: list[str]) -> str:
    """Replace ``${args[0]}`` / ``${args[1]}`` … tokens.

    Markdown bodies contain ``{`` / ``}`` freely, so ``str.format()`` is
    unsafe. We do a simple left-to-right scan for the literal token
    ``${args[N]}``. Missing indices substitute the empty string (so a
    template that expects 2 args invoked with 1 doesn't throw).
    """
    if not args:
        return body.replace("${args[", "${args[")  # no-op, keeps style
    out = body
    for i, val in enumerate(args):
        out = out.replace(f"${{args[{i}]}}", str(val))
    # Clear any remaining unfilled slots so the rendered message never
    # shows ``${args[3]}`` to the model.
    import re

    out = re.sub(r"\$\{args\[\d+\]\}", "", out)
    return out


class SkillPackSnapshotResolver:
    """Read Skill instructions/resources from an exact Manager version root.

    The caller supplies the already-frozen owner/pack/version/hash scope. This
    resolver never asks for a live binding and never executes pack code.
    """

    manager_backed = True

    def __init__(
        self,
        *,
        version_store: Any,
        selection_source: Any | None = None,
    ) -> None:
        self._version_store = version_store
        self._selection_source = selection_source

    def resolve_selection(
        self, name: str
    ) -> PreparedSkillInvocationScopeV1:
        """Freeze one new-Turn name through the injected owner projection."""

        if self._selection_source is None:
            raise RuntimeError(
                "manager_skill_selection_source_unavailable"
            )
        resolve = getattr(
            self._selection_source, "resolve_selection", None
        )
        if not callable(resolve):
            raise TypeError(
                "manager skill selection source has no typed resolver"
            )
        scope = resolve(name)
        if not isinstance(scope, PreparedSkillInvocationScopeV1):
            raise TypeError("manager skill selection is not frozen")
        return scope

    async def resolve_named_instruction(
        self,
        name: str,
        arguments: tuple[str, ...] = (),
    ) -> ResolvedSkillInstructionV1:
        """Resolve ``/<skill>`` without giving name lookup body authority."""

        return await self.resolve_instruction(
            self.resolve_selection(name),
            arguments,
        )

    async def execute(
        self, name: str, args: Optional[list[str]] = None
    ) -> str:
        """Compatibility command surface backed only by Manager records."""

        frozen = await self.resolve_instruction(self.resolve_selection(name))
        return _substitute_args(frozen.instruction, list(args or ()))

    async def resolve_scope(
        self,
        *,
        owner_key: str,
        pack_id: str,
        skill_id: str,
        version: str,
        manifest_hash: str,
    ) -> PreparedSkillInvocationScopeV1:
        """Build the frozen Skill scope from one exact catalog pack ref."""

        root, manifest = await self._resolve_record_root(
            pack_id,
            version,
            manifest_hash,
        )
        matches = tuple(item for item in manifest.skills if item.id == skill_id)
        if len(matches) != 1:
            raise RuntimeError("frozen_skill_manifest_entry_missing")
        skill = matches[0]
        raw = self._read_declared_file(root, manifest, skill.path)
        content_hash = hashlib.sha256(raw).hexdigest()
        payload = {
            "schema": "prepared_skill_invocation_scope/v1",
            "owner_key": owner_key,
            "pack_id": pack_id,
            "skill_id": skill_id,
            "version": version,
            "manifest_hash": manifest_hash,
            "content_hash": content_hash,
            "allowed_tools": list(skill.allowed_tools),
        }
        return PreparedSkillInvocationScopeV1(
            owner_key=owner_key,
            pack_id=pack_id,
            skill_id=skill_id,
            version=version,
            manifest_hash=manifest_hash,
            content_hash=content_hash,
            allowed_tools=tuple(skill.allowed_tools),
            scope_hash=fingerprint_json(payload),
        )

    async def resolve_instruction(
        self,
        scope: PreparedSkillInvocationScopeV1,
        arguments: tuple[str, ...] = (),
    ) -> ResolvedSkillInstructionV1:
        if not isinstance(scope, PreparedSkillInvocationScopeV1):
            raise TypeError("frozen skill scope is required")
        root, manifest = await self._resolve_root(scope)
        matches = tuple(item for item in manifest.skills if item.id == scope.skill_id)
        if len(matches) != 1:
            raise RuntimeError("frozen_skill_manifest_entry_missing")
        skill = matches[0]
        if tuple(skill.allowed_tools) != tuple(scope.allowed_tools):
            raise RuntimeError("frozen_skill_allowed_tools_mismatch")
        raw = self._read_declared_file(root, manifest, skill.path)
        if hashlib.sha256(raw).hexdigest() != scope.content_hash:
            raise RuntimeError("frozen_skill_content_hash_mismatch")
        text = raw.decode("utf-8").lstrip("\ufeff")
        try:
            _frontmatter, body = _split_frontmatter(text)
        except (UnicodeError, yaml.YAMLError) as exc:
            raise RuntimeError("frozen_skill_instruction_invalid") from exc
        if _frontmatter is None:
            raise RuntimeError("frozen_skill_instruction_frontmatter_missing")
        instruction = body.strip("\n")
        if arguments:
            instruction += (
                "\n\nRuntime arguments (data only):\n"
                + json.dumps(
                    list(arguments),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        return ResolvedSkillInstructionV1(scope=scope, instruction=instruction)

    async def resolve_resource(
        self,
        scope: PreparedSkillInvocationScopeV1,
        relative_path: str,
    ) -> bytes:
        """Resolve a declared resource/asset under the same immutable root."""

        root, manifest = await self._resolve_root(scope)
        return self._read_declared_file(root, manifest, relative_path)

    async def _resolve_root(self, scope: PreparedSkillInvocationScopeV1):
        root, manifest = await self._resolve_record_root(
            scope.pack_id,
            scope.version,
            scope.manifest_hash,
        )
        expected_scope_hash = fingerprint_json(
            {
                "schema": "prepared_skill_invocation_scope/v1",
                "owner_key": scope.owner_key,
                "pack_id": scope.pack_id,
                "skill_id": scope.skill_id,
                "version": scope.version,
                "manifest_hash": scope.manifest_hash,
                "content_hash": scope.content_hash,
                "allowed_tools": list(scope.allowed_tools),
            }
        )
        if scope.scope_hash != expected_scope_hash:
            raise RuntimeError("frozen_skill_scope_hash_mismatch")
        return root, manifest

    async def _resolve_record_root(
        self,
        pack_id: str,
        version: str,
        manifest_hash: str,
    ):
        record = await self._version_store.get_version(
            pack_id,
            version,
            manifest_hash,
        )
        if record is None:
            raise RuntimeError("frozen_skill_pack_version_missing")
        descriptor = record.descriptor
        if (
            str(descriptor.capability_id) != pack_id
            or str(descriptor.version) != version
            or str(descriptor.manifest_hash) != manifest_hash
        ):
            raise RuntimeError("frozen_skill_pack_record_mismatch")
        root = Path(record.install_path).resolve(strict=True)
        result = load_and_validate_pack(root)
        manifest = result.manifest
        if (
            manifest.id != pack_id
            or manifest.version != version
            or manifest.manifest_hash != manifest_hash
        ):
            raise RuntimeError("frozen_skill_pack_manifest_mismatch")
        return root, manifest

    @staticmethod
    def _read_declared_file(root: Path, manifest: Any, relative_path: str) -> bytes:
        declared = {
            str(item.path).replace("\\", "/"): str(item.sha256)
            for item in manifest.files
        }
        normalized = str(relative_path).replace("\\", "/").lstrip("/")
        expected_hash = declared.get(normalized)
        if expected_hash is None:
            raise RuntimeError("frozen_skill_resource_not_declared")
        path = (root / normalized).resolve(strict=True)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise RuntimeError("frozen_skill_resource_outside_pack") from exc
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            raise RuntimeError("frozen_skill_resource_hash_mismatch")
        return raw


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
class SkillLoader:
    # Explicit compatibility/migration reader only. It has no default source
    # and no mutation or publication surface. Production composition injects
    # SkillPackSnapshotResolver instead.
    manager_backed = False
    legacy_no_manager_resolver = True
    legacy_read_only_source = True
    """Scans + watches SKILL.md directories. Public API (spec §15)::

        loader = SkillLoader([built_in_dir, user_dir])
        await loader.start()
        metas = loader.list_skills()
        txt = await loader.execute("recall-yesterday")
        await loader.stop()

    Thread-safety:
      ``reload()`` holds an internal lock so concurrent watchdog events
      and explicit ``list_skills()`` callers see a consistent snapshot.
    """

    def __init__(
        self,
        skill_dirs: Optional[list[Path]] = None,
        *,
        skill_scopes: Optional[list[str]] = None,
        enable_watch: bool = True,
        script_timeout_s: float = 10.0,
        debounce_s: float = 1.0,
        tool_registry: Any = None,
        knowledge_enabled: bool = False,
    ) -> None:
        self._dirs: list[Path] = [Path(d) for d in (skill_dirs or ())]
        if skill_scopes is not None:
            if len(skill_scopes) != len(self._dirs):
                raise ValueError("skill_scopes must align with skill_dirs")
            if any(item not in {"built-in", "user"} for item in skill_scopes):
                raise ValueError("skill_scopes accepts only built-in or user")
            self._scopes = list(skill_scopes)
        else:
            self._scopes = (
                ["built-in", *("user" for _ in self._dirs[1:])]
                if self._dirs
                else []
            )
        self._enable_watch = enable_watch
        self._script_timeout_s = float(script_timeout_s)
        self._debounce_s = float(debounce_s)
        self._tool_registry = tool_registry
        self._knowledge_enabled = bool(knowledge_enabled)
        # Scope inference: index 0 is built-in, index 1+ is user. The
        # first dir containing the path wins.
        self._lock = threading.Lock()
        self._skills: dict[str, SkillMeta] = {}  # name → meta
        self._current_scopes: dict[str, PreparedSkillInvocationScopeV1] = {}
        self._instruction_snapshots: dict[
            tuple[str, str, str, str, str], str
        ] = {}
        self._observer: Any = None
        self._debounce_timer: Optional[threading.Timer] = None
        self._started = False
        # Tool registry integration — register once per loader so names
        # re-bound on reload always dispatch to the live body.

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Initial scan + watchdog install on the user dir."""
        if self._started:
            return
        self._started = True
        self.reload()
        if self._enable_watch and len(self._dirs) >= 2:
            self._install_watchdog()

    async def stop(self) -> None:
        """Cancel timer, halt observer, join its thread."""
        if not self._started:
            return
        self._started = False
        if self._debounce_timer is not None:
            try:
                self._debounce_timer.cancel()
            except Exception:
                pass
            self._debounce_timer = None
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("skill.observer_stop_error", error=str(exc))
            self._observer = None

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------
    def reload(self) -> None:
        """Re-scan every directory; swap the cache atomically.

        On per-file failure we log and continue so a single bad SKILL.md
        never wedges the loader. On a catastrophic exception during the
        merge we preserve the previous cache (D3).
        """
        new_map: dict[str, SkillMeta] = {}
        try:
            for idx, d in enumerate(self._dirs):
                scope = self._scopes[idx]
                if not d.exists():
                    continue
                for skill_dir in sorted(p for p in d.iterdir() if p.is_dir()):
                    skill_md = skill_dir / "SKILL.md"
                    if not skill_md.is_file():
                        continue
                    meta = self._load_single(skill_md, scope=scope)
                    if meta is None:
                        continue
                    if meta.requires_script or (skill_dir / "script.py").is_file():
                        logger.warning(
                            "skill.executable_content_rejected",
                            name=meta.name,
                            path=str(skill_dir),
                        )
                        continue
                    # WI-5: `user-invocable: false` bundles are background
                    # knowledge snippets, not user-callable skills. Keep them
                    # entirely out of the loader snapshot unless the explicit
                    # knowledge flag is on, preserving flag-off BC for desc
                    # lists, /help surfaces, and skill_invoke.
                    if not self._knowledge_enabled and not meta.user_invocable:
                        continue
                    # Tie-break: user scope wins over built-in. We iterate
                    # built-in first, so when a later "user" entry arrives
                    # with the same name we overwrite.
                    prior = new_map.get(meta.name)
                    if prior is not None and prior.scope == "user" and scope != "user":
                        continue
                    new_map[meta.name] = meta
        except Exception as exc:  # noqa: BLE001 — preserve prior snapshot
            logger.warning(
                "skill.reload_crashed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return

        new_scopes: dict[str, PreparedSkillInvocationScopeV1] = {}
        new_instructions: dict[tuple[str, str, str, str, str], str] = {}
        try:
            for name, meta in new_map.items():
                raw = Path(meta.path).read_bytes()
                content_hash = hashlib.sha256(raw).hexdigest()
                text = raw.decode("utf-8").lstrip("\ufeff")
                frontmatter, body = _split_frontmatter(text)
                if frontmatter is None:
                    raise RuntimeError("frozen_skill_frontmatter_missing")
                owner_key = "builtin" if meta.scope == "built-in" else "user"
                pack_id = f"legacy-skill-{name}"
                version = meta.version or "0"
                manifest_hash = fingerprint_json(
                    {
                        "schema": "legacy-skill-discovery/v1",
                        "owner_key": owner_key,
                        "pack_id": pack_id,
                        "name": name,
                        "version": version,
                        "content_hash": content_hash,
                        "allowed_tools": list(meta.allowed_tools),
                    }
                )
                scope_hash = fingerprint_json(
                    {
                        "schema": "prepared_skill_invocation_scope/v1",
                        "owner_key": owner_key,
                        "pack_id": pack_id,
                        "skill_id": name,
                        "version": version,
                        "manifest_hash": manifest_hash,
                        "content_hash": content_hash,
                        "allowed_tools": list(meta.allowed_tools),
                    }
                )
                scope = PreparedSkillInvocationScopeV1(
                    owner_key=owner_key,
                    pack_id=pack_id,
                    skill_id=name,
                    version=version,
                    manifest_hash=manifest_hash,
                    content_hash=content_hash,
                    allowed_tools=tuple(meta.allowed_tools),
                    scope_hash=scope_hash,
                )
                key = (
                    owner_key,
                    pack_id,
                    version,
                    manifest_hash,
                    content_hash,
                )
                new_scopes[name] = scope
                new_instructions[key] = body.strip("\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "skill.freeze_snapshot_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return

        with self._lock:
            self._skills = new_map
            self._current_scopes = new_scopes
            # Append-only: an older Run may still hold an older exact ref.
            self._instruction_snapshots.update(new_instructions)
        logger.info("skill.reload_ok", count=len(new_map))

    def _load_single(self, skill_md: Path, *, scope: str) -> Optional[SkillMeta]:
        try:
            text = skill_md.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill.read_failed", path=str(skill_md), error=str(exc))
            return None
        try:
            fm, _body = _split_frontmatter(text)
        except yaml.YAMLError as exc:
            logger.warning(
                "skill.invalid_frontmatter",
                path=str(skill_md),
                error=str(exc),
            )
            return None
        if fm is None:
            logger.warning("skill.invalid_frontmatter", path=str(skill_md), error="missing")
            return None

        # P4-S20: dispatch by frontmatter shape.
        # - Has both `version` AND `author` → legacy DeskPet skill format
        #   (strict required-field validation kept for backward compat)
        # - Else → Claude Code v1 (only `description` required;
        #   `name` defaults to directory name)
        is_legacy = bool(fm.get("version")) and bool(fm.get("author"))
        if is_legacy:
            return self._load_single_legacy(fm, skill_md, scope)
        return self._load_single_v1(skill_md, scope)

    # -----------------------------------------------------------------
    # Legacy (DeskPet built-in) format loader
    # -----------------------------------------------------------------
    def _load_single_legacy(
        self, fm: dict[str, Any], skill_md: Path, scope: str
    ) -> Optional[SkillMeta]:
        missing = [f for f in _REQUIRED_FIELDS if not fm.get(f)]
        if missing:
            logger.warning(
                "skill.invalid_frontmatter",
                path=str(skill_md),
                error=f"missing required fields: {missing}",
            )
            return None
        known = set(_REQUIRED_FIELDS) | {
            "task_types", "requires_script", "when_to_use", "triggers"
        }
        extra = {k: v for k, v in fm.items() if k not in known}
        task_types = fm.get("task_types") or []
        if not isinstance(task_types, list):
            task_types = []
        triggers = fm.get("triggers") or []
        if not isinstance(triggers, list):
            triggers = []
        return SkillMeta(
            name=str(fm["name"]),
            description=str(fm["description"]),
            version=str(fm["version"]),
            author=str(fm["author"]),
            scope=scope,
            path=str(skill_md),
            task_types=[str(t) for t in task_types],
            requires_script=bool(fm.get("requires_script", False)),
            meta=extra,
            # TC-5.1 (2026-06-11)：legacy 格式原本不解析 when_to_use → builtin
            # skill 的 embedding 文本只有 description，短中文 query 相似度区分
            # 度差(on-target 0.45~0.55 vs off-target 0.57)。提升到一等字段,
            # SkillMatcher embed description+when_to_use。
            when_to_use=str(fm.get("when_to_use", "") or ""),
            triggers=[str(t) for t in triggers if str(t).strip()],
            source_format="deskpet-legacy",
        )

    # -----------------------------------------------------------------
    # Claude Code v1 format loader (delegates to parser package)
    # -----------------------------------------------------------------
    def _load_single_v1(
        self, skill_md: Path, scope: str
    ) -> Optional[SkillMeta]:
        try:
            from .parser import parse_skill_md, SkillParseError
        except ImportError as exc:
            logger.warning("skill.parser_missing", path=str(skill_md), error=str(exc))
            return None
        try:
            cm = parse_skill_md(skill_md)
        except SkillParseError as exc:
            logger.warning(
                "skill.invalid_frontmatter",
                path=str(skill_md),
                error=str(exc),
            )
            return None
        raw_triggers = cm.raw_frontmatter.get("triggers") or []
        if not isinstance(raw_triggers, list):
            raw_triggers = []
        return SkillMeta(
            name=cm.name,
            description=cm.description,
            version=cm.version or "",
            author="",  # not in v1 spec
            scope=scope,
            path=cm.path,
            task_types=[],
            requires_script=False,
            meta=dict(cm.raw_frontmatter),
            when_to_use=cm.when_to_use,
            triggers=[str(t) for t in raw_triggers if str(t).strip()],
            disable_model_invocation=cm.disable_model_invocation,
            user_invocable=cm.user_invocable,
            allowed_tools=list(cm.allowed_tools),
            paths=list(cm.paths),
            argument_hint=cm.argument_hint,
            source_format="claude-code-v1",
        )

    # ------------------------------------------------------------------
    # Query surface
    # ------------------------------------------------------------------
    def list_skills(self) -> list[dict[str, Any]]:
        """IPC-friendly dict list — task 15.9."""
        with self._lock:
            metas = list(self._skills.values())
        metas.sort(key=lambda m: (m.scope, m.name))
        return [m.to_dict() for m in metas]

    def list_metas(self) -> list[SkillMeta]:
        """Sorted :class:`SkillMeta` objects (internal/assembler use)."""
        with self._lock:
            metas = list(self._skills.values())
        metas.sort(key=lambda m: (m.scope, m.name))
        return metas

    def all(self) -> list[SkillMeta]:
        """Compat alias — SkillComponent calls ``.all()`` when there's no
        ``.select()``. We provide both; component prefers select()."""
        return self.list_metas()

    def get(self, name: str) -> Optional[SkillMeta]:
        with self._lock:
            return self._skills.get(name)

    def resolve_selection(self, name: str) -> PreparedSkillInvocationScopeV1:
        """Return the current immutable selection for a new Turn."""

        with self._lock:
            scope = self._current_scopes.get(name)
        if scope is None:
            raise KeyError(name)
        return scope

    def resolve_instruction(
        self,
        scope: PreparedSkillInvocationScopeV1,
        arguments: tuple[str, ...] = (),
    ) -> ResolvedSkillInstructionV1:
        """Resolve an append-only Loader snapshot by exact identity."""

        key = (
            scope.owner_key,
            scope.pack_id,
            scope.version,
            scope.manifest_hash,
            scope.content_hash,
        )
        with self._lock:
            body = self._instruction_snapshots.get(key)
        if body is None:
            raise KeyError(scope.skill_id)
        expected_hash = fingerprint_json(
            {
                "schema": "prepared_skill_invocation_scope/v1",
                "owner_key": scope.owner_key,
                "pack_id": scope.pack_id,
                "skill_id": scope.skill_id,
                "version": scope.version,
                "manifest_hash": scope.manifest_hash,
                "content_hash": scope.content_hash,
                "allowed_tools": list(scope.allowed_tools),
            }
        )
        if expected_hash != scope.scope_hash:
            raise RuntimeError("frozen_skill_scope_hash_mismatch")
        instruction = body
        if arguments:
            instruction += (
                "\n\nRuntime arguments (data only):\n"
                + json.dumps(
                    list(arguments),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        return ResolvedSkillInstructionV1(scope=scope, instruction=instruction)

    def select(
        self, task_type: str, prefer: Optional[list[str]] = None
    ) -> list[SkillMeta]:
        """Duck-type contract expected by SkillComponent.

        Selection rules (simpler than the full policy proposal — we
        don't touch policy YAML for S10):

          1. If ``prefer`` contains ``"skill:NAME"`` entries → those
             named skills (if loaded) come out first.
          2. Then skills whose ``task_types`` frontmatter list contains
             ``task_type`` get added (dedup preserved).
          3. Unknown task type + no name-based prefer → empty list so
             the assembler doesn't leak random skills into chat turns.
        """
        prefer = list(prefer or [])
        with self._lock:
            snapshot = dict(self._skills)

        ordered: list[SkillMeta] = []
        seen: set[str] = set()

        # 1. Explicit skill:name preferences (P4-S11 will wire these
        #    through policy YAML; for now they can arrive via
        #    prefer=[...]).
        for p in prefer:
            if not isinstance(p, str) or not p.startswith("skill:"):
                continue
            name = p.split(":", 1)[1].strip()
            meta = snapshot.get(name)
            if meta is not None and meta.name not in seen:
                ordered.append(meta)
                seen.add(meta.name)

        # 2. task_types match.
        for meta in snapshot.values():
            if meta.name in seen:
                continue
            if task_type and task_type in meta.task_types:
                ordered.append(meta)
                seen.add(meta.name)

        return ordered

    # ------------------------------------------------------------------
    # Body read (WI-4.1 — no arg substitution)
    # ------------------------------------------------------------------
    def read_body(self, name: str) -> str:
        """Return the raw body of a skill's SKILL.md **without** any
        ``${args[N]}`` substitution.

        Unlike ``execute()``, this is synchronous and does **not** perform
        argument substitution — intended for the auto-disclosure path where
        the body is inlined verbatim into the context prelude.

        Raises :class:`KeyError` if the skill is unknown.
        """
        scope = self.resolve_selection(name)
        return self.resolve_instruction(scope).instruction

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    async def execute(
        self, name: str, args: Optional[list[str]] = None
    ) -> str:
        """Render a skill's body with ``${args[N]}`` substitution.

        Returns the body text. Caller wraps as a ``user`` message.
        Unknown name → raises :class:`KeyError`; the ``skill_invoke``
        tool handler converts that into an error payload.
        """
        scope = self.resolve_selection(name)
        frozen = self.resolve_instruction(scope)
        return _substitute_args(frozen.instruction, list(args or []))

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------
    def _install_watchdog(self) -> None:
        user_dir = self._dirs[-1]  # user dir is the last entry
        try:
            user_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill.user_dir_mkdir_failed", error=str(exc))
            return
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill.watchdog_unavailable", error=str(exc))
            return

        loader = self

        class _Handler(FileSystemEventHandler):  # type: ignore[misc]
            def on_any_event(self, event: Any) -> None:  # noqa: D401
                # All events bounce through the debounce timer — we
                # never call ``reload()`` inline from the observer
                # thread to avoid blocking it on slow I/O.
                loader._schedule_debounced_reload()

        observer = Observer()
        observer.schedule(_Handler(), str(user_dir), recursive=True)
        observer.daemon = True
        try:
            observer.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill.observer_start_failed", error=str(exc))
            return
        self._observer = observer

    def _schedule_debounced_reload(self) -> None:
        """Coalesce bursts of events into one reload.

        Each call cancels the prior pending timer; the actual reload
        only runs ``debounce_s`` seconds after the LAST event.
        """
        with self._lock:
            if self._debounce_timer is not None:
                try:
                    self._debounce_timer.cancel()
                except Exception:
                    pass
            t = threading.Timer(self._debounce_s, self._on_debounce_fire)
            t.daemon = True
            self._debounce_timer = t
            t.start()

    def _on_debounce_fire(self) -> None:
        try:
            self.reload()
        except Exception as exc:  # noqa: BLE001 — keep prior cache
            logger.warning(
                "skill.debounce_reload_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # Test hook: trigger the debounce path directly without waiting
    # for a real filesystem event.
    def _fire_event_for_test(self) -> None:
        self._schedule_debounced_reload()

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------
    @property
    def skill_dirs(self) -> list[Path]:
        return list(self._dirs)

__all__ = [
    "SkillPackSnapshotResolver",
    "SkillLoader",
    "SkillMeta",
]
