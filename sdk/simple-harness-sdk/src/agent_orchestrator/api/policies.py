# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Policy API (ORCH-BUILD §11.4): read the policy library.

The caller's authenticated identity is fixed when the object is made (``principal``); it
never comes from a model parameter or a workspace file, and no Agent tool reaches this
module.  The library is read-only from here: ``list`` and ``show`` name the versions,
``status`` is the per-version report of how the Missions bound to each version ended."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..governance.permissions import Principal
from ..orchestrator.commit_service import CommitService

TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


class PolicyRequestError(ValueError):
    """The request is refused at the API door (nothing was written)."""


class PolicyApi:
    def __init__(self, commit: CommitService, principal: Principal) -> None:
        if not isinstance(principal, Principal):
            raise PolicyRequestError("the API needs the caller's authenticated Principal")
        self._commit = commit
        self._principal = principal

    @property
    def _store(self) -> Any:
        return self._commit.store

    # ------------------------------------------------------------ reading
    def list(self) -> dict[str, Any]:
        active = self._store.active_policy()
        return {
            "active_version_id": None if active is None else active["version_id"],
            "versions": [
                {k: v.get(k) for k in ("version_id", "status", "source")}
                for v in self._store.list_policy_versions()
            ],
        }

    def show(self, identifier: str) -> dict[str, Any]:
        found = self._store.get_policy_version(identifier)
        if found is None:
            raise PolicyRequestError(f"no policy version {identifier!r}")
        return found

    def status(self) -> dict[str, Any]:
        """Per-version health (plan D9-8'): how the Missions bound to each version ended."""

        rows = []
        for version in self._store.list_policy_versions():
            bound = self._store.list_mission_policies(version["version_id"])
            ended: Counter[str] = Counter()
            for binding in bound:
                mission = self._store.get_mission(str(binding["mission_id"]))
                if mission is not None:
                    ended[str(mission.status)] += 1
            finished = sum(ended[s] for s in TERMINAL)
            rows.append(
                {
                    "version_id": version["version_id"],
                    "status": version["status"],
                    "source": version["source"],
                    "missions": len(bound),
                    "by_status": dict(ended),
                    "failure_rate": None if not finished else round(ended["FAILED"] / finished, 4),
                }
            )
        active = self._store.active_policy()
        return {
            "active_version_id": None if active is None else active["version_id"],
            "versions": rows,
            "activations": self._store.list_policy_activations(),
        }


__all__ = ("PolicyApi", "PolicyRequestError")
