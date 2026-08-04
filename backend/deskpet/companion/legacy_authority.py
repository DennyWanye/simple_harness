# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Typed adapter around the pre-Companion growth entry points.

The adapter preserves existing behavior while ensuring the composition root
has one place to connect legacy preference, codifier, and reminder callbacks.
It intentionally does not inspect feature flags or authority state; only
``GrowthAuthorityRouter`` may choose it.
"""
from __future__ import annotations

from typing import Protocol

from .authority import GrowthIngressKind, GrowthIngressRequest


class LegacyPreferenceReaderPort(Protocol):
    async def read_preference(self, request: GrowthIngressRequest) -> object: ...


class LegacyPreferenceWriterPort(Protocol):
    async def write_preference(self, request: GrowthIngressRequest) -> object: ...


class LegacyCodifierPort(Protocol):
    async def codify(self, request: GrowthIngressRequest) -> object: ...


class LegacyReminderReaderPort(Protocol):
    async def read_reminders(self, request: GrowthIngressRequest) -> object: ...


class LegacyReminderWriterPort(Protocol):
    async def write_reminder(self, request: GrowthIngressRequest) -> object: ...


class LegacyGrowthAuthorityAdapter:
    """Map typed growth ingress onto the existing legacy ports."""

    def __init__(
        self,
        *,
        preference_reader: LegacyPreferenceReaderPort,
        preference_writer: LegacyPreferenceWriterPort,
        codifier: LegacyCodifierPort,
        reminder_reader: LegacyReminderReaderPort,
        reminder_writer: LegacyReminderWriterPort,
    ) -> None:
        self._preference_reader = preference_reader
        self._preference_writer = preference_writer
        self._codifier = codifier
        self._reminder_reader = reminder_reader
        self._reminder_writer = reminder_writer

    async def read(self, request: GrowthIngressRequest) -> object:
        if request.kind is GrowthIngressKind.PREFERENCE_READ:
            return await self._preference_reader.read_preference(request)
        if request.kind is GrowthIngressKind.REMINDER_READ:
            return await self._reminder_reader.read_reminders(request)
        raise ValueError(f"{request.kind.value} is not a legacy growth read")

    async def write(self, request: GrowthIngressRequest) -> object:
        if request.kind is GrowthIngressKind.PREFERENCE_WRITE:
            return await self._preference_writer.write_preference(request)
        if request.kind is GrowthIngressKind.CODIFY:
            return await self._codifier.codify(request)
        if request.kind is GrowthIngressKind.REMINDER_WRITE:
            return await self._reminder_writer.write_reminder(request)
        raise ValueError(f"{request.kind.value} is not a legacy growth write")


__all__ = [
    "LegacyCodifierPort",
    "LegacyGrowthAuthorityAdapter",
    "LegacyPreferenceReaderPort",
    "LegacyPreferenceWriterPort",
    "LegacyReminderReaderPort",
    "LegacyReminderWriterPort",
]
