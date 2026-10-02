# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Independent Critic (§14.1 layer 3, §9.2 role Critic) as a BaseAgent call.

The Critic is its own Agent (``creation_key = <attempt_id>:critic:<n>``, D22),
sees only the verification copy through read-only tools plus the deterministic
test output, never the Worker's own explanation (§10.2), and answers with one
``<critic_verdict>`` block.  Its call is dispatched through the same intent
machinery as a Worker so it is budgeted, replayable and settled (ORCH §12.2).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_MISSION_CRITERIA_MISMATCH = "critic mission_criteria must cover the Mission criteria in order"


@dataclass(frozen=True, slots=True)
class CriticVerdict:
    verdict: str
    findings: tuple[Mapping[str, Any], ...]
    mission_criteria: tuple[Mapping[str, Any], ...]
    raw: Mapping[str, Any]
    # step 7 (D7-8'): the Critic cannot reliably judge the success condition (original §22);
    # only meaningful on a PASS — a blocker is a FAIL whatever else the Critic says
    needs_human: bool = False

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    def to_json(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "findings": [dict(item) for item in self.findings],
            "mission_criteria": [dict(item) for item in self.mission_criteria],
            "needs_human": self.needs_human,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> CriticVerdict:
        """A verdict recorded as a verification layer, read back on resume (D7-8')."""

        return cls(
            verdict=str(value.get("verdict", "FAIL")),
            findings=tuple(dict(item) for item in value.get("findings", [])),
            mission_criteria=tuple(dict(item) for item in value.get("mission_criteria", [])),
            raw=dict(value),
            needs_human=bool(value.get("needs_human", False)),
        )


__all__ = ("CriticVerdict",)
