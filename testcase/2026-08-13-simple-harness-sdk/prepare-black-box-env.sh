#!/usr/bin/env bash
set -euo pipefail

: "${SDK_WHEEL:?set SDK_WHEEL to the exact release wheel}"
: "${SDK_WHEEL_SHA256:?set SDK_WHEEL_SHA256 to the release digest}"
: "${EVIDENCE_RUN_DIR:?set EVIDENCE_RUN_DIR below .local-test-evidence}"
: "${DESKPET_EXISTING_LOGIN_CONFIRMED:?set to 1 after confirming an existing dev login}"

if [[ "$DESKPET_EXISTING_LOGIN_CONFIRMED" != "1" ]]; then
  echo "Existing dev login is required; SR-9 forbids pretending to test first-login cold start." >&2
  exit 2
fi

repo_root="$(pwd -P)"
evidence_root="$repo_root/.local-test-evidence/"
case "$EVIDENCE_RUN_DIR" in
  "$repo_root"/.local-test-evidence/*) ;;
  *) echo "EVIDENCE_RUN_DIR must be an absolute path below $evidence_root" >&2; exit 2 ;;
esac
mkdir -p "$EVIDENCE_RUN_DIR"
resolved_evidence="$(cd "$EVIDENCE_RUN_DIR" && pwd -P)/"
case "$resolved_evidence" in
  "$evidence_root"*) ;;
  *) echo "EVIDENCE_RUN_DIR must resolve below $evidence_root" >&2; exit 2 ;;
esac

actual_sha="$(shasum -a 256 "$SDK_WHEEL" | awk '{print $1}')"
if [[ "$actual_sha" != "$SDK_WHEEL_SHA256" ]]; then
  echo "wheel digest mismatch" >&2
  exit 3
fi

python3.11 -m venv "$resolved_evidence/venv"
"$resolved_evidence/venv/bin/python" -m pip install --disable-pip-version-check "$SDK_WHEEL"
"$resolved_evidence/venv/bin/python" - <<'PY' > "$resolved_evidence/platform.txt"
import platform
import sys
print(platform.system(), platform.machine(), sys.version.split()[0])
PY

printf '%s\n' "$actual_sha" > "$resolved_evidence/wheel.sha256"
mkdir -p "$resolved_evidence/artifact" "$resolved_evidence/conformance" \
  "$resolved_evidence/ui" "$resolved_evidence/ledger" "$resolved_evidence/negative"
echo "BLACK_BOX_ENV_READY $resolved_evidence"
