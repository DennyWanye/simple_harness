"""Mission-funded final review must not borrow a Task Critic's protected tail."""


from graph_helpers7 import node

from agent_orchestrator.orchestrator.commit_service import mission_account


def test_final_judge_exception_rejects_foreign_or_malformed_authority(tmp_path):
    import pytest
    from graph_helpers7 import graph_service

    from agent_orchestrator.governance.budgets import BudgetError
    from agent_orchestrator.orchestrator.commit_service import Reservation, task_account

    commit, mission, tasks = graph_service(tmp_path, nodes=[node("A")])
    task = tasks["A"]
    try:
        cases = ("foreign_account", "task_id", "wrong_subject", "no_view")
        for ordinal, case in enumerate(cases, 1):
            subject = f"{mission.id}:judge:{ordinal}"
            if case == "wrong_subject":
                subject = f"{mission.id}:critic:{ordinal}"
            commit.create_service_intent(
                kind="critic", subject_id=subject, mission_id=mission.id,
                account_id=(task_account(task.id) if case == "foreign_account"
                            else mission_account(mission.id)),
                creation_key=subject, input_id="input", input_hash="controlled",
                config={
                    "attempt_id": None if case == "no_view" else f"{mission.id}-judge-view",
                    "task_id": task.id if case == "task_id" else None,
                }, reservation=Reservation(6000, 0),
            )
            with commit.store.transaction():
                before = dict(commit.ledger.reservation(subject))
                with pytest.raises(BudgetError, match="actual Attempt"):
                    commit.grow_system_worker_allowance(subject, tokens=9894, cost_micros=0)
                assert dict(commit.ledger.reservation(subject)) == before
    finally:
        commit.store.close()
