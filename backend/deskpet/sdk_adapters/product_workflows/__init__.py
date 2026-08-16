# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Product-owned handlers used by SDK Workflow definitions.

This package deliberately has no dependency on ``deskpet.workflows``.  The SDK
owns graph execution; DeskPet owns the physical LLM/search/artifact boundaries.
"""

from .research_ports import ResearchPorts

__all__ = ("ResearchPorts",)
