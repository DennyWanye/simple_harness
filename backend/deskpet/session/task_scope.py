# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from threading import RLock
from typing import Callable, Optional


@dataclass(frozen=True)
class TaskScopeDecision:
    effective_sid: str
    created: bool
    reason: str
    stripped_text: str
    force_l2_page_in: Optional[str] = None


class TaskSessionManager:
    """In-process task-session scope resolver.

    Explicit ``/new`` creates an opaque UUID session id. ``/continue``
    stays in the base sid and marks a future L2 page-in override without
    implementing page-in here. Run/task identity remains owned by RunKernel.
    """

    def __init__(self, id_factory: Callable[[], str] | None = None) -> None:
        self._lock = RLock()
        self._active: dict[str, str] = {}
        self._peer_groups: dict[str, str] = {}
        self._parents: dict[str, str] = {}
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def resolve(
        self,
        base_sid: str,
        text: str,
        explicit_new: bool,
        force_l2: bool = False,
    ) -> TaskScopeDecision:
        base = base_sid or "default"
        root_base = self._root_base_sid(base)
        raw_text = text or ""
        stripped_new = self._strip_command(raw_text, "/new")
        stripped_continue = self._strip_command(raw_text, "/continue")
        has_new = stripped_new is not None
        has_continue = stripped_continue is not None

        if explicit_new or has_new:
            body = stripped_new if has_new else raw_text
            with self._lock:
                effective_sid = self._new_session_id()
                self._parents[effective_sid] = root_base
                self._active[root_base] = effective_sid
            return TaskScopeDecision(
                effective_sid=effective_sid,
                created=True,
                reason="explicit_new",
                stripped_text=body,
            )

        if force_l2 or has_continue:
            body = stripped_continue if has_continue else raw_text
            return TaskScopeDecision(
                effective_sid=base,
                created=False,
                reason="continue",
                stripped_text=body,
                force_l2_page_in="always",
            )

        return TaskScopeDecision(
            effective_sid=base,
            created=False,
            reason="default",
            stripped_text=raw_text,
        )

    def active_sid(self, base_sid: str) -> str:
        base = self._root_base_sid(base_sid or "default")
        with self._lock:
            return self._active.get(base, base)

    def register_peer(self, transport_sid: str) -> None:
        sid = transport_sid or "default"
        with self._lock:
            self._peer_groups.setdefault(sid, self._initial_peer_group(sid))

    def peer_group(self, transport_sid: str) -> str:
        sid = transport_sid or "default"
        with self._lock:
            return self._peer_groups.get(sid, self._initial_peer_group(sid))

    def remap_peer_group(self, transport_sid: str, effective_sid: str) -> None:
        sid = transport_sid or "default"
        target = effective_sid or sid
        with self._lock:
            old_group = self._peer_groups.get(sid, self._initial_peer_group(sid))
            self._peer_groups.setdefault(sid, old_group)
            for peer, group in list(self._peer_groups.items()):
                if group == old_group:
                    self._peer_groups[peer] = target

    def peers_for_group(self, effective_sid: str) -> list[str]:
        with self._lock:
            return [
                peer
                for peer, group in self._peer_groups.items()
                if group == effective_sid
            ]

    @staticmethod
    def _strip_command(text: str, command: str) -> str | None:
        if text == command:
            return ""
        prefix = command + " "
        if text.startswith(prefix):
            return text[len(prefix):].lstrip()
        return None

    @staticmethod
    def _initial_peer_group(transport_sid: str) -> str:
        return "default" if transport_sid in {"default", "message-panel-main"} else transport_sid

    def _new_session_id(self) -> str:
        sid = str(self._id_factory()).strip()
        return sid or str(uuid.uuid4())

    def _root_base_sid(self, base_sid: str) -> str:
        """Collapse generated task ids back to their stable parent sid.

        The frontend can legitimately send the current effective sid on a
        later "new topic" action. Without normalization, creating a new task
        from ``task-default-1`` produced ``task-task-default-1-1`` and split
        conversational history across nested task ids.
        """
        sid = base_sid or "default"
        parent = self._parents.get(sid)
        if parent:
            return parent
        if not sid.startswith("task-"):
            return sid
        match = re.fullmatch(r"task-(?P<base>.+)-(?P<seq>\d+)", sid)
        if not match:
            return sid
        base = match.group("base")
        while base.startswith("task-"):
            inner = re.fullmatch(r"task-(?P<base>.+)-(?P<seq>\d+)", base)
            if inner is None:
                break
            base = inner.group("base")
        return base or "default"


task_session_manager = TaskSessionManager()


def source_session_for_created_conversation(
    requested_session_id: object,
    resolved_base_sid: str,
) -> str | None:
    """Return a real source Session only when the client selected one.

    Empty-state ChatView turns intentionally send ``session_id: ""`` with
    ``new_session: true``. The resolved base in that case is a transport peer
    group, not a durable Session row, so it cannot own a handoff.
    """

    requested = str(requested_session_id or "").strip()
    return str(resolved_base_sid).strip() if requested else None
