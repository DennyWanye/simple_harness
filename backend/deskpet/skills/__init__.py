"""Compatibility surface for legacy, instruction-only user Skills.

Shipped Skills are immutable first-party Capability Packs. ``SkillLoader``
does not own their executable lifecycle and never launches ``script.py``.
"""

from deskpet.skills.loader import SkillLoader, SkillMeta

__all__ = ["SkillLoader", "SkillMeta"]
