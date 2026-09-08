# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HM-TO-A6 turn-15 真实模型复算驱动（事件 T，`DECISION-T-RELATION-FORM.md` §3）。

把某一次原生跑里持久化下来的 turn-15 分析请求**逐字重放**给真实模型，臂之间只换
system 提示词与工具 schema（`host-analysis-prompt/v8` 对照 / `v9`），
证据体、候选、`max_output_tokens` 一字不改；每个响应再离线过一遍 Host 编译器
（`compile_proposal`，带同样的候选），得到「模型提了什么形状」+「Host 收不收」两层结论。

§3 的那张表就是本脚本跑出来的。改动 v9 三段策略文本的人必须重跑它（F-T5）。

输入
----
* `--attempt-input`：一次 `analysis-attempt-input-*` 证据的 JSON（含 `provider_request`）。
  它在证据目录的 `state.db.human_memory_evidence` 里，导出方式见 §0 的表。
* `--credentials`：`KEY=VALUE` 形式的 env 文件，至少含 `BASEURL` 与 `APIKEY`
  （也可改用环境变量 `A6_REPLAY_CREDENTIALS` 指同一个路径，或直接设 `BASEURL`/`APIKEY`）。
  **凭据只在内存里用于 Authorization 头，不打印、不写进输出 JSON、不入库。**
  本仓的凭据一律放在 git-ignored 的 `.local-test-evidence/<日期>/credentials/` 下，
  路径由调用方给出，脚本里不硬编码。
* `--plan`：`[[臂, 模型, 采样数], ...]` 的 JSON，例如
  `[["v8","deepseek-v4-flash",8],["v9","deepseek-v4-flash",9]]`。

用法
----
    backend/.venv/bin/python scripts/native/a6_replay_t15.py \
        --attempt-input <E>/t15_attempt_input.json \
        --credentials .local-test-evidence/2026-09-09/credentials/deepseek.env \
        --plan '[["v9","deepseek-v4-flash",9]]' \
        --out /tmp/replay_v9.json

输出是一行一个样本的 JSONL（stdout）加一份汇总 JSON（`--out`），每行含
`finish` / `completion_tokens` / 提案形状 / 是否提关系 / Host 是否接受 / 拒绝理由码。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def load_credentials(path: str | None) -> dict[str, str]:
    """从 env 文件或进程环境取 BASEURL/APIKEY。返回值绝不打印。"""
    env: dict[str, str] = {}
    source = path or os.environ.get("A6_REPLAY_CREDENTIALS")
    if source:
        for line in Path(source).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("BASEURL", "APIKEY"):
        env.setdefault(key, os.environ.get(key, ""))
        if not env[key]:
            raise SystemExit(
                f"缺少 {key}：用 --credentials 指向 env 文件，或设置同名环境变量。"
                "（凭据不入库、不打印。）"
            )
    return env


class Replay:
    """一次复算所需的全部只读输入。"""

    def __init__(self, attempt_input: str) -> None:
        attempt = json.loads(Path(attempt_input).read_text(encoding="utf-8"))
        self.provider_request = attempt["provider_request"]
        self.user_content = self.provider_request["messages"][1]["content"]
        body = json.loads(self.user_content.split("\n", 1)[1])
        self.body = body
        self.turn_text = body["evidence_items"][0]["text"]
        self.live_item_id = body["evidence_items"][0]["evidence_item_id"]

    def candidates(self) -> list[dict]:
        """已下发的语义候选，按 Host 内部行（编译期视图）重建。"""
        rows = []
        for index, entry in enumerate(self.body["semantic_candidates"], start=1):
            rows.append({
                "candidate_key": entry["candidate_key"],
                "memory_id": f"cognitive-memory-t15-{index}",
                "revision": 1,
                "memory_type": "semantic",
                "privacy_class": "personal",
                "information_attributes": ["preference"],
                "payload": dict(entry["semantic"]),
            })
        return rows

    def case(self):
        from tests.memory.test_procedure_adoption import compilation

        return compilation(self.turn_text)


def call(protocol, model, replay: Replay, env: dict[str, str], *, timeout=300):
    spec = protocol.proposal_tool_spec()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": protocol.ANALYSIS_SYSTEM_INSTRUCTION},
            {"role": "user", "content": replay.user_content},
        ],
        "tools": [{"type": "function", "function": {
            "name": spec.name, "description": spec.description,
            "parameters": json.loads(json.dumps(protocol.PROPOSAL_TOOL_SCHEMA)),
        }}],
        "max_tokens": int(replay.provider_request["max_output_tokens"]),
    }
    request = urllib.request.Request(
        env["BASEURL"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + env["APIKEY"]},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode())


def classify(raw, protocol, replay: Replay):
    """一个样本 → 四个被测属性 + Host 验证器的判决。"""
    from dataclasses import replace as dataclass_replace

    from deskpet.memory import analysis_proposal as legacy
    from deskpet.memory.semantic_correction import anaphoric_reference_marker
    from deskpet.sdk_adapters.provider import _repaired_tool_arguments

    out = {"finish": None, "completion_tokens": None, "proposal": None, "repaired": False,
           "relation": False, "literal": False, "resolved": False, "procedure": False,
           "extra_semantic": False, "accepted": False, "rejected": [], "shape": ""}
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
    operations = proposal.get("operations") or []
    for op in operations:
        if not isinstance(op, dict):
            continue
        kind = op.get("memory_type")
        if kind == "semantic_relation":
            out["relation"] = True
        elif kind == "procedure":
            out["procedure"] = True
        elif kind == "semantic":
            value = str((op.get("semantic") or {}).get("object_value") or "")
            if anaphoric_reference_marker(value) is not None:
                out["literal"] = True
            if value == "Python 3.12":
                out["resolved"] = True
            out["extra_semantic"] = True
    out["shape"] = "+".join(sorted(
        {str(op.get("memory_type")) for op in operations if isinstance(op, dict)})) or "empty"

    case = replay.case()
    # 持久化请求里的 evidence_item_id 是那次原生跑分配的身份，离线 case 自己另发一个；
    # 重新指向本地 id，好让验证器判的是模型的**内容**，而不是它不可能知道的 id。
    for op in operations:
        if isinstance(op, dict) and op.get("evidence_item_id") == replay.live_item_id:
            op["evidence_item_id"] = case.items[0].item_id
    request = dataclass_replace(case.request, prompt_version=protocol.PROMPT_VERSION,
                                result_schema_version=protocol.RESULT_SCHEMA_VERSION,
                                policy_version=protocol.POLICY_VERSION)
    try:
        compiled = protocol.compile_proposal(proposal, request=request, items=case.items,
                                             base_revision=1, plan_id="host-t15-replay", now=1.0,
                                             candidates=replay.candidates(), relation_candidates=())
        out["rejected"] = [(r.operation_id, r.code, dict(r.detail)) for r in compiled.rejected]
        out["accepted"] = compiled.outcome == "mutate" and not compiled.rejected
        out["relation_accepted"] = bool(
            compiled.plan is not None
            and any(getattr(op.payload, "semantic_kind", None) is not None
                    and op.payload.semantic_kind.value == "relation"
                    for op in compiled.plan.operations))
    except legacy.AnalysisProposalRejected as exc:
        out["rejected"] = [("proposal", exc.code, dict(exc.detail))]
        out["relation_accepted"] = False
    except Exception as exc:  # noqa: BLE001 - 崩溃本身也是一种被测结果
        out["rejected"] = [("proposal", type(exc).__name__, {"message": str(exc)[:200]})]
        out["relation_accepted"] = False
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HM-TO-A6 turn-15 真实模型复算（事件 T §3）")
    ap.add_argument("--attempt-input", required=True,
                    help="持久化的 analysis-attempt-input JSON（含 provider_request）")
    ap.add_argument("--credentials",
                    help="含 BASEURL/APIKEY 的 env 文件；亦可用环境变量 A6_REPLAY_CREDENTIALS "
                         "或直接设 BASEURL/APIKEY。凭据不打印、不写入输出。")
    ap.add_argument("--plan", required=True,
                    help='JSON：[["v8","deepseek-v4-flash",8],["v9","deepseek-v4-flash",9]]')
    ap.add_argument("--out", required=True, help="汇总 JSON 输出路径")
    ap.add_argument("--backend", default=str(_REPO / "backend"),
                    help="含 deskpet/tests 的 backend 目录（默认取本仓）")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args(argv)

    sys.path.insert(0, args.backend)
    env = load_credentials(args.credentials)
    replay = Replay(args.attempt_input)

    from deskpet.memory import analysis_proposal_v8 as v8
    from deskpet.memory import analysis_proposal_v9 as v9

    arms = {"v8": v8, "v9": v9}
    results = []
    for arm, model, samples in json.loads(args.plan):
        protocol = arms[arm]
        for index in range(int(samples)):
            started = time.time()
            try:
                row = classify(call(protocol, model, replay, env, timeout=args.timeout),
                               protocol, replay)
            except Exception as exc:  # noqa: BLE001
                row = {"shape": "transport_error", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
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
