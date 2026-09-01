"""Durable semantic evidence owners for DeepResearch v6.

The wire contracts in this module are the only release-v1 fact, inference,
batch, and assessment shapes.  The former Q1-only batch shape is intentionally
not accepted by the decoder.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..contracts import JsonValue, canonical_json, validate_json_value
from .deep_research_v6_contracts import parse_blob_ref, sha256_json
from .deep_research_v6_exact_fact import ExtractedScalarEvidence, ScalarEvidenceRequest

GENESIS_EVIDENCE_HEAD = "0" * 64
Q1_ROUTE_POLICY: dict[str, JsonValue] = {"schema_version": 1, "policy_id": "deep-research-v6-route-q1-v1"}
Q1_ADMISSION_POLICY: dict[str, JsonValue] = {"schema_version": 1, "policy_id": "deep-research-v6-admission-q1-v1", "winner_rule": "first-page-ordinal-first-binding"}
Q1_ASSESSMENT_POLICY: dict[str, JsonValue] = {"schema_version": 1, "policy_id": "deep-research-v6-assessment-q1-v1", "minimum_useful_bindings": 1, "completion_rule": "all-required-scalars-supported"}
Q1_ADMISSION_POLICY_HASH = sha256_json(Q1_ADMISSION_POLICY)
Q1_ASSESSMENT_POLICY_HASH = sha256_json(Q1_ASSESSMENT_POLICY)


def _text(value: object, name: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _digest(value: object, name: str) -> str:
    value = _text(value, name)
    assert isinstance(value, str)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be 64 lowercase hex characters")
    return value


def _ref(value: object, name: str, *, nullable: bool = False) -> str | None:
    value = _text(value, name, nullable=nullable)
    if value is not None:
        parse_blob_ref(value)
    return value


def _strings(value: object, name: str, *, sorted_unique: bool = True) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    items = tuple(_text(item, name) for item in value)
    if len(set(items)) != len(items):
        raise ValueError(f"{name} must be unique")
    if sorted_unique and items != tuple(sorted(items)):
        raise ValueError(f"{name} must be canonically sorted")
    return items  # type: ignore[return-value]


def _refs(value: object, name: str, *, sorted_unique: bool = True) -> tuple[str, ...]:
    values = _strings(value, name, sorted_unique=sorted_unique)
    for ref in values:
        parse_blob_ref(ref)
    return values


_FACT_PAYLOAD_KEYS = {
    "scalar": {"value", "canonical_unit", "time_scope", "scope", "definition"},
    "matrix_cell": {"axis_member_ids", "cell_id", "value", "canonical_unit", "time_scope", "scope", "definition", "claim_kind"},
    "collection_field": {"unique_key_values", "item_id", "field_key", "value", "canonical_unit", "as_of", "rank_inputs"},
    "claim_fact": {"claim_instance_id", "claim_kind", "facet_ids", "value", "canonical_unit", "time_scope", "scope", "definition"},
}


@dataclass(frozen=True, slots=True)
class AdmittedResearchFactV1:
    fact_id: str
    run_id: str
    spec_hash: str
    requirement_id: str
    target_kind: str
    item_or_cell_id: str | None
    field_or_facet_key: str | None
    candidate_id: str
    page_id: str
    span_id: str
    binding_id: str
    source_family_id: str
    source_tier: str
    admission_policy_hash: str
    status: str
    semantic_payload: dict[str, JsonValue]

    def __post_init__(self) -> None:
        for name in ("run_id", "requirement_id", "candidate_id", "page_id", "span_id", "binding_id", "source_family_id", "source_tier"):
            _text(getattr(self, name), name)
        _digest(self.spec_hash, "spec_hash"); _digest(self.admission_policy_hash, "admission_policy_hash")
        if self.target_kind not in _FACT_PAYLOAD_KEYS or self.status not in {"admitted", "conflicted"}:
            raise ValueError("fact target_kind/status invalid")
        if set(self.semantic_payload) != _FACT_PAYLOAD_KEYS[self.target_kind]:
            raise ValueError("fact semantic_payload keys differ")
        validate_json_value(self.semantic_payload)
        self._validate_truth_table()
        expected = "arf_" + sha256_json(self._base_json())[:24]
        if self.fact_id != expected:
            raise ValueError("fact_id mismatch")

    def _validate_truth_table(self) -> None:
        payload = self.semantic_payload
        if self.target_kind == "scalar":
            if self.item_or_cell_id is not None or self.field_or_facet_key is not None:
                raise ValueError("scalar fact target truth table differs")
        elif self.target_kind == "matrix_cell":
            if self.item_or_cell_id != payload["cell_id"] or self.field_or_facet_key != "value":
                raise ValueError("matrix fact target truth table differs")
            _strings(payload["axis_member_ids"], "axis_member_ids", sorted_unique=False)
        elif self.target_kind == "collection_field":
            if self.item_or_cell_id != payload["item_id"] or self.field_or_facet_key != payload["field_key"]:
                raise ValueError("collection fact target truth table differs")
            if not isinstance(payload["unique_key_values"], list) or not payload["unique_key_values"]:
                raise ValueError("collection unique_key_values invalid")
            rank_inputs = payload["rank_inputs"]
            if not isinstance(rank_inputs, list):
                raise ValueError("rank_inputs must be an array")
            for item in rank_inputs:
                if not isinstance(item, Mapping) or set(item) != {"field_key", "value", "missing"}:
                    raise ValueError("rank input keys differ")
                if not isinstance(item["missing"], bool) or (item["missing"] != (item["value"] is None)):
                    raise ValueError("rank input truth table differs")
        else:
            facets = _strings(payload["facet_ids"], "facet_ids")
            if payload["claim_kind"] != "policy_fact" or len(facets) != 1:
                raise ValueError("claim fact kind/facets invalid")
            if self.item_or_cell_id != payload["claim_instance_id"] or self.field_or_facet_key != facets[0]:
                raise ValueError("claim fact target truth table differs")

    def _base_json(self) -> dict[str, JsonValue]:
        return {"schema_version": 1, "run_id": self.run_id, "spec_hash": self.spec_hash, "requirement_id": self.requirement_id, "target_kind": self.target_kind, "item_or_cell_id": self.item_or_cell_id, "field_or_facet_key": self.field_or_facet_key, "candidate_id": self.candidate_id, "page_id": self.page_id, "span_id": self.span_id, "binding_id": self.binding_id, "source_family_id": self.source_family_id, "source_tier": self.source_tier, "admission_policy_hash": self.admission_policy_hash, "status": self.status, "semantic_payload": self.semantic_payload}

    def to_json(self) -> dict[str, JsonValue]:
        return {**self._base_json(), "fact_id": self.fact_id}

    @classmethod
    def create(cls, **values: Any) -> AdmittedResearchFactV1:
        values["semantic_payload"] = copy.deepcopy(dict(values["semantic_payload"]))
        provisional = cls.__new__(cls)
        for key, value in values.items(): object.__setattr__(provisional, key, value)
        return cls(fact_id="arf_" + sha256_json(provisional._base_json())[:24], **values)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> AdmittedResearchFactV1:
        keys = {"schema_version", "fact_id", "run_id", "spec_hash", "requirement_id", "target_kind", "item_or_cell_id", "field_or_facet_key", "candidate_id", "page_id", "span_id", "binding_id", "source_family_id", "source_tier", "admission_policy_hash", "status", "semantic_payload"}
        if not isinstance(value, Mapping) or set(value) != keys or value.get("schema_version") != 1 or not isinstance(value["semantic_payload"], Mapping):
            raise ValueError("AdmittedResearchFactV1 keys/version differ")
        return cls(fact_id=str(value["fact_id"]), run_id=str(value["run_id"]), spec_hash=str(value["spec_hash"]), requirement_id=str(value["requirement_id"]), target_kind=str(value["target_kind"]), item_or_cell_id=value["item_or_cell_id"], field_or_facet_key=value["field_or_facet_key"], candidate_id=str(value["candidate_id"]), page_id=str(value["page_id"]), span_id=str(value["span_id"]), binding_id=str(value["binding_id"]), source_family_id=str(value["source_family_id"]), source_tier=str(value["source_tier"]), admission_policy_hash=str(value["admission_policy_hash"]), status=str(value["status"]), semantic_payload=copy.deepcopy(dict(value["semantic_payload"])))


@dataclass(frozen=True, slots=True)
class RegisteredInferenceV1:
    inference_id: str
    run_id: str
    spec_hash: str
    requirement_id: str
    item_or_cell_id: str | None
    inference_kind: str
    facet_ids: tuple[str, ...]
    normalized_proposition: str
    proposed_premise_fact_refs: tuple[str, ...]
    premise_fact_refs: tuple[str, ...]
    premise_binding_ids: tuple[str, ...]
    model_id: str
    model_policy_ref: str
    inference_policy_hash: str
    status: str
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.inference_kind not in {"impact", "comparison", "preference", "conclusion", "limitation", "counterevidence", "uncertainty"}:
            raise ValueError("inference_kind invalid")
        for name in ("run_id", "requirement_id", "normalized_proposition", "model_id"):
            _text(getattr(self, name), name)
        _text(self.item_or_cell_id, "item_or_cell_id", nullable=True)
        _digest(self.spec_hash, "spec_hash"); _ref(self.model_policy_ref, "model_policy_ref"); _digest(self.inference_policy_hash, "inference_policy_hash")
        proposed = _refs(self.proposed_premise_fact_refs, "proposed_premise_fact_refs")
        resolved = _refs(self.premise_fact_refs, "premise_fact_refs")
        bindings = _strings(self.premise_binding_ids, "premise_binding_ids")
        _strings(self.facet_ids, "facet_ids"); reasons = _strings(self.reason_codes, "reason_codes")
        if not proposed or not set(resolved).issubset(proposed):
            raise ValueError("inference premise subset truth table differs")
        if self.status == "registered":
            if proposed != resolved or not resolved or not bindings or reasons:
                raise ValueError("registered inference truth table differs")
        elif self.status == "rejected":
            if not reasons:
                raise ValueError("rejected inference requires reason_codes")
        else:
            raise ValueError("inference status invalid")
        expected = "rin_" + sha256_json(self._base_json())[:24]
        if self.inference_id != expected:
            raise ValueError("inference_id mismatch")

    def _base_json(self) -> dict[str, JsonValue]:
        return {"schema_version": 1, "run_id": self.run_id, "spec_hash": self.spec_hash, "requirement_id": self.requirement_id, "item_or_cell_id": self.item_or_cell_id, "inference_kind": self.inference_kind, "facet_ids": list(self.facet_ids), "normalized_proposition": self.normalized_proposition, "proposed_premise_fact_refs": list(self.proposed_premise_fact_refs), "premise_fact_refs": list(self.premise_fact_refs), "premise_binding_ids": list(self.premise_binding_ids), "model_id": self.model_id, "model_policy_ref": self.model_policy_ref, "inference_policy_hash": self.inference_policy_hash, "status": self.status, "reason_codes": list(self.reason_codes)}
    def to_json(self) -> dict[str, JsonValue]: return {**self._base_json(), "inference_id": self.inference_id}
    @classmethod
    def create(cls, **values: Any) -> RegisteredInferenceV1:
        for key in ("facet_ids", "proposed_premise_fact_refs", "premise_fact_refs", "premise_binding_ids", "reason_codes"):
            values[key] = tuple(sorted(set(values.get(key, ()))))
        provisional = cls.__new__(cls)
        for key, value in values.items(): object.__setattr__(provisional, key, value)
        return cls(inference_id="rin_" + sha256_json(provisional._base_json())[:24], **values)
    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> RegisteredInferenceV1:
        keys={"schema_version","inference_id","run_id","spec_hash","requirement_id","item_or_cell_id","inference_kind","facet_ids","normalized_proposition","proposed_premise_fact_refs","premise_fact_refs","premise_binding_ids","model_id","model_policy_ref","inference_policy_hash","status","reason_codes"}
        if not isinstance(value,Mapping) or set(value)!=keys or value.get("schema_version")!=1: raise ValueError("RegisteredInferenceV1 keys/version differ")
        for key in ("facet_ids","proposed_premise_fact_refs","premise_fact_refs","premise_binding_ids","reason_codes"):
            if not isinstance(value[key],list): raise ValueError(f"{key} must be an array")
        return cls(inference_id=str(value["inference_id"]),run_id=str(value["run_id"]),spec_hash=str(value["spec_hash"]),requirement_id=str(value["requirement_id"]),item_or_cell_id=value["item_or_cell_id"],inference_kind=str(value["inference_kind"]),facet_ids=tuple(str(x) for x in value["facet_ids"]),normalized_proposition=str(value["normalized_proposition"]),proposed_premise_fact_refs=tuple(str(x) for x in value["proposed_premise_fact_refs"]),premise_fact_refs=tuple(str(x) for x in value["premise_fact_refs"]),premise_binding_ids=tuple(str(x) for x in value["premise_binding_ids"]),model_id=str(value["model_id"]),model_policy_ref=str(value["model_policy_ref"]),inference_policy_hash=str(value["inference_policy_hash"]),status=str(value["status"]),reason_codes=tuple(str(x) for x in value["reason_codes"]))


_POLICY_REF_KEYS = {"compiler", "route", "extraction", "llm_extract", "llm_repair", "llm_inference", "admission", "inference", "assessment", "claim", "quality"}
_CANDIDATE_SLOT_KEYS = {"logical_page_id", "work_group_id", "producer_outcome_ref", "bundle_ref"}
_INFERENCE_SLOT_KEYS = {"work_group_id", "effect_outcome_ref", "proposal_bundle_ref"}


def _validate_slot_array(value: object, keys: set[str], name: str) -> tuple[dict[str, JsonValue], ...]:
    if not isinstance(value, list): raise ValueError(f"{name} must be an array")
    result=[]
    for item in value:
        if not isinstance(item,Mapping) or set(item)!=keys: raise ValueError(f"{name} keys differ")
        raw=copy.deepcopy(dict(item)); validate_json_value(raw)
        for key in keys:
            if key.endswith("_ref") and raw[key] is not None: parse_blob_ref(raw[key])
        result.append(raw)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class EvidenceFactBatchV1:
    batch_id: str
    batch_kind: str
    run_id: str
    spec_hash: str
    previous_head_hash: str
    ordinal: int
    page_result_refs: tuple[str, ...]
    candidate_slot_results: tuple[dict[str, JsonValue], ...]
    inference_slot_results: tuple[dict[str, JsonValue], ...]
    admitted_fact_refs: tuple[str, ...]
    registered_inference_refs: tuple[str, ...]
    rejected_candidate_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...]
    provenance_refs: tuple[str, ...]
    policy_refs: dict[str, str]
    head_hash: str

    def __post_init__(self) -> None:
        if self.batch_kind not in {"facts", "inferences"}: raise ValueError("batch_kind invalid")
        _text(self.run_id,"run_id"); _digest(self.spec_hash,"spec_hash"); _digest(self.previous_head_hash,"previous_head_hash"); _digest(self.head_hash,"head_hash")
        if isinstance(self.ordinal,bool) or not isinstance(self.ordinal,int) or self.ordinal<0: raise ValueError("ordinal invalid")
        for name in ("page_result_refs","admitted_fact_refs","registered_inference_refs","provenance_refs"): _refs(getattr(self,name),name)
        for name in ("rejected_candidate_ids","conflict_ids"): _strings(getattr(self,name),name)
        if set(self.policy_refs)!=_POLICY_REF_KEYS: raise ValueError("policy_refs keys differ")
        for ref in self.policy_refs.values(): parse_blob_ref(ref)
        if self.batch_kind=="facts":
            if self.inference_slot_results or self.registered_inference_refs: raise ValueError("facts batch inference fields must be empty")
        elif self.page_result_refs or self.candidate_slot_results or self.admitted_fact_refs or self.rejected_candidate_ids or self.conflict_ids:
            raise ValueError("inferences batch fact fields must be empty")
        direct_refs=set(self.page_result_refs)|set(self.admitted_fact_refs)|set(self.registered_inference_refs)
        for slot in self.candidate_slot_results:
            direct_refs.add(str(slot["producer_outcome_ref"]));
            if slot["bundle_ref"] is not None: direct_refs.add(str(slot["bundle_ref"]))
        for slot in self.inference_slot_results:
            if slot["effect_outcome_ref"] is None: raise ValueError("inference slot requires effect_outcome_ref")
            direct_refs.add(str(slot["effect_outcome_ref"]));
            if slot["proposal_bundle_ref"] is not None: direct_refs.add(str(slot["proposal_bundle_ref"]))
        if not direct_refs.issubset(set(self.provenance_refs)):
            raise ValueError("batch provenance omits direct refs")
        expected_head=hashlib.sha256((self.previous_head_hash+canonical_json(self._base_json())).encode("utf-8")).hexdigest()
        if self.head_hash!=expected_head: raise ValueError("evidence fact batch head_hash mismatch")
        if self.batch_id!="efb_"+self.head_hash[:24]: raise ValueError("evidence fact batch batch_id mismatch")

    def _base_json(self)->dict[str,JsonValue]:
        return {"schema_version":1,"batch_kind":self.batch_kind,"run_id":self.run_id,"spec_hash":self.spec_hash,"previous_head_hash":self.previous_head_hash,"ordinal":self.ordinal,"page_result_refs":list(self.page_result_refs),"candidate_slot_results":copy.deepcopy(list(self.candidate_slot_results)),"inference_slot_results":copy.deepcopy(list(self.inference_slot_results)),"admitted_fact_refs":list(self.admitted_fact_refs),"registered_inference_refs":list(self.registered_inference_refs),"rejected_candidate_ids":list(self.rejected_candidate_ids),"conflict_ids":list(self.conflict_ids),"provenance_refs":list(self.provenance_refs),"policy_refs":copy.deepcopy(self.policy_refs)}
    def to_json(self)->dict[str,JsonValue]: return {**self._base_json(),"batch_id":self.batch_id,"head_hash":self.head_hash}
    @classmethod
    def create(cls,**values:Any)->EvidenceFactBatchV1:
        # Final-schema API.  Callers must persist facts/inferences before batching.
        values.setdefault("batch_kind","facts")
        for key in ("page_result_refs","admitted_fact_refs","registered_inference_refs","rejected_candidate_ids","conflict_ids","provenance_refs"):
            values[key]=tuple(sorted(set(values.get(key,()))))
        values["candidate_slot_results"]=tuple(copy.deepcopy(values.get("candidate_slot_results",())))
        values["inference_slot_results"]=tuple(copy.deepcopy(values.get("inference_slot_results",())))
        values["policy_refs"]=copy.deepcopy(dict(values["policy_refs"]))
        provisional=cls.__new__(cls)
        for key,value in values.items(): object.__setattr__(provisional,key,value)
        head=hashlib.sha256((values["previous_head_hash"]+canonical_json(provisional._base_json())).encode("utf-8")).hexdigest()
        return cls(batch_id="efb_"+head[:24],head_hash=head,**values)
    @classmethod
    def from_json(cls,value:Mapping[str,Any])->EvidenceFactBatchV1:
        keys={"schema_version","batch_id","batch_kind","run_id","spec_hash","previous_head_hash","ordinal","page_result_refs","candidate_slot_results","inference_slot_results","admitted_fact_refs","registered_inference_refs","rejected_candidate_ids","conflict_ids","provenance_refs","policy_refs","head_hash"}
        if not isinstance(value,Mapping) or set(value)!=keys or value.get("schema_version")!=1: raise ValueError("EvidenceFactBatchV1 keys/version differ")
        for key in ("page_result_refs","candidate_slot_results","inference_slot_results","admitted_fact_refs","registered_inference_refs","rejected_candidate_ids","conflict_ids","provenance_refs"):
            if not isinstance(value[key],list): raise ValueError(f"{key} must be an array")
        if not isinstance(value["policy_refs"],Mapping): raise ValueError("policy_refs must be an object")
        return cls(batch_id=str(value["batch_id"]),batch_kind=str(value["batch_kind"]),run_id=str(value["run_id"]),spec_hash=str(value["spec_hash"]),previous_head_hash=str(value["previous_head_hash"]),ordinal=value["ordinal"],page_result_refs=tuple(str(x) for x in value["page_result_refs"]),candidate_slot_results=_validate_slot_array(value["candidate_slot_results"],_CANDIDATE_SLOT_KEYS,"candidate_slot_results"),inference_slot_results=_validate_slot_array(value["inference_slot_results"],_INFERENCE_SLOT_KEYS,"inference_slot_results"),admitted_fact_refs=tuple(str(x) for x in value["admitted_fact_refs"]),registered_inference_refs=tuple(str(x) for x in value["registered_inference_refs"]),rejected_candidate_ids=tuple(str(x) for x in value["rejected_candidate_ids"]),conflict_ids=tuple(str(x) for x in value["conflict_ids"]),provenance_refs=tuple(str(x) for x in value["provenance_refs"]),policy_refs={str(k):str(v) for k,v in value["policy_refs"].items()},head_hash=str(value["head_hash"]))


def derive_assessment_input_hash(*,spec_hash:str,evidence_head_hash:str,ordered_fact_refs:Sequence[str],ordered_inference_refs:Sequence[str],assessment_policy_hash:str)->str:
    return sha256_json({"schema_version":1,"spec_hash":spec_hash,"evidence_head_hash":evidence_head_hash,"ordered_fact_refs":list(ordered_fact_refs),"ordered_inference_refs":list(ordered_inference_refs),"assessment_policy_hash":assessment_policy_hash})


_REQUIREMENT_RESULT_KEYS={"requirement_id","item_or_cell_id","support_status","binding_ids","reason_codes"}


@dataclass(frozen=True,slots=True)
class AnswerAssessmentV1:
    assessment_id:str; assessment_hash:str; assessment_input_hash:str; spec_hash:str; evidence_head_hash:str; policy_hash:str
    requirement_results:tuple[dict[str,JsonValue],...]; conflicts:tuple[dict[str,JsonValue],...]; missing_requirement_ids:tuple[str,...]
    minimum_useful:bool; status:str; reason_codes:tuple[str,...]
    def __post_init__(self)->None:
        for name in ("assessment_hash","assessment_input_hash","spec_hash","evidence_head_hash","policy_hash"): _digest(getattr(self,name),name)
        if self.status not in {"completed_candidate","partial_candidate","insufficient","needs_evidence"}: raise ValueError("assessment status invalid")
        if not isinstance(self.minimum_useful,bool): raise ValueError("minimum_useful must be boolean")
        identities=[]
        for result in self.requirement_results:
            if set(result)!=_REQUIREMENT_RESULT_KEYS: raise ValueError("assessment requirement result keys differ")
            identity=(_text(result["requirement_id"],"requirement_id"),_text(result["item_or_cell_id"],"item_or_cell_id")); identities.append(identity)
            if result["support_status"] not in {"supported","missing","conflicted"}: raise ValueError("support_status invalid")
            _strings(result["binding_ids"],"binding_ids"); _strings(result["reason_codes"],"reason_codes")
        if identities!=sorted(set(identities)): raise ValueError("assessment results must be unique and sorted")
        _strings(self.missing_requirement_ids,"missing_requirement_ids",sorted_unique=False); _strings(self.reason_codes,"reason_codes")
        expected=sha256_json(self._base_json())
        if self.assessment_hash!=expected or self.assessment_id!="asa_"+expected[:24]: raise ValueError("assessment_hash mismatch")
    def _base_json(self)->dict[str,JsonValue]: return {"schema_version":1,"assessment_input_hash":self.assessment_input_hash,"spec_hash":self.spec_hash,"evidence_head_hash":self.evidence_head_hash,"policy_hash":self.policy_hash,"requirement_results":copy.deepcopy(list(self.requirement_results)),"conflicts":copy.deepcopy(list(self.conflicts)),"missing_requirement_ids":list(self.missing_requirement_ids),"minimum_useful":self.minimum_useful,"status":self.status,"reason_codes":list(self.reason_codes)}
    def to_json(self)->dict[str,JsonValue]: return {**self._base_json(),"assessment_id":self.assessment_id,"assessment_hash":self.assessment_hash}
    @classmethod
    def create(cls,*,spec_hash:str,evidence_head_hash:str,requirement_results:Sequence[Mapping[str,JsonValue]],missing_requirement_ids:Sequence[str],minimum_useful:bool,status:str,reason_codes:Sequence[str],conflicts:Sequence[Mapping[str,JsonValue]]=(),policy_hash:str=Q1_ASSESSMENT_POLICY_HASH,assessment_input_hash:str|None=None,ordered_fact_refs:Sequence[str]=(),ordered_inference_refs:Sequence[str]=())->AnswerAssessmentV1:
        input_hash=assessment_input_hash or derive_assessment_input_hash(spec_hash=spec_hash,evidence_head_hash=evidence_head_hash,ordered_fact_refs=ordered_fact_refs,ordered_inference_refs=ordered_inference_refs,assessment_policy_hash=policy_hash)
        base={"schema_version":1,"assessment_input_hash":input_hash,"spec_hash":spec_hash,"evidence_head_hash":evidence_head_hash,"policy_hash":policy_hash,"requirement_results":[copy.deepcopy(dict(x)) for x in requirement_results],"conflicts":[copy.deepcopy(dict(x)) for x in conflicts],"missing_requirement_ids":list(missing_requirement_ids),"minimum_useful":minimum_useful,"status":status,"reason_codes":sorted(set(reason_codes))}
        digest=sha256_json(base); return cls.from_json({**base,"assessment_id":"asa_"+digest[:24],"assessment_hash":digest})
    @classmethod
    def from_json(cls,value:Mapping[str,Any])->AnswerAssessmentV1:
        keys={"schema_version","assessment_id","assessment_hash","assessment_input_hash","spec_hash","evidence_head_hash","policy_hash","requirement_results","conflicts","missing_requirement_ids","minimum_useful","status","reason_codes"}
        if not isinstance(value,Mapping) or set(value)!=keys or value.get("schema_version")!=1: raise ValueError("AnswerAssessmentV1 keys/version differ")
        for key in ("requirement_results","conflicts","missing_requirement_ids","reason_codes"):
            if not isinstance(value[key],list): raise ValueError(f"{key} must be an array")
        return cls(assessment_id=str(value["assessment_id"]),assessment_hash=str(value["assessment_hash"]),assessment_input_hash=str(value["assessment_input_hash"]),spec_hash=str(value["spec_hash"]),evidence_head_hash=str(value["evidence_head_hash"]),policy_hash=str(value["policy_hash"]),requirement_results=tuple(copy.deepcopy(dict(x)) for x in value["requirement_results"]),conflicts=tuple(copy.deepcopy(dict(x)) for x in value["conflicts"]),missing_requirement_ids=tuple(str(x) for x in value["missing_requirement_ids"]),minimum_useful=value["minimum_useful"],status=str(value["status"]),reason_codes=tuple(str(x) for x in value["reason_codes"]))


def assess_q1_evidence(*,spec_hash:str,evidence_head_hash:str,requests:Sequence[ScalarEvidenceRequest],evidence:Sequence[ExtractedScalarEvidence],ordered_fact_refs:Sequence[str]=())->AnswerAssessmentV1:
    by_requirement={item.requirement_id:item for item in evidence}
    if len(by_requirement)!=len(evidence): raise ValueError("multiple admitted bindings for requirement")
    if set(by_requirement)-{request.requirement_id for request in requests}: raise ValueError("assessment evidence references unknown requirements")
    results=[]; missing=[]
    for request in requests:
        item=by_requirement.get(request.requirement_id)
        if item is None:
            missing.append(request.requirement_id); results.append({"requirement_id":request.requirement_id,"item_or_cell_id":request.requirement_id,"support_status":"missing","binding_ids":[],"reason_codes":["no_admitted_binding"]})
        else: results.append({"requirement_id":request.requirement_id,"item_or_cell_id":request.requirement_id,"support_status":"supported","binding_ids":[item.binding_id],"reason_codes":["binding_admitted"]})
    supported=len(requests)-len(missing)
    if supported==len(requests): status,reasons="completed_candidate",["all_required_supported"]
    elif supported: status,reasons="partial_candidate",["minimum_useful_met","required_evidence_missing"]
    else: status,reasons="insufficient",["minimum_useful_not_met"]
    return AnswerAssessmentV1.create(spec_hash=spec_hash,evidence_head_hash=evidence_head_hash,ordered_fact_refs=ordered_fact_refs,requirement_results=sorted(results,key=lambda x:(str(x["requirement_id"]),str(x["item_or_cell_id"]))),missing_requirement_ids=missing,minimum_useful=supported>0,status=status,reason_codes=reasons)


__all__=["GENESIS_EVIDENCE_HEAD", "Q1_ADMISSION_POLICY", "Q1_ADMISSION_POLICY_HASH", "Q1_ASSESSMENT_POLICY", "Q1_ASSESSMENT_POLICY_HASH", "Q1_ROUTE_POLICY", "AdmittedResearchFactV1", "AnswerAssessmentV1", "EvidenceFactBatchV1", "RegisteredInferenceV1", "assess_q1_evidence", "derive_assessment_input_hash"]
