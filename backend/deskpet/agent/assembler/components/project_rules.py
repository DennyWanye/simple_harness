# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Path-scoped project rule loading for code/workspace contexts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from deskpet.agent.assembler.bundle import ContextFragment, Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext
from deskpet.agent.tokens import count_text_tokens


_RULE_FILES = ("AGENTS.md", "AGENTS", "rules.md")
_RULE_DIRS = (".deskpet/rules", ".claude/rules", "rules")
_RULE_SUFFIXES = frozenset({".md", ".txt"})
_TRIM_MARKER = "\n[…project rules truncated]"


@dataclass(frozen=True)
class _WorkspaceSelection:
    root: Path
    active: Path


@dataclass(frozen=True)
class _RuleCandidate:
    path: Path
    relative_path: str
    depth: int


@dataclass(frozen=True)
class _RuleFile:
    text: str
    sha256: str
    byte_truncated: bool


class ProjectRulesComponent:
    """Load bounded hierarchical rules without trusting prompt paths."""

    name: str = "project_rules"

    async def provide(self, ctx: ComponentContext) -> Slice:
        # This branch must stay ahead of every Path/filesystem operation.
        if ctx.task_type not in {"code", "workspace"}:
            return self._empty("scope_ineligible")

        try:
            from deskpet.context_os_e2e_hooks import consume_context_os_e2e_fault

            if consume_context_os_e2e_fault("project_rules_io_error"):
                return self._empty("read_failed:FixtureProjectRulesIOError")
        except RuntimeError as exc:
            return self._empty(str(exc))

        selection, error = _select_verified_workspace(ctx.config)
        if selection is None:
            return self._empty(error or "workspace_unverified")

        rules_config = _mapping(ctx.config.get("project_rules"))
        max_chars = _bounded_int(rules_config.get("max_chars"), 12_000, 0, 200_000)
        max_tokens = _bounded_int(rules_config.get("max_tokens"), 3_000, 0, 50_000)
        max_files = _bounded_int(rules_config.get("max_files"), 32, 0, 256)
        max_file_bytes = _bounded_int(
            rules_config.get("max_file_bytes"), 256_000, 1, 4_000_000
        )
        if max_chars == 0 or max_tokens == 0 or max_files == 0:
            return self._empty(
                "budget_disabled",
                workspace_root=str(selection.root),
                active_path=str(selection.active),
            )

        candidates, rejected = _discover_rule_files(selection)
        if not candidates:
            return self._empty(
                "no_matching_rules",
                workspace_root=str(selection.root),
                active_path=str(selection.active),
                rejected_sources=rejected,
            )

        remaining_chars = max_chars
        remaining_tokens = max_tokens
        fragments: list[ContextFragment] = []
        sources: list[dict[str, Any]] = []
        omitted: list[dict[str, str]] = []

        for candidate in candidates:
            if len(fragments) >= max_files:
                omitted.append(
                    {"path": candidate.relative_path, "reason": "max_files"}
                )
                continue
            if remaining_chars <= 0 or remaining_tokens <= 0:
                omitted.append(
                    {"path": candidate.relative_path, "reason": "budget_exhausted"}
                )
                continue
            if ctx.time_remaining_ms() is not None and ctx.time_remaining_ms() <= 0:
                omitted.append(
                    {"path": candidate.relative_path, "reason": "component_deadline"}
                )
                continue

            try:
                loaded = _read_rule_file(candidate.path, max_file_bytes=max_file_bytes)
            except OSError as exc:
                omitted.append(
                    {
                        "path": candidate.relative_path,
                        "reason": f"read_failed:{type(exc).__name__}",
                    }
                )
                continue

            header = f"# Project rules: {candidate.relative_path}\n"
            rendered, budget_truncated = _fit_text(
                header + loaded.text,
                max_chars=remaining_chars,
                max_tokens=remaining_tokens,
            )
            if not rendered:
                omitted.append(
                    {"path": candidate.relative_path, "reason": "budget_exhausted"}
                )
                continue

            tokens = count_text_tokens(rendered)
            remaining_chars -= len(rendered)
            remaining_tokens -= tokens
            path_hash = hashlib.sha256(
                candidate.relative_path.encode("utf-8")
            ).hexdigest()[:10]
            source = f"project_rules:{candidate.relative_path}"
            file_meta = {
                "path": candidate.relative_path,
                "sha256": loaded.sha256,
                "depth": candidate.depth,
                "byte_truncated": loaded.byte_truncated,
                "budget_truncated": budget_truncated,
            }
            fragments.append(
                ContextFragment(
                    fragment_id=(
                        f"project_rules:{path_hash}:{loaded.sha256[:16]}"
                    ),
                    source=source,
                    role="system",
                    content=rendered,
                    lifetime="stable",
                    placement="prefix",
                    # A nearer rule has a larger keep-priority and is emitted first.
                    priority=80 + min(candidate.depth, 15),
                    trim_policy="truncate",
                    protected=True,
                    reason="matched_verified_active_path",
                    cache_scope=f"workspace:{_workspace_hash(selection.root)}",
                    meta=file_meta,
                )
            )
            sources.append(file_meta)

        if not fragments:
            return self._empty(
                "rules_unreadable_or_over_budget",
                workspace_root=str(selection.root),
                active_path=str(selection.active),
                rejected_sources=rejected,
                omitted=omitted,
            )

        return Slice(
            component_name=self.name,
            tokens=sum(count_text_tokens(str(fragment.content)) for fragment in fragments),
            priority=max(fragment.priority for fragment in fragments),
            bucket="frozen",
            fragments=fragments,
            meta={
                "status": "loaded",
                "reason": "matched_verified_active_path",
                "workspace_root": str(selection.root),
                "active_path": str(selection.active),
                "sources": sources,
                "rejected_sources": rejected,
                "omitted": omitted,
                "max_chars": max_chars,
                "max_tokens": max_tokens,
                "remaining_chars": remaining_chars,
                "remaining_tokens": remaining_tokens,
            },
        )

    def _empty(self, reason: str, **meta: Any) -> Slice:
        return Slice(
            component_name=self.name,
            priority=80,
            bucket="frozen",
            meta={"status": "empty", "reason": reason, **meta},
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _select_verified_workspace(
    config: Mapping[str, Any],
) -> tuple[_WorkspaceSelection | None, str | None]:
    workspace_context = _mapping(config.get("workspace_context"))
    if workspace_context:
        if workspace_context.get("verified") is not True:
            return None, "workspace_unverified"
        root_value = workspace_context.get("root") or workspace_context.get(
            "workspace_root"
        )
        active_value = workspace_context.get("active_path") or root_value
    else:
        # ``code_mode.project_root`` is populated by the host CodeModeManager,
        # not parsed from user text.  An optional active path needs a separate
        # host verification bit; otherwise only root-level rules are loaded.
        code_mode = _mapping(config.get("code_mode"))
        if code_mode.get("enabled") is not True or not code_mode.get("project_root"):
            return None, "workspace_unverified"
        root_value = code_mode.get("project_root")
        active_value = (
            code_mode.get("active_path")
            if code_mode.get("active_path_verified") is True
            else root_value
        )

    try:
        root = Path(str(root_value)).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None, "workspace_root_missing"
    if not root.is_dir():
        return None, "workspace_root_not_directory"

    active_candidate = Path(str(active_value)).expanduser()
    if not active_candidate.is_absolute():
        active_candidate = root / active_candidate
    try:
        active = active_candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None, "active_path_missing"
    active_dir = active if active.is_dir() else active.parent
    if not _is_within(active_dir, root):
        return None, "active_path_outside_workspace"
    return _WorkspaceSelection(root=root, active=active), None


def _discover_rule_files(
    selection: _WorkspaceSelection,
) -> tuple[list[_RuleCandidate], list[dict[str, str]]]:
    active_dir = selection.active if selection.active.is_dir() else selection.active.parent
    relative = active_dir.relative_to(selection.root)
    ancestors = [selection.root]
    current = selection.root
    for part in relative.parts:
        current = current / part
        ancestors.append(current)

    found: list[_RuleCandidate] = []
    rejected: list[dict[str, str]] = []
    seen: set[Path] = set()
    for depth, directory in reversed(list(enumerate(ancestors))):
        direct_candidates = [directory / name for name in _RULE_FILES]
        directory_candidates: list[Path] = []
        for rule_dir_name in _RULE_DIRS:
            rule_dir = directory / rule_dir_name
            try:
                resolved_dir = rule_dir.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not resolved_dir.is_dir() or not _is_within(resolved_dir, selection.root):
                if rule_dir.exists() or rule_dir.is_symlink():
                    rejected.append(
                        {
                            "path": _display_path(rule_dir, selection.root),
                            "reason": "outside_workspace",
                        }
                    )
                continue
            try:
                entries = sorted(
                    (
                        entry
                        for entry in resolved_dir.iterdir()
                        if entry.suffix.lower() in _RULE_SUFFIXES
                    ),
                    key=lambda entry: entry.name.casefold(),
                )
            except OSError:
                entries = []
            directory_candidates.extend(entries)

        for candidate in [*direct_candidates, *directory_candidates]:
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not resolved.is_file() or not _is_within(resolved, selection.root):
                if candidate.exists() or candidate.is_symlink():
                    rejected.append(
                        {
                            "path": _display_path(candidate, selection.root),
                            "reason": "outside_workspace",
                        }
                    )
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(
                _RuleCandidate(
                    path=resolved,
                    relative_path=resolved.relative_to(selection.root).as_posix(),
                    depth=depth,
                )
            )
    return found, rejected


def _read_rule_file(path: Path, *, max_file_bytes: int) -> _RuleFile:
    digest = hashlib.sha256()
    captured = bytearray()
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
            if len(captured) < max_file_bytes:
                captured.extend(chunk[: max_file_bytes - len(captured)])
    return _RuleFile(
        text=bytes(captured).decode("utf-8", errors="replace"),
        sha256=digest.hexdigest(),
        byte_truncated=total > max_file_bytes,
    )


def _fit_text(text: str, *, max_chars: int, max_tokens: int) -> tuple[str, bool]:
    if not text or max_chars <= 0 or max_tokens <= 0:
        return "", bool(text)
    if len(text) <= max_chars and count_text_tokens(text) <= max_tokens:
        return text, False
    if len(_TRIM_MARKER) > max_chars or count_text_tokens(_TRIM_MARKER) > max_tokens:
        return "", True

    high = max(0, min(len(text), max_chars - len(_TRIM_MARKER)))
    low = 0
    while low < high:
        midpoint = (low + high + 1) // 2
        candidate = text[:midpoint].rstrip() + _TRIM_MARKER
        if len(candidate) <= max_chars and count_text_tokens(candidate) <= max_tokens:
            low = midpoint
        else:
            high = midpoint - 1
    if low == 0:
        return "", True
    return text[:low].rstrip() + _TRIM_MARKER, True


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _workspace_hash(root: Path) -> str:
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]


_ASSERT_PROTOCOL: Component = ProjectRulesComponent()


__all__ = ["ProjectRulesComponent"]
