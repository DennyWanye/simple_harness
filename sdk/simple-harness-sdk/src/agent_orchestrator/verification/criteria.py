# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Content revisions of a Task's and a Mission's contract, and a criterion's wording.

What was left of the document domain's assessment module (删旧平面模式第三刀第 5 步,
strict citation option A): the pieces the budget tail revision and the action schema
still read.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..contracts import ContractError, Mission
from ..contracts.models import sha256_hex


def required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be non-empty text")
    return value

_CONTRACT_TEXT = ("task_id", "kind", "goal", "rationale")
_CONTRACT_LIST = ("success_criteria", "verification_policy", "outputs")


def _contract(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError("frozen task_contract must be an object")
    raw = {
        **value,
        "task_id": value.get("task_id", value.get("id")),
        "kind": value.get("kind", "work"),
    }
    if value.get("task_id") is not None and value.get("id") not in {None, value["task_id"]}:
        raise ContractError("task_contract has conflicting identities")
    result: dict[str, Any] = {}
    for name in _CONTRACT_TEXT:
        item = raw.get(name)
        if not isinstance(item, str) or (name != "rationale" and not item.strip()):
            raise ContractError(f"frozen task_contract is missing {name}")
        result[name] = item
    for name in _CONTRACT_LIST:
        items = raw.get(name)
        if not isinstance(items, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in items
        ):
            raise ContractError(f"frozen task_contract has invalid {name}")
        result[name] = list(items)
    return result


def task_contract_revision(contract: Mapping[str, Any]) -> str:
    return sha256_hex(_contract(contract))


def task_criterion_text(text: str, requirement_texts: Mapping[str, str]) -> str:
    """The wording a Task criterion is shown by.

    A hierarchical occurrence carries requirement ids on ``Task.success_criteria``
    (``c-user-<n>``, see ``deployment.root``); this maps one back to the user's own words
    by id, from the Mission's current requirements (``deployment.root.current_criteria``).
    Other strings are kept exactly.
    """

    return requirement_texts.get(text, text)


def mission_contract_revision(mission: Mission) -> str:
    criteria = mission.success_criteria
    if not isinstance(criteria, (list, tuple)) or any(
        not isinstance(text, str) or not text.strip() for text in criteria
    ):
        raise ContractError("invalid original Mission criteria")
    if len(set(criteria)) != len(criteria):
        raise ContractError("original Mission criteria must not contain duplicates")
    return sha256_hex(
        {
            "scope": "mission",
            "mission_id": required_text(mission.id, "mission_id"),
            "goal": required_text(mission.goal, "mission goal"),
            "success_criteria": list(criteria),
        }
    )


__all__ = ("mission_contract_revision", "task_contract_revision", "task_criterion_text")
