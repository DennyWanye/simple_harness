#!/bin/bash
# wait for the rep0 parent to exit, then start rep1 detached (same runner, same identity, resume-safe)
cd "/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-16/a96-grok"
while kill -0 30307 2>/dev/null; do sleep 20; done
sleep 5
if [ -f matrix-grok46-256k-v2/STOP ]; then echo "STOP present; not starting rep1" >> chain-rep1.log; exit 0; fi
echo "rep0 parent exited at $(date +%H:%M:%S); starting rep1" >> chain-rep1.log
nohup ./appworld-venv/bin/python run-a96-grok46.py --workers 2 --reps 1 > batch-v2-rep1-parent.log 2>&1 < /dev/null &
