# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Product workflow registration over the public Simple Harness SDK surface."""

from __future__ import annotations

import hashlib
from types import MappingProxyType

from simple_harness.contracts import JsonValue, canonical_json
from simple_harness.workflow import (
    SDK_DEPENDENCY_LOCK_HASH,
    ProfileDescriptor,
    StartInputSchema,
    WorkflowDefinition,
    WorkflowDefinitionRegistration,
    WorkflowProfileRegistration,
    compile_workflow,
    profile_descriptor_fingerprint,
    workflow_manifest_hash,
)

from deskpet.workflows.definitions.sdk_v2.ppt_pro import PPT_PRO_V2_DEFINITION
from deskpet.workflows.definitions.sdk_v7.deep_research import (
    DEEP_RESEARCH_V7_SDK1_DEFINITION,
)


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


def _registration(
    *,
    profile_key: str,
    description: str,
    use_when: str,
    avoid_when: str,
    schema: dict[str, JsonValue],
    schema_ref: str,
    generation: int,
    definition: WorkflowDefinition,
    transaction_owner: object,
) -> WorkflowDefinitionRegistration:
    descriptor = ProfileDescriptor(
        key=profile_key,
        description=description,
        use_when=use_when,
        avoid_when=avoid_when,
        input_schema_ref=schema_ref,
        generation=generation,
        fingerprint=profile_descriptor_fingerprint(
            profile_key,
            description,
            use_when,
            avoid_when,
            schema_ref,
            generation,
        ),
    )
    profile = WorkflowProfileRegistration(
        descriptor=descriptor,
        workflow_name=definition.name,
        workflow_version=definition.version,
        start_input_schema=StartInputSchema(
            schema_ref=schema_ref,
            canonical_schema=schema,
            schema_hash=hashlib.sha256(canonical_json(schema).encode()).hexdigest(),
        ),
    )
    compiled = compile_workflow(
        definition, dependency_lock_hash=SDK_DEPENDENCY_LOCK_HASH
    )
    return WorkflowDefinitionRegistration(
        profile=profile,
        definition=definition,
        dependency_lock_hash=compiled.manifest.dependency_lock_hash,
        expected_manifest_hash=workflow_manifest_hash(compiled.manifest),
        expected_implementation_fingerprint=(
            compiled.manifest.implementation_bundle_hash
        ),
        transaction_owner=transaction_owner,
    )


def build_product_workflow_registrations(
    *, generation: int, transaction_owner: object
) -> tuple[WorkflowDefinitionRegistration, ...]:
    """Return the two active, detached product registrations."""

    if generation < 1:
        raise ValueError("generation must be positive")
    return (
        _registration(
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
        _registration(
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
