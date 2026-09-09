# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HM-TO-A6 turn-20 / turn-21 真实模型复算驱动（事件 AJ，`DECISION-AJ-V10-CORRECTION-REGRESSION.md` §5）。

和 `a6_replay_t14.py` / `a6_replay_t15.py` 同一套做法：把某一次原生跑里持久化的分析请求
**逐字重放**给真实模型，臂之间只换 system 提示词与工具 schema（`host-analysis-prompt/v9`
对照 / `v10` / `v11`），证据体、`now_iso`、候选、`max_output_tokens` 一字不改；每个响应再
离线过一遍那个臂自己的 Host 编译器，得到「模型提了什么形状」+「Host 收不收」两层结论。

T20 是 A6-7（显式更正 → `revise_semantic` + revision 2），T21 是 A6-8（含糊冲突 →
`contest_semantic` + conflict group）。被测属性：

* `action`：模型这条 semantic 用的是 `create` / `revise_semantic` / `contest_semantic`；
* `candidate_key_used`：有没有引用 Host 下发的候选键（不引用就注定落不到那条记忆上）；
* `qualifiers`：模型给的槽位限定词，以及它和候选那条是否逐字相同；
* `accepted` / `rejected`：Host 编译器的判决与逐条拒收码；
* `applied_kinds`：真的落进 plan 的 mutation kind（`revise` / `contest` 才算数）。

候选按 t15 的做法从请求体重建（`candidate_key` 保持请求里那一个，`memory_id` 本地另发），
`correction_intent` 由 **Host 自己的语法**（`explicit_correction_intent`）针对离线证据项现算，
和 `SemanticCorrectionAuthority.prepare` 的第二趟完全一致——模型永远不参与这一步。

凭据处理与 `a6_replay_t15.py` 完全一致：只在内存里用于 Authorization 头，不打印、不写进
输出 JSON、不入库。

用法
----
    backend/.venv/bin/python scripts/native/a6_replay_t20.py \\
        --attempt-input <E>/t20_attempt_input.json \\
        --credentials .local-test-evidence/2026-09-07/credentials/deepseek.env \\
        --plan '[["v9","deepseek-v4-flash",6],["v10","deepseek-v4-flash",6],["v11","deepseek-v4-flash",6]]' \\
        --out /tmp/replay_t20.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from a6_replay_t15 import call, load_credentials  # noqa: E402


class CorrectionReplay:
    """一次 T20/T21 复算所需的全部只读输入。"""

    def __init__(self, attempt_input: str) -> None:
        attempt = json.loads(Path(attempt_input).read_text(encoding="utf-8"))
        self.provider_request = attempt["provider_request"]
        self.user_content = self.provider_request["messages"][1]["content"]
        body = json.loads(self.user_content.split("\n", 1)[1])
        self.body = body
        self.turn_text = body["evidence_items"][0]["text"]
        self.live_item_id = body["evidence_items"][0]["evidence_item_id"]
        self.now_iso = body["now_iso"]
        self.now_epoch = datetime.fromisoformat(self.now_iso).timestamp()
        self.issued = body.get("semantic_candidates") or []

    def case(self):
        """离线证据项 + 请求壳；`occurred_at` 钉在原始 `now_iso` 上。"""
        from types import SimpleNamespace

        from simple_harness.runtime import AnalysisBudget, EvidenceRef, MemoryAnalysisRequest

        from deskpet.memory import analysis_proposal as legacy
        from deskpet.memory.human_memory_service import build_foreground_turn_evidence
        from tests.sdk_adapters import s5b_memory_harness as mh

        envelope, receipt = build_foreground_turn_evidence(
            subject="actor-1", authority_ref=mh.AUTHORITY_REF,
            delivery_key="t20-replay", text=self.turn_text)
        item = legacy.admitted_item(envelope, receipt, occurred_at=self.now_epoch)
        request = MemoryAnalysisRequest(
            job_id="t20-replay", run_id=envelope.run_id, subject=envelope.subject,
            ordered_evidence_refs=(EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
            prompt_version="replay", result_schema_version="replay", policy_version="replay",
            provider_id="provider-1", model_id="model-1", model_config_hash="a" * 64, attempt=1,
            budget=AnalysisBudget(2048, 1024, 5000, 1000),
            disclosure_context=envelope.disclosure_context, idempotency_key="t20-replay")
        return SimpleNamespace(items=[item], request=request)

    def candidates(self, items):
        """编译期候选行：请求里的 candidate_key + Host 自己现算的 correction_intent。"""
        from deskpet.memory.semantic_correction import explicit_correction_intent

        issued = []
        for index, entry in enumerate(self.issued, start=1):
            issued.append({
                "candidate_key": entry["candidate_key"],
                "memory_id": f"cognitive-memory-t20-replay-{index}",
                "revision": 1,
                "memory_type": "semantic",
                "privacy_class": "personal",
                "information_attributes": ["preference"],
                "payload": dict(entry["semantic"]),
            })
        for row in issued:
            row["correction_intent"] = explicit_correction_intent(row, items, issued)
        return issued


def classify(raw, protocol, replay: CorrectionReplay):
    """一个样本 → 被测属性 + 该臂 Host 验证器的判决。"""
    from deskpet.sdk_adapters.provider import _repaired_tool_arguments

    out = {"finish": None, "completion_tokens": None, "repaired": False, "shape": "",
           "actions": [], "candidate_key_used": False, "qualifiers": None,
           "qualifiers_match": None, "accepted": False, "rejected": [], "applied_kinds": [],
           "proposal": None}
    choice = (raw.get("choices") or [{}])[0]
    out["finish"] = choice.get("finish_reason")
    out["completion_tokens"] = (raw.get("usage") or {}).get("completion_tokens")
    calls = (choice.get("message") or {}).get("tool_calls") or []
    if len(calls) != 1 or calls[0].get("function", {}).get("name") != "memory_analysis_proposal":
        out["shape"] = "no_proposal"
        return out
    arguments = calls[0]["function"].get("arguments") or ""
    try:
        proposal = json.loads(arguments)
    except Exception:  # noqa: BLE001 - 形状本身就是被测量的结果
        repaired = _repaired_tool_arguments(arguments)
        if repaired is None:
            out["shape"] = "unparseable"
            return out
        text = repaired[0] if isinstance(repaired, tuple) else repaired
        proposal, out["repaired"] = json.loads(text), True
    if not isinstance(proposal, dict):
        out["shape"] = "unparseable"
        return out
    out["proposal"] = proposal
    return _compile(out, proposal, protocol, replay)


def _compile(out, proposal, protocol, replay: CorrectionReplay):
    """离线跑一遍该臂的 Host 编译器；模型样本与编译器可以分开重放（`--recompile`）。"""
    from dataclasses import replace as dataclass_replace

    from deskpet.memory import analysis_proposal as legacy

    operations = [op for op in (proposal.get("operations") or []) if isinstance(op, dict)]
    issued_keys = {entry["candidate_key"] for entry in replay.issued}
    by_key = {entry["candidate_key"]: entry["semantic"] for entry in replay.issued}
    for op in operations:
        if op.get("memory_type") != "semantic":
            continue
        action = str(op.get("action") or "create")
        out["actions"].append(action)
        key = str(op.get("candidate_key") or "")
        if key in issued_keys:
            out["candidate_key_used"] = True
            quals = (op.get("semantic") or {}).get("qualifiers")
            out["qualifiers"] = quals
            out["qualifiers_match"] = (
                [str(q) for q in quals or ()] == list(by_key[key].get("qualifiers") or ()))
    out["shape"] = "+".join(sorted({str(op.get("memory_type")) for op in operations})) or "empty"

    case = replay.case()
    for op in operations:
        if op.get("evidence_item_id") == replay.live_item_id:
            op["evidence_item_id"] = case.items[0].item_id
    request = dataclass_replace(case.request, prompt_version=protocol.PROMPT_VERSION,
                                result_schema_version=protocol.RESULT_SCHEMA_VERSION,
                                policy_version=protocol.POLICY_VERSION)
    try:
        compiled = protocol.compile_proposal(
            proposal, request=request, items=case.items, base_revision=1,
            plan_id="host-t20-replay", now=replay.now_epoch + 3.0,
            candidates=replay.candidates(case.items), relation_candidates=())
        out["rejected"] = [(r.operation_id, r.code, dict(r.detail)) for r in compiled.rejected]
        out["accepted"] = compiled.outcome == "mutate" and not compiled.rejected
        out["applied_kinds"] = [] if compiled.plan is None else [
            op.kind.value for op in compiled.plan.operations]
    except legacy.AnalysisProposalRejected as exc:
        out["rejected"] = [("proposal", exc.code, dict(exc.detail))]
    except Exception as exc:  # noqa: BLE001 - 崩溃本身也是一种被测结果
        out["rejected"] = [("proposal", type(exc).__name__, {"message": str(exc)[:200]})]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HM-TO-A6 turn-20/21 真实模型复算（事件 AJ §5）")
    ap.add_argument("--attempt-input", required=True)
    ap.add_argument("--credentials",
                    help="含 BASEURL/APIKEY 的 env 文件；凭据不打印、不写入输出。")
    ap.add_argument("--plan", help='JSON：[["v9","deepseek-v4-flash",6],["v11","deepseek-v4-flash",6]]')
    ap.add_argument("--recompile",
                    help="不叫模型：读入一份既有 --out，把里面每个样本的原始提案在**当前** "
                         "backend 的 Host 编译器上重跑一遍（修复前/修复后的同样本对照）。")
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend", default=str(_REPO / "backend"))
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args(argv)
    if not args.plan and not args.recompile:
        ap.error("--plan 与 --recompile 至少给一个")

    sys.path.insert(0, args.backend)
    env = load_credentials(args.credentials) if args.plan else {}
    replay = CorrectionReplay(args.attempt_input)

    from deskpet.memory import analysis_proposal_v9 as v9
    from deskpet.memory import analysis_proposal_v10 as v10

    arms = {"v9": v9, "v10": v10}
    try:
        from deskpet.memory import analysis_proposal_v11 as v11
    except ImportError:
        pass
    else:
        arms["v11"] = v11

    results = []
    if args.recompile:
        for row in json.loads(Path(args.recompile).read_text(encoding="utf-8")):
            proposal = row.get("proposal")
            fresh = {**row, "accepted": False, "rejected": [], "applied_kinds": [],
                     "actions": [], "candidate_key_used": False, "qualifiers": None,
                     "qualifiers_match": None}
            if isinstance(proposal, dict):
                fresh = _compile(fresh, json.loads(json.dumps(proposal)), arms[row["arm"]], replay)
            results.append(fresh)
            print(json.dumps({k: v for k, v in fresh.items() if k != "proposal"},
                             ensure_ascii=False), flush=True)
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        return 0

    for arm, model, samples in json.loads(args.plan):
        protocol = arms[arm]
        for index in range(int(samples)):
            started = time.time()
            try:
                row = classify(call(protocol, model, replay, env, timeout=args.timeout),
                               protocol, replay)
            except Exception as exc:  # noqa: BLE001
                row = {"shape": "transport_error",
                       "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            row.update(arm=arm, model=model, sample=index + 1,
                       seconds=round(time.time() - started, 1))
            results.append(row)
            print(json.dumps({k: v for k, v in row.items() if k != "proposal"},
                             ensure_ascii=False), flush=True)
            Path(args.out).write_text(
                json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
