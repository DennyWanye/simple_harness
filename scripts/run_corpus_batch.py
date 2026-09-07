#!/usr/bin/env python3
"""Serially execute corpus cases one process group at a time and summarize.

Each case gets its own resource receipt and scoring directory under the ignored
evidence root. Failures are recorded, never retried. Raw evidence stays local.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-root", type=Path, required=True)
    parser.add_argument("--memory-sdk-root", type=Path, required=True)
    parser.add_argument("--installed-target", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--case-file", type=Path)
    parser.add_argument("--rss-mib", type=int, default=6144)
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--python", type=Path)
    args = parser.parse_args()
    host = args.host_root.resolve()
    python = (args.python or host / "backend/.venv/bin/python").resolve()
    cases = list(args.case)
    if args.case_file:
        cases += [line.strip() for line in args.case_file.read_text().splitlines()
                  if line.strip() and not line.startswith("#")]
    if not cases or len(set(cases)) != len(cases):
        raise SystemExit("cases empty or duplicated")
    evidence = args.evidence_root.resolve()
    if ".local-test-evidence" not in evidence.parts:
        raise SystemExit("evidence root must be ignored")
    evidence.mkdir(parents=True, exist_ok=True)
    corpus = (args.memory_sdk_root / "plans/2026-08-29-human-memory-digital-twin/quality/"
              "recall-corpus-candidate/review-zh/successor-12x20").resolve()
    compiler = (args.memory_sdk_root / "scripts").resolve()
    summary_path = evidence / "batch-summary.jsonl"
    for case_id in cases:
        case_dir = evidence / case_id
        if case_dir.exists():
            print(f"{case_id}: SKIP existing {case_dir}")
            continue
        case_dir.mkdir()
        started = time.time()
        command = [str(python), str(host / "scripts/run_resource_bounded.py"),
                   "--evidence-dir", str(case_dir / "resource"),
                   "--rss-mib", str(args.rss_mib), "--seconds", str(args.seconds), "--",
                   str(python), "-m", "deskpet.quality.corpus_scoring",
                   "--corpus-root", str(corpus), "--compiler-root", str(compiler),
                   "--host-root", str(host), "--installed-target", str(args.installed_target.resolve()),
                   "--output", str(case_dir / "scoring"), "--case", case_id, "--execute"]
        env = {"PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONPATH": f"{args.installed_target.resolve()}:{host / 'backend'}",
               "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home())}
        with (case_dir / "driver.log").open("wb") as log:
            completed = subprocess.run(command, cwd=host, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, check=False)
        record = {"case_id": case_id, "driver_returncode": completed.returncode,
                  "elapsed_seconds": round(time.time() - started, 1)}
        packet = case_dir / "scoring" / case_id / "review-packet.json"
        resource = case_dir / "resource" / "resource.json"
        if resource.is_file():
            r = json.loads(resource.read_text())
            record.update(stop_reason=r.get("stop_reason"), peak_rss_kib=r.get("peak_group_rss_kib"),
                          worker_group_returncode=r.get("parent_returncode"))
        if packet.is_file():
            p = json.loads(packet.read_text())
            record.update(oracle_verdict=p.get("oracle_verdict"),
                          predicted_types=p.get("predicted_types"),
                          handoffs=p.get("observed_handed_off_invocations"),
                          metrics=p.get("original_metric_components"))
        else:
            record["oracle_verdict"] = "NO_PACKET"
        with summary_path.open("a") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
