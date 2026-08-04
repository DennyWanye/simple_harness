"""Quarantined import compatibility for retired internal names.

Current production modules must import canonical APIs directly. Modules in
this package contain aliases only: no state, policy, persistence, or routing.
They exist so saved integrations can migrate without teaching new code the
old architecture.
"""

__all__: list[str] = []
