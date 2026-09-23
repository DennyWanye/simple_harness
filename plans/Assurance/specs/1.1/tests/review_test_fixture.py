"""Only legacy test data normalization; NOT a production producer/authority adapter."""
from reference.protocol_v11 import CriterionCheckPolicy, CheckResult, decide_review
from reference.semantics import Grade
def fixture_review_can_accept(data,expr,mandatory,values):
    policies={a['criterion_id']:CriterionCheckPolicy(a['criterion_id'],'CHECKED',((a['criterion_id'],),)) for a in data['assessments']}
    results={k:CheckResult(k,'SUCCEEDED',Grade.PASS if v else Grade.FAIL,'test-receipt-'+k,True) for k,v in values.items()}
    return decide_review(data,expr,mandatory,policies,results).acceptable
