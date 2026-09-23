#!/usr/bin/env python3
"""H5 isolated mutations: installation, integrity, references and domain isolation."""
from pathlib import Path

import run_h1h_mutations as runner
from run_h1h_mutations import Mutation

PACKAGE = "src/agent_orchestrator/planning/htn/domain_package.py"
CONTRACT = "tests/orchestrator/full_target/test_h5_domain_package_v1.py::"
M = (
    Mutation(501, "install ID-only manifest", PACKAGE,
             'if not package.definitions:', 'if False and not package.definitions:',
             CONTRACT + "test_installer_rejects_an_id_only_manifest"),
    Mutation(502, "reuse conflicting registration", PACKAGE,
             'if existing.content_hash != package.content_hash:',
             'if False and existing.content_hash != package.content_hash:',
             CONTRACT + "test_install_is_idempotent_and_rejects_conflicting_duplicate"),
    Mutation(503, "observer accepts missing predicate", PACKAGE,
             'if any(ref not in predicates for ref in observer.predicate_refs):',
             'if False and any(ref not in predicates for ref in observer.predicate_refs):',
             CONTRACT + "test_cross_references_and_capabilities_are_fail_closed"),
    Mutation(504, "hash omits actual definitions", PACKAGE,
             '**({"definitions": dict(self.definitions)} if self.definitions else {}),',
             '**{},', CONTRACT + "test_definition_content_changes_identity_and_stale_hash_is_refused"),
    Mutation(505, "observer bypasses capability declaration", PACKAGE,
             'if observer.capability not in caps:',
             'if False and observer.capability not in caps:',
             CONTRACT + "test_cross_references_and_capabilities_are_fail_closed"),
    Mutation(506, "core shortcuts one named domain", PACKAGE,
             '        if not isinstance(package, PlanningDomainPackageV1):',
             '        if package.domain_id == "code-v1":\n'
             '            return DomainInstallReceipt("INSTALLED", package.domain_id, package.content_hash)\n'
             '        if not isinstance(package, PlanningDomainPackageV1):',
             CONTRACT + "test_core_htn_has_no_domain_name_branch"),
    Mutation(507, "loader silently omits domain package install",
             "src/agent_orchestrator/planning/htn/seed_methods/loader.py",
             '        package_installer.install(domain.planning_package())',
             '        pass  # mutation: skip installing the declared package',
             "tests/orchestrator/full_target/test_h5_domain_loader_integration.py::test_seed_domain_projects_to_h5_package_and_installs_with_catalogues"),
    Mutation(508, "installed content tampering is trusted", PACKAGE,
             'if package is not None and content_hash_of(package._body()) != package.content_hash:',
             'if False and package is not None and content_hash_of(package._body()) != package.content_hash:',
             CONTRACT + "test_installed_nested_definition_tampering_is_rejected_on_read"),
)

if __name__ == "__main__":
    runner.M = M
    runner.OUT = Path("/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-22/h5-mutations")
    raise SystemExit(runner.main())
