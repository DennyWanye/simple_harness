# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Product workflow registration over the public Simple Harness SDK surface."""

from __future__ import annotations

from types import MappingProxyType

from simple_harness.contracts import JsonValue
from simple_harness.workflow import (
    WorkflowDefinitionRegistration,
    build_registration,
)

from deskpet.sdk_adapters.product_workflows.deep_research_definition import (
    DEEP_RESEARCH_V7_SDK1_DEFINITION,
)
from deskpet.sdk_adapters.product_workflows.ppt_pro_definition import PPT_PRO_V2_DEFINITION

ACTIVE_PRODUCT_WORKFLOWS = MappingProxyType(
    {
        "workflow.deep_research": ("deep_research", "v7-sdk1"),
        "workflow.presentation": ("ppt_pro", "v2"),
    }
)

_DEEP_RESEARCH_SCHEMA_REF = "deskpet://workflow.deep_research/start/v7-sdk1"
_DEEP_RESEARCH_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "required": ["topic"],
    "properties": {
        "topic": {"type": "string", "minLength": 1},
        "mode": {"type": "string", "enum": ["light", "standard", "deep"]},
    },
    "additionalProperties": False,
}

_PRESENTATION_SCHEMA_REF = "deskpet://workflow.presentation/start/v2"
_PRESENTATION_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "required": ["topic"],
    "properties": {
        "topic": {"type": "string", "minLength": 1},
        "title": {"type": "string"},
        "pages": {"type": "integer", "minimum": 1, "maximum": 40},
        "output_path": {"type": "string"},
        "editable_required": {"type": "boolean"},
        "full_page_images": {"type": "boolean"},
    },
    "additionalProperties": False,
}


def build_product_workflow_registrations(
    *, generation: int, transaction_owner: object
) -> tuple[WorkflowDefinitionRegistration, ...]:
    """Return the two active, detached product registrations."""

    if generation < 1:
        raise ValueError("generation must be positive")
    return (
        build_registration(
            profile_key="workflow.deep_research",
            description="Durable sourced research with a bounded terminal envelope.",
            use_when="The user asks for multi-source research or a cited report.",
            avoid_when="A short answer or presentation is the requested deliverable.",
            schema=_DEEP_RESEARCH_SCHEMA,
            schema_ref=_DEEP_RESEARCH_SCHEMA_REF,
            generation=generation,
            definition=DEEP_RESEARCH_V7_SDK1_DEFINITION,
            transaction_owner=transaction_owner,
        ),
        build_registration(
            profile_key="workflow.presentation",
            description="Durable editable presentation generation with outline approval.",
            use_when="The user asks to create a presentation or slide deck.",
            avoid_when="The user only needs a prose research report.",
            schema=_PRESENTATION_SCHEMA,
            schema_ref=_PRESENTATION_SCHEMA_REF,
            generation=generation,
            definition=PPT_PRO_V2_DEFINITION,
            transaction_owner=transaction_owner,
        ),
    )


__all__ = ("ACTIVE_PRODUCT_WORKFLOWS", "build_product_workflow_registrations")
