"""Historical offline H5/H6 mutation definitions.

H5 execution is superseded by run_h5_mutations.py (8 actual controls).
H6 execution is superseded by run_h6_mutations.py (8 actual controls).

GAP entries below preserve the earlier planning snapshot, not current execution
status. The runnable scripts and their evidence indexes supersede these drafts.
"""
from run_h1h_mutations import Mutation


def gap(i: int, title: str, nodeid: str, reason: str) -> Mutation:
    return Mutation(i, title, None, None, None, nodeid, gap=reason)


M = (
    gap(501, "H5 accept id-only manifest", "tests/orchestrator/full_target/test_h5_domain_package_v1.py::test_installer_rejects_an_id_only_manifest", "GAP: locate the exact installer completeness guard before defining a compiling bypass"),
    gap(502, "H5 ignore conflicting duplicate", "tests/orchestrator/full_target/test_h5_domain_package_v1.py::test_install_is_idempotent_and_rejects_conflicting_duplicate", "GAP: exact registry conflict seam not yet isolated"),
    gap(503, "H5 omit cross-reference validation", "tests/orchestrator/full_target/test_h5_domain_package_v1.py::test_cross_references_and_capabilities_are_fail_closed", "GAP: test covers several guards; one faithful atomic patch is not yet identified"),
    gap(504, "H5 hash omits package content", "tests/orchestrator/full_target/test_h5_domain_package_v1.py::test_domain_package_round_trips_and_hashes", "GAP: no exact content-hash omission patch reviewed"),
    gap(505, "H5 domain-specific data contract", "tests/orchestrator/full_target/test_h5_domain_package_v1.py::test_three_domain_names_use_the_same_data_contract", "GAP: requires a compiling domain branch rather than an interface break"),
    gap(506, "H5 core branches on domain name", "tests/orchestrator/full_target/test_h5_domain_package_v1.py::test_core_htn_has_no_domain_name_branch", "GAP: source-scan mutation needs a meaningful compiling behavior branch"),
    gap(507, "H5 loader drops catalogues", "tests/orchestrator/full_target/test_h5_domain_loader_integration.py::test_seed_domain_projects_to_h5_package_and_installs_with_catalogues", "GAP: exact projection return construction not yet reviewed"),
    gap(508, "H5 planning world drops installed package", "tests/orchestrator/full_target/test_h5_domain_loader_integration.py::test_production_planning_world_keeps_the_installed_domain_package", "GAP: exact assembly binding seam not yet reviewed"),
    gap(601, "H6 evaluation set is mutable or ordered", "tests/orchestrator/full_target/test_h6_method_lifecycle_v1.py::test_evaluation_set_is_frozen_and_order_independent", "GAP: exact canonicalization snippet not yet isolated"),
    gap(602, "H6 trial or acceptance threshold bypass", "tests/orchestrator/full_target/test_h6_method_lifecycle_v1.py::test_policy_requires_trials_and_acceptance_thresholds", "GAP: policy has multiple thresholds; faithful atomic bypass not yet selected"),
    gap(603, "H6 failed gate accepted", "tests/orchestrator/full_target/test_h6_method_lifecycle_v1.py::test_evaluation_contract_accepts_complete_run_evidence_and_rejects_failed_gate", "GAP: exact gate conjunction not yet isolated"),
    gap(604, "H6 replay accepts evaluation-set mismatch", "tests/orchestrator/full_target/test_h6_method_lifecycle_v1.py::test_evaluation_set_mismatch_is_rejected_on_replay", "GAP: exact replay identity guard not yet isolated"),
    gap(605, "H6 oracle mode trusts report status", "tests/orchestrator/full_target/test_h6_oracle_bound_evaluation.py::test_real_terminal_nonadopting_mission_oracle_persists_and_legacy_run_stays_strict", "GAP: must mutate the real oracle-mode decision without producing AttributeError"),
    gap(606, "H6 unknown usage is scoreable", "tests/orchestrator/full_target/test_h6_oracle_bound_evaluation.py::test_real_oracle_rejects_unknown_usage_and_post_receipt_usage_change", "GAP: exact unknown-usage fail-closed guard not yet isolated"),
    gap(607, "H6 denominator drops failed or nonadopting receipts", "tests/orchestrator/full_target/test_h6_oracle_bound_evaluation.py::test_aggregation_only_oracle_thresholds_use_all_25_candidate_receipts", "GAP: exact complete-denominator aggregation expression not yet isolated"),
    gap(608, "H6 promotion trusts stale evidence", "tests/orchestrator/full_target/test_h6_method_lifecycle_v1.py::test_evaluated_status_and_frozen_evidence_survive_restart", "GAP: existing node proves restart persistence but has not been verified to assert promotion-time evidence reread"),
)

__all__ = ("M",)
