#!/usr/bin/env python3
"""Extract per-case review material (setup, input, gold, recall, answer) to Markdown.

Reads a run_corpus_batch evidence root. Output is review input for the
post-terminal semantic review; it never decides PASS/FAIL by itself.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(path: Path):
    return json.loads(path.read_text()) if path.is_file() else None


def render_case(case_dir: Path) -> str:
    case_id = case_dir.name
    scoring = case_dir / "scoring" / case_id
    packet = _load(scoring / "review-packet.json") or {}
    execution = _load(scoring / "execution.json") or {}
    setup = _load(scoring / "setup.json") or {}
    inp = _load(scoring / "input.json") or {}
    oracle = _load(scoring / "oracle.json") or {}
    resource = _load(case_dir / "resource" / "resource.json") or {}
    lines = [f"## {case_id}", ""]
    lines.append(f"- 分类/条件：{packet.get('case', {}).get('category')} / {packet.get('case', {}).get('condition')}")
    lines.append(f"- setup 原文：{setup.get('setup_source_text')}")
    lines.append(f"- 用户输入：{inp.get('current_user_message')}")
    if inp.get("recent_messages"):
        lines.append(f"- recent_messages：{json.dumps(inp.get('recent_messages'), ensure_ascii=False)[:600]}")
    lines.append(f"- 场景时钟：{json.dumps(inp.get('scenario_clock'), ensure_ascii=False)}")
    lines.append(f"- gold（仅事后评分）：{oracle.get('oracle_source_text')}")
    lines.append(f"- 标签：{json.dumps(oracle.get('labels'), ensure_ascii=False)}")
    lines.append(f"- 执行状态：{execution.get('execution_status')} / verdict {packet.get('oracle_verdict')} / "
                 f"error {execution.get('error_type')} / stop {resource.get('stop_reason')} / "
                 f"{resource.get('elapsed_seconds')}s / 峰 {resource.get('peak_group_rss_kib')} KiB")
    lines.append(f"- 类型预测：{packet.get('predicted_types')}；指标 {json.dumps(packet.get('original_metric_components'), ensure_ascii=False)}")
    access = packet.get("required_procedure_access")
    if access:
        # Parallel label: procedure is reached through the discovery tool, not typed recall.
        lines.append(f"- 程序访问（{access.get('required_access')}）：{access.get('status')}；"
                     f"调用 {len(access.get('observed_calls') or [])} 次，"
                     f"结果 {json.dumps(access.get('observed_results'), ensure_ascii=False)}；"
                     f"命中种子 {access.get('matched_memory_ids')} / 种子 {access.get('seeded_procedure_memory_ids')}")
    if execution.get("error_traceback"):
        lines.append("- 回溯尾部：`" + execution["error_traceback"].strip().splitlines()[-1][:200] + "`")
    if execution.get("followup_events"):
        lines.append(f"- 多阶段：task_phase_status={execution.get('task_phase_status')} unmet={execution.get('unmet_followup')} "
                     f"followups={json.dumps([{k: ev.get(k) for k in ('followup_id', 'status', 'after_event', 'on_unmet')} for ev in execution['followup_events']], ensure_ascii=False)}")
    phases = sorted(d for d in scoring.glob("scoring-*") if d.is_dir())
    for phase in phases:
        lines.append(f"- 阶段 {phase.name}：")
        lines.extend(_render_transcript(_load(phase / "observation-transcript.json"), indent="  "))
    transcript = None if phases else _load(scoring / "observation-transcript.json")
    if transcript:
        lines.append("- 对话：")
        lines.extend(_render_transcript(transcript))
    elif not phases:
        lines.append("- 对话：无 transcript")
    lines.append("")
    return "\n".join(lines)


def _render_transcript(transcript, indent: str = "") -> list[str]:
    if not transcript:
        return [f"{indent}  - 无 transcript"]
    lines: list[str] = []
    msgs = transcript if isinstance(transcript, list) else transcript.get("messages") or []
    if True:
        for m in msgs:
            role = m.get("role")
            if role == "tool":
                try:
                    v = json.loads(m["content"])["value"]
                    frags = [f.get("payload") for f in v.get("fragments", [])]
                    lines.append(f"  - [tool {m.get('name')}] route={v.get('context_route_receipt', {}).get('route')} "
                                 f"recall_refs={v.get('context_route_receipt', {}).get('recall_refs')} "
                                 f"degr={v.get('degradation_codes')} fragments={json.dumps(frags, ensure_ascii=False)[:500]}")
                except Exception:
                    lines.append(f"  - [tool {m.get('name')}] {str(m.get('content'))[:300]}")
            elif role == "assistant":
                calls = [((tc.get("function") or tc).get("name"), (tc.get("function") or tc).get("arguments", "")[:300])
                         for tc in m.get("tool_calls") or []]
                text = (m.get("content") or "").replace("\n", " ⏎ ")
                lines.append(f"  - [assistant] {text[:800]}" + (f" CALLS={calls}" if calls else ""))
            elif role == "user":
                lines.append(f"  - [user] {str(m.get('content'))[:300]}")
    return [indent + line for line in lines]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.evidence_root
    cases = sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("C"))
    body = [f"# 语料批次复核材料：{root.name}", "", f"共 {len(cases)} 例。每例含 setup/输入/gold/召回/最终回答，供语义复核。", ""]
    body += [render_case(c) for c in cases]
    args.output.write_text("\n".join(body))
    print(f"wrote {args.output} ({len(cases)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
