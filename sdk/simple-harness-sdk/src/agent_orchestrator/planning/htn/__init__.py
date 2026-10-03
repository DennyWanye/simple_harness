# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Hierarchical task network planning package.

The pipeline of §18.3, in the order a planner walks it:

``applicability.assess_method``
    four-valued: applicable, refuted, needs evidence, conflicted, unavailable.
``refinement.planning_frontier`` / ``refinement.evidence_requests``
    which compound occurrences are still open, and which read-only observers
    could answer an UNKNOWN precondition.  *Which* method refines a goal is the
    planner's (LLM's) judgement, not a function here.
``grounding.ground_method``
    type-check, bind, derive stable identities, decide what may be shared.
``compiler.compile_refinement``
    the ten steps of TG §5.2, ending in a ``ProposedPlanDelta`` and stopping short
    of the Commit service.
``validation.validate_delta`` / ``validation.to_hddl``
    the semantic judgements the structural checks do not cover, and the minimal
    HDDL export the PANDA backend consumes.

``registry`` holds the declarations all of them read; ``seed_methods`` is a pair
of domains expressed entirely as data, which is how the generality claim of §7.1
is checked rather than asserted.
"""

from .applicability import (
    ApplicabilityReport,
    ApplicabilityStatus,
    CapabilityRecord,
    CapabilitySnapshot,
    assess_method,
    recheck_method_instance,
)
from .backend_port import (
    BackendStatus,
    CandidatePlanWitness,
    PandaPlanningBackend,
    PlanningBackend,
    PlanningBackendBridge,
    PlanningBackendResult,
    PlanningLimits,
    PlanningProblemSnapshot,
)
from .compiler import (
    BudgetRequirement,
    CompilationRefused,
    DemandAdmission,
    DemandNotAdmissible,
    RefinementCompilation,
    RootNetwork,
    apply_obligation_openings,
    compile_refinement,
    compile_refinement_bundle,
    demand_admissions_for,
)
from .cross_domain_acceptance import (
    AcceptanceResultV1,
    CrossDomainAcceptancePlan,
    CrossDomainScenario,
    FourArm,
    ScenarioKind,
)
from .domain_package import (
    DomainInstallReceipt,
    DomainPackageInstaller,
    ObserverRegistrationV1,
    OperatorRegistrationV1,
    PlanningDomainPackageV1,
)
from .grounding import (
    GroundingError,
    ShareDecision,
    SharedGoalEntry,
    SharedGoalIndex,
    ShareVerdict,
    SharingSignature,
    ground_method,
    may_share,
)
from .method_selection import (
    MAX_EVIDENCE_QUESTIONS,
    MethodCandidates,
    MethodSelectionCandidateV1,
    filter_candidates,
    normalize_candidates,
    validate_evidence_request,
)
from .refinement import (
    FrontierItem,
    planning_frontier,
)
from .registry import (
    AdmissionPolicy,
    AdmissionReceipt,
    AdmissionVerdict,
    MethodCandidate,
    MethodProposal,
    MethodRegistry,
    ObjectSchema,
    RejectionCode,
    SchemaCatalog,
    SchemaField,
    TaskTypeCatalog,
    TaskTypeSpec,
)
from .validation import (
    DeltaProblemKind,
    DeltaReport,
    HddlExport,
    PreconditionClass,
    UnsupportedFeature,
    to_hddl,
    validate_delta,
)

__all__ = (
    "AdmissionPolicy",
    "AdmissionReceipt",
    "AdmissionVerdict",
    "ApplicabilityReport",
    "ApplicabilityStatus",
    "BackendStatus",
    "CandidatePlanWitness",
    "PlanningBackend",
    "PandaPlanningBackend",
    "PlanningBackendBridge",
    "PlanningBackendResult",
    "PlanningLimits",
    "PlanningProblemSnapshot",
    "MAX_EVIDENCE_QUESTIONS",
    "MethodCandidates",
    "MethodSelectionCandidateV1",
    "filter_candidates",
    "CrossDomainAcceptancePlan",
    "AcceptanceResultV1",
    "CrossDomainScenario",
    "FourArm",
    "ScenarioKind",
    "DomainInstallReceipt",
    "DomainPackageInstaller",
    "ObserverRegistrationV1",
    "OperatorRegistrationV1",
    "PlanningDomainPackageV1",
    "BudgetRequirement",
    "CapabilityRecord",
    "CapabilitySnapshot",
    "CompilationRefused",
    "DeltaProblemKind",
    "DeltaReport",
    "FrontierItem",
    "GroundingError",
    "HddlExport",
    "MethodCandidate",
    "MethodProposal",
    "MethodRegistry",
    "ObjectSchema",
    "PreconditionClass",
    "RefinementCompilation",
    "RejectionCode",
    "RootNetwork",
    "SchemaCatalog",
    "SchemaField",
    "ShareDecision",
    "ShareVerdict",
    "SharedGoalEntry",
    "SharedGoalIndex",
    "SharingSignature",
    "TaskTypeCatalog",
    "TaskTypeSpec",
    "UnsupportedFeature",
    "DemandAdmission",
    "DemandNotAdmissible",
    "apply_obligation_openings",
    "demand_admissions_for",
    "assess_method",
    "compile_refinement",
    "compile_refinement_bundle",
    "ground_method",
    "may_share",
    "normalize_candidates",
    "planning_frontier",
    "recheck_method_instance",
    "to_hddl",
    "validate_delta",
    "validate_evidence_request",
)
