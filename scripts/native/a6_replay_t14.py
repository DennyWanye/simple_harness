# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HM-TO-A6 turn-14 真实模型复算驱动（事件 AE，`DECISION-AE-VAGUE-WISH-PROSPECTIVE.md` §5）。

和 `a6_replay_t15.py` 同一套做法：把某一次原生跑里持久化的 turn-14 分析请求**逐字重放**给
真实模型，臂之间只换 system 提示词与工具 schema（`host-analysis-prompt/v9` 对照 / `v10`），
证据体、`now_iso`、候选、`max_output_tokens` 一字不改；每个响应再离线过一遍那个臂自己的
Host 编译器，得到「模型提了什么形状」+「Host 收不收」两层结论。

T14 是负控 NC-3（「以后有机会我想学画画。」）。这里被测量的属性和 T15 不同：

* `prospective`：模型有没有提 prospective（**提了就是错的**）；
* `now_echo`：它的 `trigger_at_iso` 是不是逐字等于请求体里的 `now_iso`
  （attempt 11 实测就是这一步：`2026-09-09T10:26:00+08:00` → `trigger_at=1788920760.0`）；
* `semantic` / `episode`：有没有落到正确形状（semantic interest/goal）；
* `pending_prospective`：Host 编译之后**真的落库**了一条 pending Prospective 吗——
  这才是 NC-3 的判据，v10 的期望值是 0；
* `rejected`：Host 给出的拒收码（期望 `analysis_prospective_vague_wish`）。

凭据处理与 `a6_replay_t15.py` 完全一致：只在内存里用于 Authorization 头，不打印、不写进
输出 JSON、不入库。

用法
----
    backend/.venv/bin/python scripts/native/a6_replay_t14.py \\
        --attempt-input <E>/t14_attempt_input.json \\
        --credentials .local-test-evidence/2026-09-07/credentials/deepseek.env \\
        --plan '[["v9","deepseek-v4-flash",6],["v10","deepseek-v4-flash",6]]' \\
        --out /tmp/replay_t14.json
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


class Turn14Replay:
    """一次 T14 复算所需的全部只读输入。"""

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

    def case(self):
        """离线证据项，`occurred_at` 钉在原始 `now_iso` 上，相对表达才锚得对。"""
        from types import SimpleNamespace

        from simple_harness.runtime import AnalysisBudget, EvidenceRef, MemoryAnalysisRequest

        from deskpet.memory import analysis_proposal as legacy
        from deskpet.memory.human_memory_service import build_foreground_turn_evidence
        from tests.sdk_adapters import s5b_memory_harness as mh

        envelope, receipt = build_foreground_turn_evidence(
            subject="actor-1", authority_ref=mh.AUTHORITY_REF,
            delivery_key="t14-replay", text=self.turn_text)
        item = legacy.admitted_item(envelope, receipt, occurred_at=self.now_epoch)
        request = MemoryAnalysisRequest(
            job_id="t14-replay", run_id=envelope.run_id, subject=envelope.subject,
            ordered_evidence_refs=(EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
            prompt_version="replay", result_schema_version="replay", policy_version="replay",
            provider_id="provider-1", model_id="model-1", model_config_hash="a" * 64, attempt=1,
            budget=AnalysisBudget(2048, 1024, 5000, 1000),
            disclosure_context=envelope.disclosure_context, idempotency_key="t14-replay")
        return SimpleNamespace(items=[item], request=request)


def classify(raw, protocol, replay: Turn14Replay):
    """一个样本 → 被测属性 + 该臂 Host 验证器的判决。"""
    from dataclasses import replace as dataclass_replace

    from deskpet.memory import analysis_proposal as legacy
    from deskpet.sdk_adapters.provider import _repaired_tool_arguments

    out = {"finish": None, "completion_tokens": None, "repaired": False, "prospective": False,
           "now_echo": False, "trigger_at_iso": None, "semantic": False, "episode": False,
           "semantic_predicates": [], "accepted": False, "rejected": [],
           "pending_prospective": 0, "shape": ""}
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
    operations = [op for op in (proposal.get("operations") or []) if isinstance(op, dict)]
    for op in operations:
        kind = op.get("memory_type")
        if kind == "prospective":
            out["prospective"] = True
            iso = str((op.get("prospective") or {}).get("trigger_at_iso") or "")
            out["trigger_at_iso"] = iso
            try:
                out["now_echo"] = datetime.fromisoformat(iso).timestamp() == replay.now_epoch
            except (TypeError, ValueError):
                out["now_echo"] = False
        elif kind == "semantic":
            out["semantic"] = True
            out["semantic_predicates"].append(str((op.get("semantic") or {}).get("predicate") or ""))
        elif kind == "episode":
            out["episode"] = True
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
            plan_id="host-t14-replay", now=replay.now_epoch + 3.0,
            candidates=(), relation_candidates=())
        out["rejected"] = [(r.operation_id, r.code, dict(r.detail)) for r in compiled.rejected]
        out["accepted"] = compiled.outcome == "mutate" and not compiled.rejected
        out["pending_prospective"] = 0 if compiled.plan is None else sum(
            op.memory_type.value == "prospective" for op in compiled.plan.operations)
        out["applied"] = [] if compiled.plan is None else [
            op.memory_type.value for op in compiled.plan.operations]
    except legacy.AnalysisProposalRejected as exc:
        out["rejected"] = [("proposal", exc.code, dict(exc.detail))]
    except Exception as exc:  # noqa: BLE001 - 崩溃本身也是一种被测结果
        out["rejected"] = [("proposal", type(exc).__name__, {"message": str(exc)[:200]})]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HM-TO-A6 turn-14 真实模型复算（事件 AE §5）")
    ap.add_argument("--attempt-input", required=True)
    ap.add_argument("--credentials",
                    help="含 BASEURL/APIKEY 的 env 文件；凭据不打印、不写入输出。")
    ap.add_argument("--plan", required=True,
                    help='JSON：[["v9","deepseek-v4-flash",6],["v10","deepseek-v4-flash",6]]')
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend", default=str(_REPO / "backend"))
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args(argv)

    sys.path.insert(0, args.backend)
    env = load_credentials(args.credentials)
    replay = Turn14Replay(args.attempt_input)

    from deskpet.memory import analysis_proposal_v9 as v9
    from deskpet.memory import analysis_proposal_v10 as v10

    arms = {"v9": v9, "v10": v10}
    results = []
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
            print(json.dumps(row, ensure_ascii=False), flush=True)
            Path(args.out).write_text(
                json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
