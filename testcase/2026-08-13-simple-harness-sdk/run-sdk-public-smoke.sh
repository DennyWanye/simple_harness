#!/usr/bin/env bash
set -euo pipefail

: "${SDK_PYTHON:?set SDK_PYTHON to the clean exact-wheel Python}"
: "${CONFORMANCE_HOST:?set CONFORMANCE_HOST to a public module:factory}"
: "${EVIDENCE_RUN_DIR:?set EVIDENCE_RUN_DIR below .local-test-evidence}"

repo_root="$(pwd -P)"
evidence_root="$repo_root/.local-test-evidence/"
case "$EVIDENCE_RUN_DIR" in
  "$repo_root"/.local-test-evidence/*) ;;
  *) echo "EVIDENCE_RUN_DIR must be an absolute path below $evidence_root" >&2; exit 2 ;;
esac
mkdir -p "$EVIDENCE_RUN_DIR/smoke"
resolved_evidence="$(cd "$EVIDENCE_RUN_DIR" && pwd -P)/"
case "$resolved_evidence" in
  "$evidence_root"*) ;;
  *) echo "EVIDENCE_RUN_DIR escapes .local-test-evidence" >&2; exit 2 ;;
esac

"$SDK_PYTHON" -I - <<'PY' > "$resolved_evidence/smoke/import.txt"
import simple_harness
print("SDK_IMPORT_OK", getattr(simple_harness, "__version__", "version-unexposed"))
PY

"$SDK_PYTHON" -m simple_harness.testing \
  --host "$CONFORMANCE_HOST" \
  --suite provider,tool,runtime,workflow \
  --json "$resolved_evidence/smoke/conformance.json"

echo "SDK_PUBLIC_SMOKE_READY"
