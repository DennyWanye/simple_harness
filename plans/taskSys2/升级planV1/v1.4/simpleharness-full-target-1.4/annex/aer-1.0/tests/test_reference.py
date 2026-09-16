"""仅测局部纯规则，非实际SDK/模型/数据库集成验收。"""
import unittest
from dataclasses import replace
from reference.protocol_rules import *

class ExpressionTests(unittest.TestCase):
    def test_all_pass(self):
        self.assertIs(evaluate(All((Criterion('a'), Criterion('b'))),
                               {'a':Verdict.PASS, 'b':Verdict.PASS}), Verdict.PASS)
    def test_unknown_required(self):
        self.assertIs(evaluate(All((Criterion('a'), Criterion('b'))),
                               {'a':Verdict.PASS, 'b':Verdict.UNKNOWN}), Verdict.UNKNOWN)
    def test_alternative_pass(self):
        self.assertIs(evaluate(AnyOf((Criterion('a'), Criterion('b'))),
                               {'a':Verdict.FAIL, 'b':Verdict.PASS}), Verdict.PASS)
    def test_required_fail(self):
        self.assertIs(evaluate(All((Criterion('a'), Criterion('b'))),
                               {'a':Verdict.UNKNOWN, 'b':Verdict.FAIL}), Verdict.FAIL)
    def test_unknown_alternative(self):
        self.assertIs(evaluate(AnyOf((Criterion('a'), Criterion('b'))),
                               {'a':Verdict.FAIL, 'b':Verdict.UNKNOWN}), Verdict.UNKNOWN)
    def test_missing_not_default_pass(self):
        with self.assertRaises(ValueError): evaluate(Criterion('missing'), {})
    def test_empty_rejected(self):
        with self.assertRaises(ValueError): All(())
        with self.assertRaises(ValueError): AnyOf(())
    def test_hard_failure_wins(self):
        self.assertFalse(may_accept(expression_result=Verdict.PASS, hard_constraints_pass=False,
            mandatory_checks_pass=True,semantic_review_required=True,semantic_review_pass=True,
            current_bindings=True,critical_obligations_accounted_for=True))
    def test_missing_review_blocks(self):
        self.assertFalse(may_accept(expression_result=Verdict.PASS, hard_constraints_pass=True,
            mandatory_checks_pass=True,semantic_review_required=True,semantic_review_pass=False,
            current_bindings=True,critical_obligations_accounted_for=True))
    def test_stale_binding_blocks(self):
        self.assertFalse(may_accept(expression_result=Verdict.PASS, hard_constraints_pass=True,
            mandatory_checks_pass=True,semantic_review_required=True,semantic_review_pass=True,
            current_bindings=False,critical_obligations_accounted_for=True))

class JustificationTests(unittest.TestCase):
    def test_no_cycle_self_proof(self):
        out=grounded_closure(frozenset(),[Rule((('b',True),),('a',True)),Rule((('a',True),),('b',True))])
        self.assertIs(truth_for('a',out),Truth.UNKNOWN)
    def test_external_anchor_does_not_seed_self_and(self):
        out=grounded_closure(frozenset({('e',True)}),[Rule((('a',True),('e',True)),('a',True))])
        self.assertIs(truth_for('a',out),Truth.UNKNOWN)
    def test_grounded_cycle(self):
        rules=[Rule((('e',True),),('a',True)),Rule((('a',True),),('b',True)),Rule((('b',True),),('a',True))]
        self.assertIs(truth_for('b',grounded_closure(frozenset({('e',True)}),rules)),Truth.TRUE)
    def test_anchor_retraction_no_cycle_survival(self):
        rules=[Rule((('e',True),),('a',True)),Rule((('a',True),),('b',True)),Rule((('b',True),),('a',True))]
        self.assertIs(truth_for('b',grounded_closure(frozenset(),rules)),Truth.UNKNOWN)
    def test_alternative_support_survives(self):
        rules=[Rule((('e1',True),('condition',True)),('k',True)),Rule((('e2',True),),('k',True))]
        self.assertIs(truth_for('k',grounded_closure(frozenset({('e2',True)}),rules)),Truth.TRUE)
    def test_conflict_not_vote(self):
        out=grounded_closure(frozenset({('k',True),('k',False)}),[])
        self.assertIs(truth_for('k',out),Truth.CONFLICT)
    def test_missing_not_negative(self):
        self.assertIs(truth_for('k',frozenset()),Truth.UNKNOWN)
    def test_bounded_not_unsolvable(self):
        with self.assertRaisesRegex(RuntimeError,'EVALUATION_INCOMPLETE'):
            grounded_closure(frozenset({('a',True)}),[Rule((('a',True),),('b',True))],max_atoms=1)
    def test_stale_epoch_blocks(self):
        self.assertFalse(usable_for_execution(truth=Truth.TRUE,current=True,readable=True,
            authorized=True,assurance_sufficient=True,premises_consistent=True,
            witness_epoch=2,current_epoch=3,now_ms=5,not_after_ms=10))
    def test_expiry_boundary_blocks(self):
        self.assertFalse(usable_for_execution(truth=Truth.TRUE,current=True,readable=True,
            authorized=True,assurance_sufficient=True,premises_consistent=True,
            witness_epoch=2,current_epoch=2,now_ms=10,not_after_ms=10))

class RetryTests(unittest.TestCase):
    def setUp(self):
        self.base=RetryFacts(Outcome.UNKNOWN,True,True,True,True,retry_permitted_by_policy=True)
    def test_unknown_not_blind_retry(self):
        self.assertIs(retry_decision(self.base,now_ms=10),RetryDecision.RECONCILE)
    def test_live_native_key_retry(self):
        f=replace(self.base,native_atomic_dedupe=True,authoritative_contract=True,dedupe_until_ms=20)
        self.assertIs(retry_decision(f,now_ms=10),RetryDecision.SEND_SAME_OPERATION)
    def test_expired_dedupe(self):
        f=replace(self.base,native_atomic_dedupe=True,authoritative_contract=True,dedupe_until_ms=10)
        self.assertIs(retry_decision(f,now_ms=10),RetryDecision.RECONCILE)
    def test_empty_lookup_delayed_request(self):
        f=replace(self.base,outcome=Outcome.NOT_APPLIED_FINAL,final_negative_witness=True)
        self.assertIs(retry_decision(f,now_ms=10),RetryDecision.RECONCILE)
    def test_quiescent_negative(self):
        f=replace(self.base,outcome=Outcome.NOT_APPLIED_FINAL,final_negative_witness=True,old_request_cannot_apply=True)
        self.assertIs(retry_decision(f,now_ms=10),RetryDecision.SEND_SAME_OPERATION)
    def test_different_payload(self):
        self.assertIs(retry_decision(replace(self.base,same_payload=False),now_ms=10),RetryDecision.CONFLICT)
    def test_applied_returns_receipt(self):
        self.assertIs(retry_decision(replace(self.base,outcome=Outcome.APPLIED),now_ms=10),RetryDecision.RETURN_RECORDED_RESULT)
    def test_revocation_stops_handoff(self):
        self.assertIs(retry_decision(replace(self.base,current_authorization=False),now_ms=10),RetryDecision.DENY_NEW_HANDOFF)
    def test_partial_needs_reconciliation(self):
        f=replace(self.base,outcome=Outcome.PARTIAL,native_atomic_dedupe=True,authoritative_contract=True,dedupe_until_ms=100)
        self.assertIs(retry_decision(f,now_ms=10),RetryDecision.RECONCILE)
    def test_changed_target_version_blocks(self):
        self.assertIs(retry_decision(replace(self.base,current_target_condition=False),now_ms=10),RetryDecision.DENY_NEW_HANDOFF)

if __name__ == '__main__': unittest.main()
