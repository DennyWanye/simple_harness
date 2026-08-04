#!/usr/bin/env python3
"""Decode the UTF-16LE tauri-dev.log and count Phase-1 key events.

Usage: python p1grep.py [tail_logical_lines]
Prints cumulative counts of each event + the last N decoded lines that match any event.
"""
import os
import re
import sys

LOG = os.environ.get(
    "DESKPET_E2E_LOG",
    r"G:\projects\deskpet\plans\manual-results-2026-06-26-bugb-phase1\tauri-dev.log",
)

EVENTS = [
    "intent_triage.allowlist_hit",
    "intent_triage.done",
    "intent_triage.llm_failed",
    "intent_triage.parse_failed",
    "intent_triage.shortcircuit",          # MUST stay 0 (retired rule short-circuit)
    "pipeline.short_circuit",
    "pipeline_short_circuit",
    "pipeline.pre_loop_done",
    "pipeline_clarification_pause",
    "evidence_gathered_set",
]


def decode():
    raw = open(LOG, "rb").read()
    txt = raw.decode("utf-16-le", errors="ignore").replace("\x00", "")
    # de-wrap: the rust pipe hard-wraps lines; join then re-split on log-level starts
    txt = txt.replace("\r", "")
    return txt


def main():
    txt = decode()
    # collapse hard-wraps: remove newlines that are mid-record (not followed by LEVEL:)
    lines = txt.split("\n")
    logical = []
    cur = ""
    START = re.compile(r"^(INFO|WARNING|ERROR|DEBUG|CRITICAL):")
    for ln in lines:
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
                matched.append((e, ll.strip()))
    print(f"total_logical_lines={len(logical)}")
    for e in EVENTS:
        print(f"  {e} = {counts[e]}")
    tail = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if tail:
        print("--- last matched events ---")
        for e, ll in matched[-tail:]:
            safe = ll[:220].encode("ascii", "replace").decode("ascii")
            print(f"  [{e}] {safe}")


if __name__ == "__main__":
    main()
