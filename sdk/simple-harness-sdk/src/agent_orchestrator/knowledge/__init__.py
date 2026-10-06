# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Predicates, justifications and evidence validity (plan §18.1 ``knowledge/``)."""

from . import justifications, predicates
from .justifications import (
    Anchor,
    AnchorSelector,
    Atom,
    ClosureResult,
    ClosureStatus,
    JustificationSet,
    Polarity,
    SupportGraph,
    grounded_closure,
)
from .predicates import PredicateRegistry, PredicateSignature, WorldAssumption, proposition_key

__all__ = (
    "Anchor",
    "AnchorSelector",
    "Atom",
    "ClosureResult",
    "ClosureStatus",
    "JustificationSet",
    "Polarity",
    "PredicateRegistry",
    "PredicateSignature",
    "SupportGraph",
    "WorldAssumption",
    "grounded_closure",
    "justifications",
    "predicates",
    "proposition_key",
)
