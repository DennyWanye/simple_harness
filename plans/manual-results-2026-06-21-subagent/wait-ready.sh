#!/usr/bin/env bash
# Poll the UTF-16LE tauri-dev log; emit one line when backend is ready or failed.
LOG="G:/projects/deskpet/plans/manual-results-2026-06-21-subagent/tauri-dev.log"
for i in $(seq 1 240); do
  # Convert UTF-16LE -> UTF-8 (ignore errors on partial multibyte tail).
  txt=$(iconv -f UTF-16LE -t UTF-8//TRANSLIT "$LOG" 2>/dev/null | tr -d '\000')
  if echo "$txt" | grep -Eq "os error 10048|panicked|exited without SHARED_SECRET|Error: |ERROR.*backend_launch"; then
    echo "FAIL-MARKER:"
    echo "$txt" | grep -Eo "os error 10048|panicked|exited without SHARED_SECRET" | head -1
    exit 1
  fi
  if echo "$txt" | grep -Eq "Application startup complete|Uvicorn running|backend.*ready|ws.*listening|Started server process"; then
    echo "BACKEND-READY"
    echo "$txt" | grep -E "backend_launch|Application startup|Uvicorn running" | tail -3
    exit 0
  fi
  sleep 2
done
echo "TIMEOUT-no-ready-marker"
exit 2
