#!/usr/bin/env python3
"""Decode UTF-16LE tauri log and show assembler_task_classified task_type + intent events.

Usage: python p2grep.py [tail]
Env DESKPET_E2E_LOG points to the log (default tauri-phase2.log).
"""
import os
import re
import sys

LOG = os.environ.get(
    "DESKPET_E2E_LOG",
    r"G:\projects\deskpet\plans\manual-results-2026-06-26-bugb-phase1\tauri-phase2.log",
)

EVENTS = [
    "assembler_task_classified",
    "assembler_classifier_llm_injected",
    "assembler_classifier_llm_inject_failed",
    "classifier.llm_timeout",
    "classifier.llm_failed",
    "classifier.llm_unknown_task_type",
    "intent_triage.allowlist_hit",
    "intent_triage.done",
]


def main():
    raw = open(LOG, "rb").read()
    txt = raw.decode("utf-16-le", errors="ignore").replace("\x00", "").replace("\r", "")
    START = re.compile(r"^(INFO|WARNING|ERROR|DEBUG|CRITICAL)")
    logical = []
    cur = ""
    for ln in txt.split("\n"):
        if START.match(ln):
            if cur:
                logical.append(cur)
            cur = ln
        else:
            cur += ln
    if cur:
        logical.append(cur)

    counts = {e: 0 for e in EVENTS}
    matched = []
    for ll in logical:
        for e in EVENTS:
            if e in ll:
                counts[e] += 1
                matched.append((e, ll))
    for e in EVENTS:
        print(f"  {e} = {counts[e]}")
    tail = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if tail:
        print("--- last matched ---")
        for e, ll in matched[-tail:]:
            tt = re.search(r"task_type=(\S+)", ll)
            ts = re.search(r"timestamp='([^']+)'", ll)
            extra = (tt.group(0) if tt else "")
            safe = (extra or ll[:120]).encode("ascii", "replace").decode("ascii")
            tss = ts.group(1)[11:23] if ts else "?"
            print(f"  {tss} [{e}] {safe}")


if __name__ == "__main__":
    main()
