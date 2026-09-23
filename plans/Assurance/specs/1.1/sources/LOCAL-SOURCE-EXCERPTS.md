# 当前候选定点源码摘录

用途：支持 F01–F15 计划修订。仅为局部静态来源，不是完整源码审计、运行证据或生产就绪证明。每文件记录 SHA-256 和原始行号；未包含凭据或环境文件。

## SDK src/agent_orchestrator/contracts/resolution.py

SHA-256: `92dcb60352ba829275bfe4c732691f20d63a97eee875edb98db81672461fad60`

原行 446–565
```python
446: class RequirementsRevision:
447:     """AER §4.1: one immutable version of what the user asked for."""
448: 
449:     revision_id: RequirementsRevisionId
450:     mission_id: str
451:     revision: int
452:     criteria: tuple[Criterion, ...]
453:     success_expression: Any
454:     source_text_ref: TypedRef | None = None
455:     authority_subject: str | None = None
456:     interpretation_scope: str | None = None
457:     delivery_contract_ref: str | None = None
458:     amendment_credential_ref: str | None = None
459: 
460:     def __post_init__(self) -> None:
461:         object.__setattr__(
462:             self,
463:             "revision_id",
464:             RequirementsRevisionId(identifier(self.revision_id, "requirements.revision_id")),
465:         )
466:         object.__setattr__(
467:             self, "mission_id", identifier(self.mission_id, "requirements.mission_id")
468:         )
469:         object.__setattr__(self, "revision", index(self.revision, "requirements.revision"))
470:         if not self.criteria:
471:             raise ContractError("requirements.criteria must not be empty")
472:         ids = [criterion.criterion_id for criterion in self.criteria]
473:         if len(set(ids)) != len(ids):
474:             raise ContractError("requirements.criteria must not repeat a criterion_id")
475:         unknown = unknown_expression_criteria(self.success_expression, self.criteria)
476:         if unknown:
477:             raise ContractError(
478:                 f"requirements.success_expression names criteria outside the catalogue: "
479:                 f"{list(unknown)}"
480:             )
481:         violations = hard_constraints_not_independent(self.success_expression, self.criteria)
482:         if violations:
483:             raise ContractError(
484:                 "every HARD_CONSTRAINT must be an independent AND conjunct of the success "
485:                 f"expression; these are not: {list(violations)}"
486:             )
487:         if self.source_text_ref is not None and not isinstance(self.source_text_ref, TypedRef):
488:             raise ContractError("requirements.source_text_ref must be a TypedRef or null")
489:         for name in (
490:             "authority_subject",
491:             "interpretation_scope",
492:             "delivery_contract_ref",
493:             "amendment_credential_ref",
494:         ):
495:             object.__setattr__(
496:                 self, name, optional_identifier(getattr(self, name), f"requirements.{name}")
497:             )
498: 
499:     def required_criterion_ids(self) -> tuple[str, ...]:
500:         return tuple(criterion.criterion_id for criterion in self.criteria if criterion.is_required)
501: 
502:     def to_json(self) -> dict[str, Any]:
503:         return {
504:             "revision_id": str(self.revision_id),
505:             "mission_id": self.mission_id,
506:             "revision": self.revision,
507:             "criteria": [criterion.to_json() for criterion in self.criteria],
508:             "success_expression": self.success_expression.to_json(),
509:             "source_text_ref": (
510:                 None if self.source_text_ref is None else self.source_text_ref.to_json()
511:             ),
512:             "authority_subject": self.authority_subject,
513:             "interpretation_scope": self.interpretation_scope,
514:             "delivery_contract_ref": self.delivery_contract_ref,
515:             "amendment_credential_ref": self.amendment_credential_ref,
516:         }
517: 
518:     def content_hash(self) -> str:
519:         return content_hash_of(self.to_json())
520: 
521:     @classmethod
522:     def from_json(cls, value: object, name: str = "requirements_revision") -> RequirementsRevision:
523:         data = fields_of(
524:             value,
525:             name,
526:             required=("revision_id", "mission_id", "revision", "criteria", "success_expression"),
527:             optional=(
528:                 "source_text_ref",
529:                 "authority_subject",
530:                 "interpretation_scope",
531:                 "delivery_contract_ref",
532:                 "amendment_credential_ref",
533:             ),
534:         )
535:         raw_source = data.get("source_text_ref")
536:         return cls(
537:             revision_id=RequirementsRevisionId(data["revision_id"]),
538:             mission_id=data["mission_id"],
539:             revision=data["revision"],
540:             criteria=sequence_of(
541:                 data["criteria"],
542:                 f"{name}.criteria",
543:                 lambda item, where: Criterion.from_json(item, where),
544:                 minimum=1,
545:             ),
546:             success_expression=parse_success_expression(
547:                 data["success_expression"], f"{name}.success_expression"
548:             ),
549:             source_text_ref=(
550:                 None
551:                 if raw_source is None
552:                 else TypedRef.from_json(raw_source, f"{name}.source_text_ref")
553:             ),
554:             authority_subject=data.get("authority_subject"),
555:             interpretation_scope=data.get("interpretation_scope"),
556:             delivery_contract_ref=data.get("delivery_contract_ref"),
557:             amendment_credential_ref=data.get("amendment_credential_ref"),
558:         )
559: 
560: 
561: # --------------------------------------------------------------------------------------
562: # Review (AER §5, §13 v1.4)
563: # --------------------------------------------------------------------------------------
564: 
565: 
```

原行 566–602
```python
566: class ReviewPurpose(StrEnum):
567:     """§13 v1.4: the six review purposes.  Each binds to its own account."""
568: 
569:     TASK_CONTENT = "TASK_CONTENT"
570:     METHOD_PLAN = "METHOD_PLAN"
571:     COMPOSITION = "COMPOSITION"
572:     ACTION_PROPOSAL = "ACTION_PROPOSAL"
573:     OPERATION_OUTCOME = "OPERATION_OUTCOME"
574:     MISSION_FINAL = "MISSION_FINAL"
575: 
576: 
577: class ReviewAccount(StrEnum):
578:     """Which budget account a review's cost lands on (§13 v1.4, §21.5 conservation)."""
579: 
580:     TASK = "task"
581:     MISSION_PLANNING = "mission_planning"
582:     PARENT_COMPOUND_TASK = "parent_compound_task"
583:     OPERATION_TASK = "operation_task"
584:     MISSION = "mission"
585: 
586: 
587: #: §13 v1.4: total, so no purpose can quietly escape budget accounting.
588: REVIEW_PURPOSE_ACCOUNTS: Mapping[ReviewPurpose, ReviewAccount] = {
589:     ReviewPurpose.TASK_CONTENT: ReviewAccount.TASK,
590:     ReviewPurpose.METHOD_PLAN: ReviewAccount.MISSION_PLANNING,
591:     ReviewPurpose.COMPOSITION: ReviewAccount.PARENT_COMPOUND_TASK,
592:     ReviewPurpose.ACTION_PROPOSAL: ReviewAccount.OPERATION_TASK,
593:     ReviewPurpose.OPERATION_OUTCOME: ReviewAccount.OPERATION_TASK,
594:     ReviewPurpose.MISSION_FINAL: ReviewAccount.MISSION,
595: }
596: 
597: 
598: def account_for_purpose(purpose: ReviewPurpose) -> ReviewAccount:
599:     resolved = enum_of(ReviewPurpose, purpose, "review.purpose")
600:     return REVIEW_PURPOSE_ACCOUNTS[resolved]
601: 
602: 
```

原行 797–859
```python
797: class ReviewPackage:
798:     """AER §5.2: the immutable anchor a review is bound to.
799: 
800:     The system fills identity, authority, account and hashes; a worker may only
801:     contribute references to its own candidate.  Extra evidence gathered during
802:     the review is appended as new records, never by rewriting this package.
803:     """
804: 
805:     package_id: ReviewPackageId
806:     purpose: ReviewPurpose
807:     binding: ReviewBinding
808:     criteria: tuple[Criterion, ...]
809:     success_expression: Any
810:     candidate_refs: tuple[TypedRef, ...] = ()
811:     child_acceptance_refs: tuple[TypedRef, ...] = ()
812:     defect_history_refs: tuple[TypedRef, ...] = ()
813:     counter_evidence_refs: tuple[TypedRef, ...] = ()
814:     allowed_capabilities: tuple[str, ...] = ()
815:     independence_policy_ref: str | None = None
816:     method_instance_id: MethodInstanceId | None = None
817:     review_budget_ref: str | None = None
818:     #: The agents that produced the candidate.  Recorded on the package so the
819:     #: independence check reads one frozen fact instead of re-deriving authorship
820:     #: from whatever the record happens to mention (AER §5.3).
821:     producer_agent_ids: tuple[str, ...] = ()
822:     reviewer_workspace_access: WorkspaceAccess = WorkspaceAccess.READ_ONLY
823:     #: Binds the package to the exact requirements text it was cut from, so a later
824:     #: requirements revision cannot be read back through this anchor (AER §3.2).
825:     requirements_content_hash: str | None = None
826: 
827:     def __post_init__(self) -> None:
828:         object.__setattr__(
829:             self,
830:             "package_id",
831:             ReviewPackageId(identifier(self.package_id, "package.package_id")),
832:         )
833:         object.__setattr__(self, "purpose", enum_of(ReviewPurpose, self.purpose, "package.purpose"))
834:         if not isinstance(self.binding, ReviewBinding):
835:             raise ContractError("package.binding must be a ReviewBinding")
836:         if not self.criteria:
837:             raise ContractError("package.criteria must not be empty")
838:         ids = [criterion.criterion_id for criterion in self.criteria]
839:         if len(set(ids)) != len(ids):
840:             raise ContractError("package.criteria must not repeat a criterion_id")
841:         unknown = unknown_expression_criteria(self.success_expression, self.criteria)
842:         if unknown:
843:             raise ContractError(
844:                 f"package.success_expression names criteria outside its catalogue: {list(unknown)}"
845:             )
846:         object.__setattr__(
847:             self,
848:             "allowed_capabilities",
849:             identifiers(self.allowed_capabilities, "package.allowed_capabilities"),
850:         )
851:         object.__setattr__(
852:             self,
853:             "producer_agent_ids",
854:             identifiers(self.producer_agent_ids, "package.producer_agent_ids"),
855:         )
856:         object.__setattr__(
857:             self,
858:             "reviewer_workspace_access",
859:             enum_of(
```

## SDK src/agent_orchestrator/orchestrator/scoped_content_review.py

SHA-256: `4fec9f5ec06b3c91964b96270c54c9903df8d239c9095d913de7ffea0a1653a1`

原行 29–157
```python
29: from ..contracts.semantic_base import TypedRef, TypedRefKind
30: from ..storage.htn_store import HtnStore
31: from ..storage.store import Store
32: from .operation_completion import OperationCompletionError, OperationCompletionReader
33: 
34: 
35: def uses_completion_protocol(store: Store, mission_id: str) -> bool:
36:     row = store.connection.execute(
37:         "SELECT protocol_version FROM mission_planning_protocols WHERE mission_id=?",
38:         (mission_id,),
39:     ).fetchone()
40:     return row is not None and row[0] == "planning-decision-v1"
41: 
42: 
43: @dataclass(frozen=True, slots=True)
44: class ScopedTaskContent:
45:     requirements: RequirementsRevision
46:     spec: OperationCompletionRequirementsV1
47:     scope: OccurrenceCompletionScopeV1
48:     criteria: tuple[Criterion, ...]
49:     expression: SuccessExpression
50:     artifacts: tuple[CompletionPinV1, ...]
51:     producer_agent_id: str
52: 
53: 
54: def read_task_content_projection(
55:     store: Store,
56:     mission_id: str,
57:     task_id: str,
58:     result_id: str,
59: ) -> ScopedTaskContent:
60:     # Local import avoids a module cycle; the established leaf policy is shared,
61:     # never redefined from a model reply or a caller-supplied criterion catalogue.
62:     from .accepted_outputs import carried_criteria_for
63:     from .leaf_acceptance import criteria_for, layer_outcomes
64: 
65:     with store.read_view():
66:         htn = HtnStore(store)
67:         active = htn.active_plan_revision(mission_id)
68:         binding = htn.task_semantics_of(mission_id, task_id)
69:         if active is None or binding is None or binding.form is not TaskForm.PRIMITIVE:
70:             raise OperationCompletionError(
71:                 "OP_COMPLETION_SCOPE_UNRESOLVED", "a planned primitive Task is required"
72:             )
73:         members = [
74:             m
75:             for m in htn.list_plan_memberships(mission_id, active.revision)
76:             if str(m.task_id) == task_id
77:         ]
78:         if len(members) != 1:
79:             raise OperationCompletionError(
80:                 "OP_COMPLETION_SCOPE_UNRESOLVED", "Task occurrence is missing or ambiguous"
81:             )
82:         reader = OperationCompletionReader(store)
83:         scope = reader.read_scope(
84:             mission_id,
85:             PlanRevisionPinV1(revision=active.revision, snapshot_hash=active.snapshot_hash),
86:             str(members[0].occurrence_id),
87:         )
88:         spec = reader.read_requirements(
89:             mission_id, TypedRef(kind=TypedRefKind.REQUIREMENTS, **scope.requirements_ref.to_json())
90:         )
91:         requirements = htn.get_requirements_revision(mission_id, scope.requirements_ref.revision)
92:         result = store.get_result(result_id)
93:         if (
94:             result is None
95:             or result.envelope.mission_id != mission_id
96:             or result.envelope.task_id != task_id
97:             or result.verification_state != "DONE"
98:             or result.verdict != "PASS"
99:         ):
100:             raise OperationCompletionError("OP_CONTENT_REVIEW_UNAVAILABLE", "no verified result")
101:         attempt = store.get_attempt(result.envelope.attempt_id)
102:         if attempt is None or attempt.task_id != task_id or not attempt.agent_id:
103:             raise OperationCompletionError(
104:                 "OP_CONTENT_REVIEW_UNAVAILABLE", "result Attempt differs"
105:             )
106:         layers = layer_outcomes(store.list_verifications(result_id))
107:         if not layers or not any(item.layer == "critic_review" and item.passed for item in layers):
108:             raise OperationCompletionError(
109:                 "OP_CONTENT_REVIEW_UNAVAILABLE", "no recorded Critic PASS"
110:             )
111:         if any(item.conclusive and not item.passed for item in layers):
112:             raise OperationCompletionError(
113:                 "OP_CONTENT_REVIEW_UNAVAILABLE", "verification disagrees"
114:             )
115:         carried = carried_criteria_for(htn, mission_id, active.revision, members[0].occurrence_id)
116:         local = criteria_for(binding, layers, carried=carried)
117:         # Remove effect criteria from TASK_CONTENT: a verifier of prepared bytes
118:         # cannot attest an external milestone. Scope compiler already proved the
119:         # remaining IDs from this Task / adopted Method / fixed local policy.
120:         allowed = set(scope.content_criterion_ids)
121:         projected = tuple(item for item in local if item.criterion_id in allowed)
122:         if not projected or {item.criterion_id for item in projected} != allowed:
123:             raise OperationCompletionError("OP_COMPLETION_SCOPE_UNRESOLVED", "review scope differs")
124:         # Preserve any approved Spec-facing criterion's original evidence policy.
125:         root_catalogue = {item.criterion_id: item for item in requirements.criteria}
126:         criteria = tuple(
127:             root_catalogue[item.criterion_id]
128:             if item.criterion_id in spec.content_criterion_ids
129:             else item
130:             for item in projected
131:         )
132:         passed_checks = {item.layer for item in layers if item.passed}
133:         for criterion in criteria:
134:             missing = set(criterion.required_evidence_policy.required_check_ids) - passed_checks
135:             if missing:
136:                 raise OperationCompletionError(
137:                     "OP_CONTENT_REVIEW_UNAVAILABLE",
138:                     f"required checks have no recorded PASS: {sorted(missing)}",
139:                 )
140:         expression: SuccessExpression = CriterionExpr(criteria[0].criterion_id)
141:         if len(criteria) > 1:
142:             expression = AllExpr(children=tuple(CriterionExpr(c.criterion_id) for c in criteria))
143:         artifacts = []
144:         for artifact_id in result.artifacts:
145:             artifact = store.get_artifact(artifact_id)
146:             if (
147:                 artifact is None
148:                 or artifact.mission_id != mission_id
149:                 or artifact.task_id != task_id
150:                 or artifact.attempt_id != attempt.id
151:                 or artifact.verification_status != "VERIFIED"
152:             ):
153:                 raise ContractError("scoped content artifact differs from the verified result")
154:             artifacts.append(
155:                 CompletionPinV1(
156:                     id=artifact.id, revision=artifact.version, content_hash=artifact.content_hash
157:                 )
```

## SDK src/agent_orchestrator/orchestrator/resolution_commits.py

SHA-256: `7b95a7d3aa8ff5422e9e6c6b52c3fd996e16e553bc630bb830196cd71ed24ef6`

原行 592–635
```python
592:     def accept_review(
593:         self, command: AcceptReviewCommand, principal: ResolutionPrincipal
594:     ) -> AcceptanceReceipt:
595:         """Accept one contribution atomically, or write nothing (AER §7)."""
596: 
597:         if not isinstance(command, AcceptReviewCommand):
598:             raise ResolutionCommitRejected(
599:                 "BAD_COMMAND", "accept_review expects an AcceptReviewCommand"
600:             )
601:         if not isinstance(principal, ResolutionPrincipal):
602:             raise ResolutionCommitRejected(
603:                 "BAD_PRINCIPAL", "accept_review expects a ResolutionPrincipal"
604:             )
605:         # AER §7 step 1: identity first — before the receipt lookup, so a forged
606:         # replay of someone else's command_id cannot read back their receipt.
607:         _authorize(command.issued_by, command.scope_id, principal)
608:         intent = command.intent_hash()
609:         with self._store.transaction():
610:             mission = self._open_mission(command.mission_id)
611:             semantics = HtnStore(self._store)
612:             replayed = self._replayed(semantics, command.mission_id, command.command_id, intent)
613:             if replayed is not None:
614:                 self._require_kind(replayed, ACCEPTANCE_KIND)
615:                 from .scoped_content_review import uses_completion_protocol
616: 
617:                 if uses_completion_protocol(self._store, command.mission_id):
618:                     from ..storage.operation_completion_store import OperationCompletionStore
619: 
620:                     replayed_contribution = OperationCompletionStore(self._store).get_acceptance_scope_exact(
621:                         command.mission_id, replayed.subject_id
622:                     )
623:                     if replayed_contribution is None:
624:                         raise ResolutionCommitRejected(
625:                             "OP_COMPLETION_SCOPE_UNRESOLVED", "accepted review has no contribution"
626:                         )
627:                 return AcceptanceReceipt(
628:                     acceptance=semantics.get_acceptance(replayed.subject_id),
629:                     commit=replayed,
630:                     replayed=True,
631:                 )
632:             binding = self._require_binding(semantics, command.mission_id, command.task_id)
633:             if str(binding.obligation_id) != command.obligation_id:
634:                 raise ResolutionCommitRejected(
635:                     "BINDING_MISMATCH",
```

原行 675–718
```python
675:             from .scoped_content_review import uses_completion_protocol
676: 
677:             projection: Any = None
678:             if uses_completion_protocol(self._store, command.mission_id):
679:                 from ..verification.scoped_acceptance import (
680:                     acceptable_scoped_task_content, acceptable_scoped_operation_outcome,
681:                 )
682:                 from .scoped_content_review import validate_scoped_command
683: 
684:                 checker = acceptable_scoped_task_content
685:                 if command.purpose is ReviewPurpose.OPERATION_OUTCOME:
686:                     from .operation_outcomes import validate_scoped_outcome_command
687: 
688:                     projection = validate_scoped_outcome_command(
689:                         self._store, command,
690:                         runtime=getattr(self, "_operation_materialization_runtime", None),
691:                     )
692:                     checker = acceptable_scoped_operation_outcome
693:                 else:
694:                     projection = validate_scoped_command(self._store, command)
695:                 decision = checker(
696:                     subject,
697:                     projected_criteria=projection.criteria,
698:                     projected_expression=projection.expression,
699:                     now_ms=int(command.accepted_at_ms),
700:                     witness=witness,
701:                     current_scope_epoch=semantics.epoch(command.mission_id, witness.scope_id),
702:                 )
703:             else:
704:                 decision = acceptable(
705:                     subject,
706:                     now_ms=int(command.accepted_at_ms),
707:                     purpose=command.purpose,
708:                     witness=witness,
709:                     current_scope_epoch=semantics.epoch(command.mission_id, witness.scope_id),
710:                 )
711:             if not decision.acceptable:
712:                 raise ResolutionCommitRejected(
713:                     "NOT_ACCEPTABLE",
714:                     "the AER §6.2 formula refused: "
715:                     + ", ".join(str(reason) for reason in decision.reasons),
716:                 )
717:             acceptance = Acceptance(
718:                 acceptance_id=AcceptanceId(command.acceptance_id),
```

原行 1087–1133
```python
1087:             if uses_completion_protocol(self._store, command.mission_id):
1088:                 from .completion_status import read_occurrence_completion
1089: 
1090:                 active = semantics.active_plan_revision(command.mission_id)
1091:                 members = (
1092:                     []
1093:                     if active is None
1094:                     else [
1095:                         member
1096:                         for member in semantics.list_plan_memberships(
1097:                             command.mission_id, active.revision
1098:                         )
1099:                         if str(member.task_id) == str(resolution.goal_task_id)
1100:                     ]
1101:                 )
1102:                 if (
1103:                     len(members) != 1
1104:                     or not read_occurrence_completion(
1105:                         self._store, command.mission_id, str(members[0].occurrence_id)
1106:                     ).effects_ready
1107:                 ):
1108:                     raise ResolutionCommitRejected(
1109:                         "OP_REQUIRED_EFFECTS_INCOMPLETE", "Goal still has required effects"
1110:                     )
1111:                 if command.is_mission_root:
1112:                     from .completion_status import current_effect_proofs
1113: 
1114:                     anchors = {ref.id for ref in command.package.child_acceptance_refs}
1115:                     reviewed = {item.criterion_id: item for item in command.record.criteria}
1116:                     resolved = {item.criterion_id: item for item in resolution.criteria}
1117:                     for proof in current_effect_proofs(self._store, command.mission_id):
1118:                         expected = {content_hash_of(ref.to_json()) for ref in proof["evidence_refs"]}
1119:                         if proof["acceptance_id"] not in anchors:
1120:                             raise ResolutionCommitRejected("OP_OUTCOME_SOURCE_UNAVAILABLE",
1121:                                 "root review did not include current effect acceptance")
1122:                         for criterion_id in proof["criterion_ids"]:
1123:                             for outcomes in (reviewed, resolved):
1124:                                 item = outcomes.get(criterion_id)
1125:                                 actual = set() if item is None else {
1126:                                     content_hash_of(ref.to_json()) for ref in item.evidence_refs}
1127:                                 if not expected.issubset(actual):
1128:                                     raise ResolutionCommitRejected("OP_OUTCOME_SOURCE_UNAVAILABLE",
1129:                                         "root effect criterion lost its reviewed evidence")
1130:             delivery = self._check_delivery(semantics, command)
1131:             withdrawn = bool(account.has_admitted_demand)
1132:             shared = (not command.is_mission_root) and _duty_has_other_occurrences(
1133:                 semantics, command
```

## SDK src/agent_orchestrator/orchestrator/commit_service.py

SHA-256: `ad96a63e6b9bd042c23d61df535ed82d29bb96e2d5c87bd3a27175caf8e35edd`

原行 5106–5156
```python
5106:     def judge_mission(
5107:         self, mission_id: str, *, judgments: Sequence[Mapping[str, Any]], summary: str
5108:     ) -> Mission:
5109:         """Mission-level success judgment, independent of the Task PASS (D21, ORCH §12.4).
5110: 
5111:         Not the "Judge" of 理论 04-7 (which picks a champion candidate; not implemented in
5112:         this build) — this is the check of the Mission's own success criteria.
5113: 
5114:         ``judgments`` carries one entry per ``Mission.success_criteria`` item with
5115:         ``met: bool``; all met → COMPLETED, otherwise FAILED(mission_criteria_unmet)
5116:         while the Task stays COMPLETED.
5117:         """
5118: 
5119:         # P2.3c part 2c / review F6: the mode gate, in the same shape and the same
5120:         # place as ``commit_graph_change``'s — *before* the transaction, because the
5121:         # refusal appends an event and an event emitted inside a transaction that then
5122:         # raises is rolled back with it.  This entry is public and had no gate at all,
5123:         # so a hierarchical Mission whose plan happened to contain only primitives
5124:         # could be judged COMPLETED without its root ``GoalResolution`` ever being
5125:         # formed — the one thing §21.5's "wrongly declared complete = 0" turns on.
5126:         # What stopped it in practice was that a compound row is materialised BLOCKED
5127:         # and can never reach COMPLETED, which is a coincidence of the display status
5128:         # and not a rule.
5129:         self._require_root_resolution(mission_id)
5130:         with self._store.transaction():
5131:             mission = self._require_mission(mission_id)
5132:             if mission.status in {MissionStatus.COMPLETED, MissionStatus.FAILED}:
5133:                 return mission
5134:             all_tasks = self._store.list_tasks(mission_id)
5135:             tasks = [
5136:                 task
5137:                 for task in all_tasks
5138:                 if task.status is not TaskStatus.CANCELLED  # D5-4: superseded work is history
5139:                 and not (
5140:                     task.paused and task.status in {TaskStatus.READY, TaskStatus.BLOCKED}
5141:                 )  # R4: a paused route is not required
5142:             ]
5143:             # P2.3c part 2c / review F6, the second half: in the hierarchical mode the
5144:             # completeness of the work is read from the *Acceptances*, never from a
5145:             # sweep of ``TaskStatus`` (the first half, the root-resolution gate, ran
5146:             # before this transaction was opened so that its refusal event survives the
5147:             # refusal).  See :meth:`_require_accepted_work`.
5148:             network = self._judgment_network(mission)
5149:             if network is not None:
5150:                 self._require_accepted_work(mission, network, tasks)
5151:                 tasks = self._drop_compound_rows(network, tasks)
5152:             elif not tasks or any(task.status is not TaskStatus.COMPLETED for task in tasks):
5153:                 raise CommitRejected("mission judgment requires every live Task to be COMPLETED")
5154:             mutable_judgments = [dict(item) for item in judgments]
5155:             judgments = mutable_judgments
5156:             domain = self.domain_for(mission_id)
```

原行 5238–5270
```python
5238:                 ],
5239:                 "knowledge": [
5240:                     k.id for k in self._store.list_knowledge(mission_id, status="VERIFIED")
5241:                 ],
5242:                 "lineage": lineage(self._store, mission_id),  # D4-14 / 30-27
5243:             }
5244:             self._emit(
5245:                 "MissionSuccessJudged",
5246:                 mission_id,
5247:                 key=f"{mission_id}:{mission.version}",
5248:                 payload={"met": met, "judgments": [dict(item) for item in judgments]},
5249:             )
5250:             if met:
5251:                 from .hierarchical_dispatch import is_hierarchical
5252: 
5253:                 if is_hierarchical(mission):
5254:                     report.update(self._ledger.usage_flags(mission_id))
5255:                 done = next_mission(
5256:                     mission,
5257:                     MissionStatus.COMPLETED,
5258:                     stop_reason=str(MissionStopReason.VERIFICATION_PASSED),
5259:                     final_report=report,
5260:                 )
5261:                 self._store.update_mission(done, expected_version=mission.version)
5262:                 self._release_terminal_mission_pools(mission_id)
5263:                 self._emit(
5264:                     "MissionCompleted",
5265:                     mission_id,
5266:                     key=mission_id,
5267:                     payload={"stop_reason": done.stop_reason, "final_report": report},
5268:                 )
5269:                 return done
5270:             failed = next_mission(
```

## SDK src/agent_orchestrator/orchestrator/event_handler.py

SHA-256: `40ea93bfdc762e66ff1e195ad3b70b47675da3cc63775edc062cf25b37687273`

原行 6173–6245
```python
6173:     async def _collect_root_review(  # type: ignore[no-untyped-def]
6174:         self, intent: DispatchIntent, result, mission: Mission, text: str
6175:     ) -> None:
6176:         """A ``<critic_verdict>`` reply → the official root ``ReviewRecord`` (AER I05).
6177: 
6178:         The conclusion is the reviewer's and nothing here adjusts it: ``PASS``
6179:         becomes ``ACCEPT``, ``FAIL`` becomes ``REJECTED``, and a criterion the
6180:         reviewer reported ``met: false`` is written ``FAIL`` — there is no branch
6181:         that produces an ACCEPT out of a reply that did not say PASS.
6182: 
6183:         An unreadable reply writes **no** record.  ``parse_critic_verdict`` is strict
6184:         on purpose (AER-V04: a malformed verdict is an error, never a PASS), and an
6185:         answer we could not read is not a conclusion, so the package keeps its one
6186:         official-record slot free and the Mission reaches the idle-stall path with
6187:         ``HierarchicalRootReviewUnreadable`` written down.  Asking again with the same
6188:         anchor would spend the Mission account on the same question.
6189:         """
6190: 
6191:         from ..contracts.resolution import CriterionVerdict, ReviewVerdict
6192: 
6193:         new_mode = self._new_mode(mission)
6194:         package_id = str(intent.config.get("review_package_id", ""))
6195:         expected = [str(item) for item in intent.config.get("review_criteria", [])]
6196:         if new_mode is None:
6197:             self._settle_intent(intent, "FAILED")
6198:             self._settle_service_if_known(intent.subject_id, mission.id)
6199:             return
6200:         coordinator = self._root_review(mission, new_mode)
6201:         try:
6202:             package = coordinator.semantics.get_review_package(package_id)
6203:         except StoreError as error:
6204:             self._note(f"root review reply names no stored package ({error})")
6205:             self._settle_intent(intent, "FAILED")
6206:             self._settle_service_if_known(intent.subject_id, mission.id)
6207:             return
6208:         try:
6209:             if result.state is not AgentTurnState.COMMITTED:
6210:                 raise ContractError(f"root reviewer turn failed: {dict(result.error or {})}")
6211:             verdict = parse_critic_verdict(text, expected_criteria=expected)
6212:         except (ContractError, BlockError) as error:
6213:             coordinator.record_unreadable(
6214:                 mission.id,
6215:                 package,
6216:                 detail=str(error),
6217:                 reviewer_turn_id=str(getattr(result, "turn_id", "") or intent.intent_id),
6218:             )
6219:             self._note(f"mission {mission.id}: the root review reply was unreadable ({error})")
6220:             self._settle_intent(intent, "FAILED")
6221:             self._settle_service_if_known(intent.subject_id, mission.id)
6222:             return
6223:         verdicts = {
6224:             str(item.get("criterion")): (
6225:                 CriterionVerdict.PASS if bool(item.get("met")) else CriterionVerdict.FAIL
6226:             )
6227:             for item in verdict.mission_criteria
6228:         }
6229:         try:
6230:             record = coordinator.record_review(
6231:                 mission.id,
6232:                 package,
6233:                 verdict=ReviewVerdict.ACCEPT if verdict.passed else ReviewVerdict.REJECTED,
6234:                 criterion_verdicts=verdicts,
6235:                 reviewer_agent_id=str(intent.agent_id or "root-reviewer"),
6236:                 reviewer_turn_id=str(getattr(result, "turn_id", "") or intent.intent_id),
6237:                 findings=verdict.findings,
6238:             )
6239:         except (ContractError, StoreError) as error:
6240:             self._note(f"mission {mission.id}: the root review record was refused ({error})")
6241:             self._settle_intent(intent, "FAILED")
6242:             self._settle_service_if_known(intent.subject_id, mission.id)
6243:             return
6244:         self._note(
6245:             f"mission {mission.id}: root review {record.record_id} concluded {record.verdict!s}"
```

原行 11678–11784
```python
11678: 
11679:     async def _ask_root_reviewer(self, mission: Mission, coordinator: Any, package: Any) -> bool:
11680:         """Create the root reviewer's intent.  Idempotent per review package.
11681: 
11682:         ``kind="plan"`` rather than ``"critic"``: the ``critic`` kind is the Task
11683:         Critic's, which is bound to an Attempt and collected by the attempt runner,
11684:         and a review of the whole composition has no Attempt.  The *role* is its own
11685:         (``root_reviewer``) and so is the account — ``ReviewAccount.MISSION``, never a
11686:         Task budget (§13 v1.4, §18.5).
11687:         """
11688: 
11689:         from ..runtime.role_templates import ROOT_REVIEWER
11690:         from .root_review import MAX_ROOT_REVIEW_ASKS, ROOT_REVIEW_UNREADABLE
11691: 
11692:         # An unreadable reply is not an answer, so this is not asking the same
11693:         # question twice: it is the same question put once more, with the parse error
11694:         # attached, exactly as ``critic_schema_retry_feedback`` does for the Task
11695:         # Critic.  Bounded at two — a model that cannot produce the block twice is a
11696:         # deployment problem, and the Mission goes to the idle-stall path with the
11697:         # reason written down rather than spending the Mission account in a loop.
11698:         unreadable = [
11699:             event
11700:             for event in self.store.list_events(mission.id)
11701:             if event.type == ROOT_REVIEW_UNREADABLE
11702:             and str((event.payload or {}).get("package_id", "")) == str(package.package_id)
11703:         ]
11704:         ordinal = len(unreadable) + 1
11705:         if ordinal > MAX_ROOT_REVIEW_ASKS:
11706:             return False
11707:         subject = f"{mission.id}:root-review:{package.package_id}:{ordinal}"
11708:         if self.store.get_intent_for_subject(subject) is not None:
11709:             return False
11710:         request = coordinator.request(
11711:             mission.id,
11712:             package,
11713:             schema_feedback=(
11714:                 ""
11715:                 if not unreadable
11716:                 else "上一次回答无法解析："
11717:                 + str((unreadable[-1].payload or {}).get("detail", ""))
11718:                 + "。请重新给出同一份判断，整段回答只包含一个 <critic_verdict>…</critic_verdict> 块。"
11719:             ),
11720:         )
11721:         template = self._template(ROOT_REVIEWER, mission.id)
11722:         decision = self._route_service("critic", mission.id)
11723:         config = AgentConfig(
11724:             name=f"root-reviewer-{str(package.package_id)[-8:]}",
11725:             instructions=template.instructions,
11726:             model_profile_ref=decision.profile_id,
11727:             # A root review reads the package it was handed and answers; it has no
11728:             # tools.  ``tool_names=()`` is the gate — the limit is only a bound, and
11729:             # :class:`AgentLimits` refuses a non-positive one, so a zero here raised
11730:             # ``ValueError`` and the Mission died on the way to its own review.
11731:             tool_names=(),
11732:             limits=AgentLimits(
11733:                 max_model_calls_per_turn=2,
11734:                 max_tool_calls_per_turn=1,
11735:                 turn_deadline_seconds=self._config.turn_deadline_seconds,
11736:             ),
11737:         )
11738:         message = user_message_json(json.dumps(request.to_json(), ensure_ascii=False))
11739:         try:
11740:             self.commit.create_service_intent(
11741:                 kind="plan",
11742:                 subject_id=subject,
11743:                 mission_id=mission.id,
11744:                 # §13 v1.4: the cost of a MISSION_FINAL review lands on the Mission
11745:                 # account.  ``coordinator.account`` is read from
11746:                 # ``account_for_purpose`` so the two cannot drift.
11747:                 account_id=mission_account(mission.id),
11748:                 creation_key=subject,
11749:                 input_id="attempt-input",
11750:                 input_hash=sha256_hex(message),
11751:                 config={
11752:                     "agent_config": config.to_json(),
11753:                     "message": message,
11754:                     "context_version": request.content_hash(),
11755:                     "prompt_version": template.prompt_version,
11756:                     "base_version": mission.version,
11757:                     "ordinal": 1,
11758:                     "role": "root_reviewer",
11759:                     "budget_account": str(coordinator.account),
11760:                     "review_package_id": str(package.package_id),
11761:                     "review_criteria": list(request.criterion_ids),
11762:                     **self._service_config(decision),
11763:                 },
11764:                 reservation=self._reservation(
11765:                     self._config.critic_reserve_tokens, decision.profile_id
11766:                 ),
11767:             )
11768:         except (ContractError, CommitRejected, BudgetError, RoutingUnavailable) as error:
11769:             self._note(f"mission {mission.id}: the root reviewer was not asked ({error})")
11770:             return False
11771:         self._note(f"mission {mission.id}: root reviewer asked about {package.package_id}")
11772:         return True
11773: 
11774:     async def _root_resolution_formed(
11775:         self, mission: Mission, new_mode: HierarchicalDispatch
11776:     ) -> bool:
11777:         """Whether this Mission's root ``GoalResolution`` stands (P2.3c part 2).
11778: 
11779:         Offers it once when it does not, and reports the refusal.  The decision itself
11780:         is entirely the Commit Service's — this is the trigger, not a second judge —
11781:         and the delivery contract is read from the Mission's requirements so the stage
11782:         a root must reach is the one the goal asked for rather than one this call
11783:         invented (AER §6.1).
11784:         """
```

## SDK src/agent_orchestrator/orchestrator/root_review.py

SHA-256: `99134dfde726caa5d5bef2ed19917d42444b2d87ff433e756248d95ff29ce76b`

原行 895–960
```python
895:     def cut(self, mission_id: str, *, now_ms: int) -> ReviewPackage:
896:         """Freeze the root ``MISSION_FINAL`` anchor, and the licence that spends it.
897: 
898:         Refuses unless :meth:`state` says a cut is required, so "cut it anyway" is
899:         not an available move: a package cut while one is live and fresh would give
900:         ``root_resolution_inputs`` two anchors for one root, and which one wins
901:         would be a matter of ordering rather than of judgement.
902: 
903:         What it writes: a ``RequirementsRevision`` for the root's own criteria (re-used
904:         rather than re-published when the content is unchanged, so the revision number
905:         moves exactly when the content does), the ``ReviewPackage``, the
906:         ``purpose=ACCEPT`` witness the root commit consumes, and one
907:         :data:`ROOT_REVIEW_CUT` event recording everything the cut was made over.
908: 
909:         What it does **not** write: a ``ReviewRecord``.  There is no conclusion yet.
910:         """
911: 
912:         state = self.state(mission_id)
913:         if not state.needs_cut:
914:             raise ContractError(
915:                 f"the root review of mission {mission_id!r} is {state.status!s}, not a cut this "
916:                 f"coordinator may make ({state.detail}); a second live package would leave "
917:                 "which review the root resolves from a matter of row order"
918:             )
919:         binding = self._root_binding(mission_id)
920:         assert binding is not None  # state() answered UNREADABLE_PLAN otherwise
921:         semantics = self.semantics
922:         previous = state.package
923:         if previous is not None:
924:             self._supersede(mission_id, previous, reasons=state.stale_reasons)
925:         requirements = self._requirements(mission_id, binding)
926:         contributions = self.contributions(mission_id)
927:         producers = self.producer_agent_ids(mission_id, contributions)
928:         manifest = self._manifest_hash(mission_id, str(binding.task_id))
929:         package = ReviewPackage(
930:             package_id=ReviewPackageId(
931:                 "pkg-root-"
932:                 + content_hash_of(
933:                     {
934:                         "mission": str(mission_id),
935:                         "task": str(binding.task_id),
936:                         "requirements": int(requirements.revision),
937:                         "contributions": list(contributions),
938:                         "cut": self.cuts_for_revision(mission_id, int(requirements.revision)) + 1,
939:                     }
940:                 )[:32]
941:             ),
942:             purpose=ReviewPurpose.MISSION_FINAL,
943:             binding=ReviewBinding(
944:                 mission_id=mission_id,
945:                 obligation_id=str(binding.obligation_id),
946:                 subject_ref=TypedRef(
947:                     kind=TypedRefKind.TASK,
948:                     id=str(binding.task_id),
949:                     revision=int(binding.contract_revision),
950:                     content_hash=binding.contract_hash,
951:                 ),
952:                 requirements_revision=int(requirements.revision),
953:                 input_manifest_hash=manifest,
954:                 policy_ref=TypedRef(
955:                     kind=TypedRefKind.SOURCE,
956:                     id=ROOT_REVIEW_POLICY,
957:                     revision=1,
958:                     content_hash=content_hash_of(ROOT_REVIEW_POLICY),
959:                 ),
960:             ),
```

原行 1401–1470
```python
1401:     def record_review(
1402:         self,
1403:         mission_id: str,
1404:         package: ReviewPackage,
1405:         *,
1406:         verdict: ReviewVerdict,
1407:         criterion_verdicts: Mapping[str, CriterionVerdict],
1408:         reviewer_agent_id: str,
1409:         reviewer_turn_id: str,
1410:         findings: Sequence[Mapping[str, Any]] = (),
1411:     ) -> ReviewRecord:
1412:         """Freeze the reviewer's conclusion.  This module never supplies one.
1413: 
1414:         ``verdict`` and ``criterion_verdicts`` both come from the reply the caller
1415:         parsed.  There is no default and no fallback: a criterion the reviewer did
1416:         not judge is written ``UNKNOWN`` — the enum's word for "not judged" — and the
1417:         acceptance formula answers for it, rather than this side answering on the
1418:         reviewer's behalf.  AER I05 is a property of that shape: nothing here can
1419:         turn "the review finished" into "the review passed".
1420: 
1421:         A non-ACCEPT conclusion is recorded *and* announced
1422:         (:data:`ROOT_REVIEW_REJECTED`), because §9.1 sends it to a decision table and
1423:         a silent retry is the one response that table does not have.
1424:         """
1425: 
1426:         resolved = ReviewVerdict(verdict)
1427:         refuse_self_contradicting_accept(resolved, criterion_verdicts)
1428:         limitations = tuple(
1429:             f"{one.get('severity', 'minor')}: {str(one.get('detail', ''))[:200]}"
1430:             for one in findings
1431:         )
1432:         outcomes = tuple(
1433:             self._outcome(
1434:                 item.criterion_id,
1435:                 criterion_verdicts.get(str(item.criterion_id), CriterionVerdict.UNKNOWN),
1436:                 limitations,
1437:             )
1438:             for item in package.criteria
1439:         )
1440:         from .scoped_content_review import uses_completion_protocol
1441: 
1442:         if uses_completion_protocol(self.store, mission_id):
1443:             from dataclasses import replace
1444:             from .completion_status import current_effect_proofs
1445: 
1446:             proofs = current_effect_proofs(self.store, mission_id)
1447:             anchors = {ref.id for ref in package.child_acceptance_refs}
1448:             outcomes = tuple(replace(outcome, evidence_refs=tuple(
1449:                 ref for proof in proofs if proof["acceptance_id"] in anchors
1450:                 and outcome.criterion_id in proof["criterion_ids"]
1451:                 for ref in proof["evidence_refs"])) for outcome in outcomes)
1452:         record = ReviewRecord(
1453:             record_id=ReviewRecordId(
1454:                 "rec-root-"
1455:                 + content_hash_of(
1456:                     {"package": str(package.package_id), "turn": str(reviewer_turn_id)}
1457:                 )[:32]
1458:             ),
1459:             package_id=package.package_id,
1460:             purpose=package.purpose,
1461:             binding=package.binding,
1462:             reviewer_agent_id=str(reviewer_agent_id),
1463:             reviewer_turn_id=str(reviewer_turn_id),
1464:             evidence_manifest_hash=content_hash_of(
1465:                 {
1466:                     "contributions": [str(item.id) for item in package.child_acceptance_refs],
1467:                     "findings": [dict(item) for item in findings],
1468:                 }
1469:             ),
1470:             criteria=outcomes,
```

## SDK src/agent_orchestrator/storage/schema.py

SHA-256: `f63f83c87058b6ffd86b03cf2487ad55e9f23b0d9368c4c8f36375b1b6ddd806`

原行 109–131
```python
109: CREATE TABLE dispatch_intents (
110:  intent_id TEXT PRIMARY KEY,
111:  kind TEXT NOT NULL,
112:  subject_id TEXT NOT NULL UNIQUE,
113:  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
114:  state TEXT NOT NULL,
115:  version INTEGER NOT NULL,
116:  creation_key TEXT NOT NULL UNIQUE,
117:  input_id TEXT NOT NULL,
118:  input_hash TEXT NOT NULL,
119:  config_json TEXT NOT NULL,
120:  expected_turn_id TEXT,
121:  agent_id TEXT,
122:  receipt_json TEXT,
123:  lease_owner TEXT,
124:  lease_expires_at REAL,
125:  replays INTEGER NOT NULL DEFAULT 0,
126:  created_at REAL NOT NULL,
127:  updated_at REAL NOT NULL
128: ) STRICT;
129: CREATE INDEX dispatch_intents_state_idx ON dispatch_intents(state, created_at);
130: 
131: CREATE TABLE results (
```

原行 224–234
```python
224: CREATE TABLE commit_receipts (
225:  commit_id TEXT PRIMARY KEY,
226:  kind TEXT NOT NULL,
227:  subject_id TEXT NOT NULL,
228:  base_version INTEGER,
229:  proposal_hash TEXT NOT NULL,
230:  receipt_json TEXT NOT NULL,
231:  applied_at REAL NOT NULL
232: ) STRICT;
233: """
234: 
```

原行 544–570
```python
544: MIGRATIONS: tuple[Migration, ...] = (
545:     Migration(1, "orchestrator-step02", DDL_V1),
546:     Migration(2, "orchestrator-step04", DDL_V2),
547:     Migration(3, "orchestrator-step05", DDL_V3),
548:     Migration(4, "orchestrator-step06", DDL_V4),
549:     Migration(5, "orchestrator-step07", DDL_V5),
550:     Migration(6, "orchestrator-step09", DDL_V6),
551:     Migration(7, "orchestrator-p32", DDL_V7),
552:     Migration(8, "orchestrator-p33-domains", DDL_V8),
553:     Migration(9, "orchestrator-p33-sources", DDL_V9),
554:     Migration(10, "orchestrator-p33-assessments", DDL_V10),
555:     Migration(11, "orchestrator-p35-provider-admission", DDL_V11),
556:     Migration(12, "orchestrator-p34-selection", DDL_V12),
557:     Migration(13, "orchestrator-tail-reservations", DDL_V13),
558:     Migration(14, "orchestrator-p34-fragments", DDL_V14),
559:     Migration(15, "orchestrator-mission-system-tail", DDL_V15),
560:     Migration(16, "orchestrator-full-target-htn", DDL_V16),
561:     Migration(17, "orchestrator-full-target-acceptance-receipts", DDL_V17),
562:     Migration(18, "orchestrator-full-target-witness-subject", DDL_V18),
563:     Migration(19, "orchestrator-planning-decision-v1", DDL_V19),
564:     Migration(20, "orchestrator-h1h-admission-seams", DDL_V20),
565:     Migration(21, "orchestrator-operation-seams", DDL_V21),
566:     Migration(22, "orchestrator-operation-completion", DDL_V22),
567:     Migration(23, "orchestrator-method-evaluations", DDL_V23),
568:     Migration(24, "orchestrator-planning-human-requests", DDL_V24),
569: )
570: SCHEMA_VERSION = MIGRATIONS[-1].version
```

## SDK src/agent_orchestrator/storage/htn_schema.py

SHA-256: `0be9e350e462af2965071c1c7ffbc82fedc43948193f3dfbb0628f8ef938b755`

原行 319–363
```python
319: CREATE TABLE review_packages (
320:  package_id TEXT PRIMARY KEY,
321:  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
322:  purpose TEXT NOT NULL CHECK(purpose IN
323:   ('TASK_CONTENT','METHOD_PLAN','COMPOSITION','ACTION_PROPOSAL','OPERATION_OUTCOME',
324:    'MISSION_FINAL')),
325:  review_account TEXT NOT NULL CHECK(review_account IN
326:   ('task','mission_planning','parent_compound_task','operation_task','mission')),
327:  obligation_id TEXT NOT NULL,
328:  subject_kind TEXT NOT NULL,
329:  subject_id TEXT NOT NULL,
330:  requirements_revision INTEGER NOT NULL CHECK(requirements_revision>=0),
331:  input_manifest_hash TEXT NOT NULL,
332:  method_instance_id TEXT,
333:  package_hash TEXT NOT NULL CHECK(length(package_hash)=64),
334:  package_json TEXT NOT NULL,
335:  created_at REAL NOT NULL
336: ) STRICT;
337: CREATE INDEX review_packages_mission_idx ON review_packages(mission_id, purpose, created_at);
338: CREATE INDEX review_packages_subject_idx
339:  ON review_packages(mission_id, subject_kind, subject_id);
340: 
341: CREATE TABLE review_records (
342:  record_id TEXT PRIMARY KEY,
343:  package_id TEXT NOT NULL REFERENCES review_packages(package_id),
344:  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
345:  purpose TEXT NOT NULL,
346:  reviewer_agent_id TEXT NOT NULL,
347:  reviewer_turn_id TEXT NOT NULL,
348:  verdict TEXT NOT NULL CHECK(verdict IN ('ACCEPT','REWORK','INCONCLUSIVE','REJECTED')),
349:  evidence_manifest_hash TEXT NOT NULL,
350:  official INTEGER NOT NULL CHECK(official IN (0,1)),
351:  record_hash TEXT NOT NULL CHECK(length(record_hash)=64),
352:  record_json TEXT NOT NULL,
353:  created_at REAL NOT NULL
354: ) STRICT;
355: CREATE UNIQUE INDEX review_records_official_idx ON review_records(package_id)
356:  WHERE official = 1;
357: CREATE INDEX review_records_package_idx ON review_records(package_id, created_at);
358: CREATE INDEX review_records_reviewer_idx ON review_records(mission_id, reviewer_agent_id);
359: 
360: CREATE TABLE criterion_evaluations (
361:  review_id TEXT NOT NULL REFERENCES review_records(record_id),
362:  criterion_id TEXT NOT NULL,
363:  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
```

原行 466–510
```python
466: CREATE TABLE justification_sets (
467:  set_id TEXT PRIMARY KEY,
468:  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
469:  subject_kind TEXT NOT NULL,
470:  subject_id TEXT NOT NULL,
471:  member_revision INTEGER NOT NULL CHECK(member_revision>=0),
472:  member_digest TEXT NOT NULL CHECK(length(member_digest)=64),
473:  rule_ref TEXT,
474:  detail_json TEXT NOT NULL,
475:  created_at REAL NOT NULL
476: ) STRICT;
477: CREATE UNIQUE INDEX justification_sets_digest_idx
478:  ON justification_sets(mission_id, subject_kind, subject_id, member_digest);
479: CREATE INDEX justification_sets_subject_idx
480:  ON justification_sets(mission_id, subject_kind, subject_id, member_revision);
481: 
482: CREATE TABLE support_members (
483:  set_id TEXT NOT NULL REFERENCES justification_sets(set_id),
484:  member_kind TEXT NOT NULL,
485:  member_id TEXT NOT NULL,
486:  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
487:  polarity INTEGER NOT NULL CHECK(polarity IN (0,1)),
488:  member_revision TEXT,
489:  member_json TEXT NOT NULL,
490:  created_at REAL NOT NULL,
491:  PRIMARY KEY(set_id, member_kind, member_id)
492: ) STRICT;
493: CREATE INDEX support_members_reverse_idx
494:  ON support_members(mission_id, member_kind, member_id);
495: 
496: CREATE TABLE validity_epochs (
497:  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
498:  scope_id TEXT NOT NULL,
499:  epoch INTEGER NOT NULL CHECK(epoch>=0),
500:  bumped_by TEXT NOT NULL,
501:  updated_at REAL NOT NULL,
502:  PRIMARY KEY(mission_id, scope_id)
503: ) STRICT;
504: 
505: CREATE TABLE validity_dirty (
506:  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
507:  subject_kind TEXT NOT NULL,
508:  subject_id TEXT NOT NULL,
509:  epoch INTEGER NOT NULL CHECK(epoch>=0),
510:  scope_id TEXT NOT NULL,
```

## SDK src/agent_orchestrator/storage/htn_store.py

SHA-256: `b53412a27bd5cb37f2863f6643a1176a784d1429968097c97d757b395242a3d3`

原行 1325–1356
```python
1325:     ) -> tuple[ValidityWitness, ...]:
1326:         clauses = ["mission_id = ?"]
1327:         values: list[Any] = [identifier(mission_id, "mission_id")]
1328:         if scope_id is not None:
1329:             clauses.append("scope_id = ?")
1330:             values.append(identifier(scope_id, "scope_id"))
1331:         if subject is not None:
1332:             clauses.append("subject_digest = ?")
1333:             values.append(str(subject))
1334:         rows = self._store.connection.execute(
1335:             f"SELECT witness_json FROM validity_witnesses WHERE {' AND '.join(clauses)}"
1336:             " ORDER BY as_of_ms, witness_id",
1337:             tuple(values),
1338:         ).fetchall()
1339:         return tuple(ValidityWitness.from_json(json.loads(row[0])) for row in rows)
1340: 
1341:     def insert_observation(
1342:         self, mission_id: str, observation: ObservationRecord, *, scope_id: str = "mission"
1343:     ) -> ObservationRecord:
1344:         if not isinstance(observation, ObservationRecord):
1345:             raise StoreConflict("insert_observation expects an ObservationRecord")
1346:         mission = identifier(mission_id, "mission_id")
1347:         self._insert(
1348:             "INSERT INTO observations(observation_id,mission_id,proposition_key,polarity,scope_id,"
1349:             "source_kind,source_id,coverage,observer_id,observed_at_ms,recorded_at_ms,"
1350:             "query_watermark_ms,valid_until_ms,observation_json,created_at)"
1351:             " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
1352:             (
1353:                 observation.observation_id,
1354:                 mission,
1355:                 observation.proposition_key,
1356:                 1 if observation.polarity else 0,
```

原行 1380–1405
```python
1380: 
1381:     def list_observations(
1382:         self, mission_id: str, *, proposition_key: str | None = None
1383:     ) -> tuple[ObservationRecord, ...]:
1384:         clauses = ["mission_id = ?"]
1385:         values: list[Any] = [identifier(mission_id, "mission_id")]
1386:         if proposition_key is not None:
1387:             clauses.append("proposition_key = ?")
1388:             values.append(str(proposition_key))
1389:         rows = self._store.connection.execute(
1390:             f"SELECT observation_json FROM observations WHERE {' AND '.join(clauses)}"
1391:             " ORDER BY observed_at_ms, observation_id",
1392:             tuple(values),
1393:         ).fetchall()
1394:         return tuple(ObservationRecord.from_json(json.loads(row[0])) for row in rows)
1395: 
1396:     def insert_justification_set(
1397:         self,
1398:         mission_id: str,
1399:         set_id: str,
1400:         *,
1401:         subject_kind: str,
1402:         subject_id: str,
1403:         members: Sequence[tuple[TypedRef, bool]],
1404:         member_revision: int = 0,
1405:         rule_ref: str | None = None,
```

## SDK src/agent_orchestrator/memory/source_dependencies.py

SHA-256: `7e790f6e3fc1deb84b74bcf323edd158f8f438de528459836a6d3a73f61d5825`

原行 4–16
```python
4: """Read-only source lineage and a separate, current-source acceptance fence.
5: 
6: Historical attribution is not regraded when a source changes. Dependency expansion
7: keeps every version, while currentness is queried separately. No source text is
8: read except through EvidenceResolver.read_source at the direct-citation fence.
9: """
10: 
11: from __future__ import annotations
12: 
13: from collections.abc import Mapping, Sequence
14: from typing import TYPE_CHECKING, Any
15: 
16: from ..artifacts.store import ArtifactStore
```

原行 96–115
```python
96:     used_knowledge: Sequence[str],
97: ) -> tuple[dict[str, tuple[str, ...]], list[dict[str, Any]]]:
98:     """Union actual validated refs and existing Knowledge, without backfilling it.
99: 
100:     The caller supplies system-validated refs for a new projection. Historical refs
101:     must also agree with the original accepted assessment receipts. Active traversal
102:     detects cycles; completed-node memoization permits shared diamond dependencies.
103:     """
104:     # Local import avoids assessments -> deterministic_checks -> KnowledgeIndex cycle.
105:     from ..verification.assessments import accepted_assessments_for
106: 
107:     try:
108:         mission = store.get_mission(mission_id)
109:         if mission is None:
110:             return {}, [_issue("ERROR", "mission_unavailable")]
111:         domain = _domain(store, mission_id)
112:     except Exception:
113:         return {}, [_issue("ERROR", "source_provenance_unavailable")]
114:     mappings: list[Mapping[str, Sequence[str]]] = []
115:     issues: list[dict[str, Any]] = []
```

## SDK src/agent_orchestrator/verification/verifier_router.py

SHA-256: `b8115330ffeec1867c14ee69f4c973d8d653f6fc6304cf561e70702209fc17e2`

原行 4–22
```python
4: 
5: """Verifier Router (§14.1, ORCH §12.4, plan D9/D23).
6: 
7: The Task Contract's ``verification_policy`` names the layers that *must* run.
8: Layers are recorded in the §14.1 order (format → rule → critic → test); the first required
9: layer that FAILs or ERRORs short-circuits the rest (D23).  Layers the policy did
10: not request are recorded as ``NOT_REQUIRED`` — never as PASS.  A layer that was
11: requested but could not run (e.g. the Critic's verdict was unreadable) is
12: ``ERROR`` and blocks acceptance exactly like a FAIL: "未运行的必需层不能算 PASS".
13: 
14: The Critic layer is executed by a callback supplied by the orchestrator (it needs
15: a dispatch intent, budget and the Agent bridge); everything else is local.
16: When both Critic and code_test are required, code_test executes after the deterministic
17: gates and before Critic so its independent output can be reviewed. Its result is
18: recorded at the existing code_test position.
19: """
20: 
21: from __future__ import annotations
22: 
```

原行 73–110
```python
73: class VerifierRouter:
74:     def __init__(
75:         self,
76:         *,
77:         test_timeout: float = 120.0,
78:         local_code_execution: bool = True,
79:         executor: Any = None,
80:         domain: Any = None,
81:     ) -> None:
82:         self._test_timeout = test_timeout
83:         self._local_code_execution = local_code_execution  # host support 0.9.8
84:         self._executor = executor  # P3.2 D2: what code_test runs through
85:         self._domain = domain  # P3.3 D1: the Mission's frozen domain profile
86: 
87:     async def verify(
88:         self,
89:         *,
90:         mission: Mission,
91:         task: Task,
92:         envelope: ResultEnvelope,
93:         artifacts: Sequence[Artifact],
94:         verification_copy: Workspace,
95:         client_result_id: str | None,
96:         run_critic: CriticRunner,
97:         recorder: Callable[[LayerResult], Awaitable[None]] | None = None,
98:         tampered: Sequence[str] = (),
99:         knowledge: KnowledgeIndex | None = None,
100:         require_synthesis_knowledge: bool = True,
101:         action_problems: Sequence[str] | None = None,
102:         human: Mapping[str, Any] | None = None,
103:         reuse: Mapping[str, LayerResult] | None = None,
104:         needs_human_allowed: bool = True,
105:         ablated: frozenset[str] = frozenset(),
106:         domain: DomainProfileV1 | None = None,
107:         assessment_binding: AssessmentBindingV1 | None = None,
108:         evidence_resolver: EvidenceResolver | None = None,
109:     ) -> Verdict:
110:         actual_domain = domain if domain is not None else self._domain
```

## SDK src/agent_orchestrator/artifacts/store.py

SHA-256: `fc06e35e04d63b233716d5d27e686b32c1b9ef3aafbb5d79413d7393b8bc2093`

原行 93–130
```python
93:         """Whether ``path`` names a file of this store (not whether it exists)."""
94: 
95:         return Path(path).parent == self._root / HASH_DIR
96: 
97:     def put_bytes(self, data: bytes) -> str:
98:         """Write once: the same bytes land at the same address; a missing, tampered or
99:         substituted (symlink) file at that address is replaced atomically."""
100: 
101:         content_hash = hashlib.sha256(data).hexdigest()
102:         target = self.path_for(content_hash)
103:         try:
104:             if hashlib.sha256(read_nofollow(target)).hexdigest() == content_hash:
105:                 return content_hash
106:         except ArtifactStoreError:
107:             pass
108:         target.parent.mkdir(parents=True, exist_ok=True)
109:         temporary = target.parent / f".tmp-{uuid.uuid4().hex}"
110:         fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
111:         try:
112:             with os.fdopen(fd, "wb") as handle:
113:                 handle.write(data)
114:                 handle.flush()
115:                 os.fsync(handle.fileno())
116:             os.chmod(temporary, 0o444)
117:             os.replace(temporary, target)
118:         except BaseException:
119:             temporary.unlink(missing_ok=True)
120:             raise
121:         return content_hash
122: 
123:     def put_file(self, source: Path) -> str:
124:         return self.put_bytes(read_nofollow(Path(source)))
125: 
126:     def read(self, content_hash: str) -> bytes:
127:         data = read_nofollow(self.path_for(content_hash))
128:         if hashlib.sha256(data).hexdigest() != content_hash:
129:             raise ArtifactStoreError("hash_mismatch", content_hash)
130:         return data
```

## SDK src/agent_orchestrator/storage/offline_backup.py

SHA-256: `cd9428be99397666e9bfbdebf6c545c6d22377d0f25d8d7dd3a955888d905231`

原行 611–679
```python
611: def restore_offline(bundle: Path, *, destination: Path, expected_manifest_sha256: str) -> dict:
612:     """Restore to a new isolated root; do not run any Agent or external lookup."""
613:     root = Path(bundle).resolve(strict=True)
614:     target = _destination(destination, outside=root)
615:     stage = None
616:     try:
617:         with _root_lock(root, explicit=root / LOCK):
618:             if (
619:                 not _hex(expected_manifest_sha256)
620:                 or _digest(root / MANIFEST) != expected_manifest_sha256
621:             ):
622:                 raise OfflineBackupError("manifest_hash_differs")
623:             manifest = json.loads((root / MANIFEST).read_text())
624:             if manifest["protocol"] != PROTOCOL:
625:                 raise OfflineBackupError("manifest_protocol")
626:             files = manifest["files"]
627:             actual = {p.relative_to(root).as_posix() for p in _files(root)} - {LOCK, MANIFEST}
628:             if actual != set(files):
629:                 raise OfflineBackupError("file_inventory_differs")
630:             names = [item["path"] for item in manifest["databases"]]
631:             profiles = [
632:                 item["profile_id"] for item in manifest["databases"] if item["role"] == "execution"
633:             ]
634:             if len(set(names)) != len(names) or len(set(profiles)) != len(profiles):
635:                 raise OfflineBackupError("duplicate_database_identity")
636:             if not set(names) <= set(files) or [
637:                 d["path"] for d in manifest["databases"] if d["role"] == "orchestrator"
638:             ] != ["orchestrator.db"]:
639:                 raise OfflineBackupError("database_manifest_invalid")
640:             for item in manifest["databases"]:
641:                 _relative(item["path"])
642:                 if item["role"] == "orchestrator":
643:                     continue
644:                 profile_id = item["profile_id"]
645:                 expected_name = (
646:                     "execution.db" if profile_id == "default" else f"execution-{profile_id}.db"
647:                 )
648:                 if (
649:                     item["role"] != "execution"
650:                     or item["path"] != expected_name
651:                     or item["profile"]["profile_id"] != profile_id
652:                 ):
653:                     raise OfflineBackupError("execution_profile_identity_differs")
654:             for rel, metadata in files.items():
655:                 path = root / _relative(rel)
656:                 if path.stat().st_size != metadata["size"] or _digest(path) != metadata["sha256"]:
657:                     raise OfflineBackupError("file_hash_differs:" + rel)
658:             stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.offline-", dir=target.parent))
659:             with _root_lock(stage, explicit=stage / LOCK):
660:                 for rel in manifest["directories"]:
661:                     (stage / _relative(rel)).mkdir(parents=True, exist_ok=True, mode=0o700)
662:                 for rel in files:
663:                     _copy_file(root / rel, stage / rel)
664:                 for item in manifest["databases"]:
665:                     with _connection(stage / item["path"]) as db:
666:                         if _schema(db, item["role"]) != item["schema"]:
667:                             raise OfflineBackupError("schema_differs")
668:                         fingerprints, _ = _formal(db, source_root=manifest["source_root"])
669:                         if fingerprints != manifest["formal_fingerprints"][item["path"]]:
670:                             raise OfflineBackupError("formal_identity_differs")
671:                         if item["role"] == "execution":
672:                             if _read_context_identity(stage / item["path"]) != item["profile"].get(
673:                                 "runtime_context"
674:                             ):
675:                                 raise OfflineBackupError("context_identity_differs")
676:                 with ExitStack() as readers:
677:                     connections = {
678:                         item["path"]: readers.enter_context(_connection(stage / item["path"]))
679:                         for item in manifest["databases"]
```

## SDK src/agent_orchestrator/api/facade.py

SHA-256: `f18a824e9a181e5d7c755f6eb97e4db6a23358c46842f364fbfc0ea46e8cfbc7`

原行 88–124
```python
88: DECISIONS = ("approve", "reject", "review_pass", "review_fail", "arbitrate")
89: TAKEOVER_ACTIONS = ("stop", "retry_with_note")
90: NOT_FOUND = "no such object for this caller"
91: 
92: 
93: class FacadeError(ValueError):
94:     """A refused request; ``code`` is stable for products to map."""
95: 
96:     def __init__(self, code: str, message: str) -> None:
97:         super().__init__(message)
98:         self.code = code
99: 
100: 
101: class MissionControlV1:
102:     def __init__(self, orchestrator: Any, *, tenant_id: str, principal: Principal) -> None:
103:         if not str(tenant_id).strip():
104:             raise ValueError("a tenant is required")
105:         self._orchestrator = orchestrator
106:         self._tenant = str(tenant_id)
107:         self._principal = principal
108:         self._approvals = ApprovalApi(
109:             orchestrator.commit, principal, deployment=orchestrator.config.deployment_policy
110:         )
111: 
112:     @property
113:     def _store(self) -> Any:
114:         return self._orchestrator.store
115: 
116:     # ------------------------------------------------------------ ownership
117:     def _mission(self, mission_id: object) -> Any:
118:         mission = self._store.get_mission(str(mission_id))
119:         if mission is None or mission.tenant_id != self._tenant:
120:             raise FacadeError("not_found", NOT_FOUND)
121:         return mission
122: 
123:     def _owner_of(self, target_id: object) -> Any:
124:         target = str(target_id)
```

## Host backend/deskpet/orchestration/handlers.py

SHA-256: `b4becbdca85d6d5da4670bcd553441e24177360e1d0378a483519994971362ec`

```text
11: 
12: from __future__ import annotations
13: 
14: import asyncio
15: import logging
16: from collections.abc import Awaitable, Callable, Mapping
17: from typing import Any
18: 
19: logger = logging.getLogger(__name__)
20: 
21: MESSAGE_TYPES = (
22:     "orchestration_status",
23:     "mission_create",
24:     "mission_create_with_sources",
25:     "mission_operation_completion_approve",
26:     "mission_planning_answer",
27:     "mission_planning_authorization",
28:     "mission_operation_intent_submit",
29:     "mission_operation_intent_status",
30:     "mission_source_register",
31:     "mission_source_supersede",
32:     "mission_source_revoke",
33:     "mission_citation_read",
34:     "mission_list",
35:     "mission_get",
36:     "mission_events",
37:     "mission_cancel",
38:     "mission_approval_list",
39:     "mission_approval_decide",
```

```text
204:     return service.mission_diagnostics(dict(body))
205: 
206: 
207: def _support_export(service: Any, body: Mapping[str, Any]) -> Any:
208:     return service.mission_diagnostics(dict(body), export=True)
209: 
210: 
211: _ACTIONS: dict[str, Callable[[Any, Mapping[str, Any]], Any | Awaitable[Any]]] = {
212:     "mission_create": _create,
213:     "mission_create_with_sources": _create_with_sources,
214:     "mission_operation_completion_approve": _completion_approve,
215:     "mission_planning_authorization": lambda service, body: service.planning_authorization(dict(body)),
216:     "mission_planning_answer": lambda service, body: service.answer_planning_question(dict(body)),
217:     "mission_operation_intent_submit": _operation_submit,
218:     "mission_operation_intent_status": _operation_status,
219:     "mission_source_register": _source_register,
220:     "mission_source_supersede": _source_supersede,
221:     "mission_source_revoke": _source_revoke,
222:     "mission_citation_read": _citation,
223:     "mission_list": _list,
224:     "mission_get": _get,
225:     "mission_events": _events,
226:     "mission_cancel": _cancel,
227:     "mission_approval_list": _approvals,
228:     "mission_approval_decide": _decide,
229:     "mission_takeover": _takeover,
230:     "mission_comment": _comment,
```

## Host backend/deskpet/orchestration/service.py

SHA-256: `2d7f43ac063a5d2c7b5a51b4ea76816d865934addf928be390c8ea602e0d9e8b`

```text
295:                if self.settings.local_model_profile else {}),
296:         )
297:         self._orchestrator = Orchestrator(
298:             self._config, provider, owner=self.owner, connectors=self._connectors,
299:             **self._runtime_options,
300:         )
301:         await self._orchestrator.__aenter__()
302:         self._install_native_verifier_pressure(self._orchestrator)
303:         self._control = MissionControlV1(
304:             self._orchestrator, tenant_id=self.tenant_id, principal=self._principal
305:         )
306:         self._diagnostics_available = self._detect_diagnostics()
307:         self._policy = PolicyApi(
308:             self._orchestrator.commit, self._principal, deployment=self._deployment
309:         )
310:         if self._test_scenario == "document-ui" and os.environ.get(
311:             "DESKPET_ORCH_UI_FIXTURE_CASE"
312:         ) == "p34-approved-compare":
313:             from .native_compare import install_compare_policy
```

## Host backend/pyproject.toml

SHA-256: `ee7eaa4b08e0b8f8cd22bbb3ab2b4ba6f5800f0c5bdc729aaa95c09de39b722e`

```text
165:     # T7.3: Production uses vendored wheel with exact hash verification.
166:     # 2026-08-29: vendored exact Agent Runtime candidate bytes.
167:     # Wheel identity single source of truth:
168:     # deskpet/sdk_adapters/sdk_candidate.py
169:     "simple-harness-sdk==0.13.0.dev20260920",
170:     "simple-harness-service-sdk[realtime]==0.3.13",
171: ]
172: 
173: [tool.uv.sources]
174: # 2026-09-10：simple-harness-memory-sdk（认知记忆 SDK）已从 Host 整条移除，
175: # 见 plans/2026-09-10-remove-memory-sdk/journal.md。
176: # T7.3: Production uses local vendored wheel (exact Release artifact).
177: # Development can still use path dependency by uncommenting below and
178: # commenting out the wheel reference.
179: #
180: # Development (path dependency):
181: # simple-harness-sdk = { path = "../../simple-harness-sdk", editable = true }
182: #
183: # Production (vendored immutable candidate):
184: simple-harness-sdk = { path = "vendor/simple_harness_sdk-0.13.0.dev20260920-py3-none-any.whl" }
```

## Host ARCHITECTURE/TASKGRAPH.md

SHA-256: `bea180967787b5e855946bbf96bf9dd61f2f31f751c9610c85c9d25ce65eca57`

```text
1: # TaskGraph 执行网络
2: 
3: 最后更新：2026-09-22 CST。
4: 
5: ## 当前生产事实
6: 
7: TG-EXEC-2.0 / V1.2.1 **整体未完成**。新增实现仍在 ignored 隔离 SDK 派生副本，未写入 HTN candidate、未合并 SDK、未安装 Host wheel、未增加 Host route/UI、未启用 TaskGraph kernel。已有 Host `backend/deskpet/agent/task_graph.py` 属于旧任务 DAG，不能当成本计划的执行网络能力。
8: 
9: HTN/V1.4 最新事实源仍标明 Operation、完整 H1 等门禁未关闭。一次任务 idle/turn completed 不代表该依赖完成。TaskGraph 接入最终 HTN 的优先级高于继续扩展旧快照；现在保持 SOURCE_MAP_COMPLETE/H1_H_READY/TG_A_VALIDATED 未满足，TG-B/C/D/E 未进入。
10: 
11: ## 当前编码工作区（未验收）
12: 
13: 2026-09-22续：`htn-taskgraph` 定时任务已按用户要求删除。隔离SDK补齐冻结输入解码、workspace/叶子review来源、完整祖先链、新revision事件、候选代次预测与本地运行账目核对；新增实际本地执行来源集合读取，仍缺H1完整执行授权/可靠运行导入/Operation来源。另有独立Host副本接入四个只读verb和执行图视图，尚未安装到共享Host或实际启动验收。新增代码均WIP，旧组件PASS不适用；不在主体完成前开展批量测试。代码补丁和剩余装配见下方主体进度。
14: 
15: 2026-09-22起由主代理集中推进 `complete-plan-prep/body-implementation/snapshot/sdk`。新增历史Store/Attempt绑定、通知/收敛、policy/source、离线重建及只读API；原Commit、Attempt创建和Provider/Tool/Action交接已有隔离接线。实际H1来源、APPLIED、baseline和执行授权适配未接齐，Host未接入，主体整体仍未完成。遵守用户新要求，主体完成前不运行批量单元、回归或全量测试。这里只记录源码WIP，不把下文父检查点PASS继承给这些新代码。具体接线与未接端口见 [主体进度](../plans/TaskGraph/V1.2.1/BODY-WORK-IN-PROGRESS.md)。
16: 
17: ## 已验证的隔离组件
18: 
19: 原冻结 SDK 22 项迁移之上，隔离注册第23项 TaskGraph 原DDL（9表、25触发器）；23只是本快照可用号，不是最终迁移号。真实Store验证新建/升级、旧行/产物保留、真实HTN非空Plan Commit、合同多版本/方法退役、命令重放零写及SIGKILL恢复。
20: 
```
