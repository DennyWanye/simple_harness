"""Explicit outcomes of resolving a public prospective outbox entry."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simple_harness_memory import ProspectiveInvalidationNotRequiredReceipt, RegistrationRequiredView


@dataclass(frozen=True)
class SettledNotRequired:
    receipt: ProspectiveInvalidationNotRequiredReceipt
    source_hash: str


@dataclass(frozen=True)
class RegistrationDependency:
    required: RegistrationRequiredView
