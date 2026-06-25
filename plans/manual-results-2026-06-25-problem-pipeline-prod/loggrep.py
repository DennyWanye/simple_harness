#!/usr/bin/env python3
"""De-wrap the UTF-16LE tauri-wi5.log (hard-wrapped ~116 chars by the Rust pipe)
and grep pipeline events from a given baseline line offset.

Usage:
  python loggrep.py <baseline_logical_lines> [filter_regex]

Prints: matched logical (de-wrapped) lines after the baseline, plus a count
summary of key pipeline events in that window.
"""
import re
import sys

LOG = r"G:\projects\deskpet\plans\manual-results-2026-06-25-problem-pipeline-prod\tauri-wi5f.log"

# A logical line starts with a python-logging level prefix.
START = re.compile(r"^(INFO|WARNING|ERROR|DEBUG|CRITICAL):")

EVENTS = [
    "intent_triage.done",
    "intent_triage.shortcircuit",   # MUST be 0 under Y-light (old rule short-circuit)
    "intent_triage.llm_failed",
    "intent_triage.parse_failed",
    "pipeline.short_circuit",
    "pipeline_short_circuit",
    "pipeline_clarification_pause",
    "clarify_persist_assistant_failed",
    "evidence_gathered_set",
    "evidence_gate.blocked",
    "evidence_gate_nudge_injected",
    "evidence_gate_exhausted",
    "self_check.done",
    "self_check_nudge_injected",
    "convergence.stop_loss",
    "chat_v2_contradiction",
]


def load_logical():
    with open(LOG, "r", encoding="utf-16-le", errors="replace") as f:
        raw = f.read().splitlines()
    logical = []
    for ln in raw:
        if START.match(ln) or not logical:
            logical.append(ln)
        else:
            logical[-1] += ln  # continuation of a wrapped line
    return logical


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    baseline = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    filt = sys.argv[2] if len(sys.argv) > 2 else None
    logical = load_logical()
    window = logical[baseline:]
    print(f"TOTAL_LOGICAL={len(logical)} WINDOW_FROM={baseline} WINDOW_LEN={len(window)}")
    if filt:
        rx = re.compile(filt)
        for ln in window:
            if rx.search(ln):
                # strip the noisy prefix for readability
                s = re.sub(r"^[A-Z]+:[^:]*:event=", "", ln)
                print(s[:300])
    print("--- EVENT COUNTS (in window) ---")
    for ev in EVENTS:
        c = sum(1 for ln in window if ev in ln)
        if c:
            print(f"{ev} = {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
