# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""DeskPet Tool implementation package.

Importing this package is deliberately inert. Product composition owns the
explicit provider inventory; importing a package must never walk the file
system, execute provider modules, or silently publish a partial catalog.

The three legacy registry exports remain lazy during the 0.1.1 cutover so
older product-only call sites can be migrated independently. Merely importing
``deskpet.tools`` does not construct that legacy authority.
"""

from __future__ import annotations

from threading import RLock
from typing import Any


_LEGACY_STATIC_PROVIDER_MODULES = (
    "agent_reach_tools",
    "browser_use_tool",
    "computer_use_tool",
    "doc_tools",
    "excel_tools",
    "file_organize_tools",
    "file_tools",
    "image_tools",
    # memory_tools intentionally stays out of the legacy registry: it only
    # provides import-safe compatibility handlers for the checked SDK manifest.
    "ocr_tools",
    "pdf_tools",
    "picker_tools",
    "ppt_tools",
    "project_group_send",
    "research_tools",
    "scrapling_tools",
    "skill_tools",
    "todo_tools",
    "tool_search",
    "web_tools",
    "window_use_tool",
)


class _LazyRegistryProxy:
    """Compatibility proxy that keeps package import itself provider-pure."""

    def __init__(self) -> None:
        self._resolved: Any = None
        self._lock = RLock()

    def _target(self) -> Any:
        import importlib

        with self._lock:
            if self._resolved is not None:
                return self._resolved
            registry_module = importlib.import_module(f"{__name__}.registry")
            target = registry_module.__dict__["registry"]
            for module_name in _LEGACY_STATIC_PROVIDER_MODULES:
                module = importlib.import_module(f"{__name__}.{module_name}")
                register = getattr(module, "register_static_tools", None)
                if not callable(register):
                    raise RuntimeError(
                        f"legacy static Tool provider has no explicit registration: {module_name}"
                    )
                register(target)
            self._resolved = target
            return target

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target(), name)


registry = _LazyRegistryProxy()
_legacy_registry_proxy = registry


def __getattr__(name: str) -> Any:
    if name in {"ToolRegistry", "ToolSpec"}:
        from .registry import ToolRegistry, ToolSpec

        return {
            "ToolRegistry": ToolRegistry,
            "ToolSpec": ToolSpec,
        }[name]
    raise AttributeError(name)


__all__ = ("registry", "ToolRegistry", "ToolSpec")
