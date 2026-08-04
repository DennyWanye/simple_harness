"""Compatibility name for the strict product projection consistency gate."""

from deskpet.agent.session_terminal_delivery import (
    SessionTerminalProjectionConsistencyGate,
)

SessionTerminalReadThrough = SessionTerminalProjectionConsistencyGate

__all__ = ["SessionTerminalReadThrough"]
