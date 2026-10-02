"""User decision 2026-09-26: one general task that may carry reference material.

A general (code-v1) Mission now accepts the same initial source batch a document
Mission does: the material is frozen per Attempt, mounted in the Worker's
workspace under ``sources/`` and protected from change.  Until now a general
Mission refused it with ``not_found`` (its profile had no source roots), so the
only way to hand an orchestration task a file was the strict citation domain.
"""



from agent_orchestrator.governance.domains import CODE_PROFILE_V4


def test_frozen_general_profiles_keep_no_source_roots():
    assert CODE_PROFILE_V4.source_roots == () and "source" not in CODE_PROFILE_V4.allowed_evidence_kinds
