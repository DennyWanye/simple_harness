#!/bin/sh
set -eu
run_tag="${1:-run-1}"
case "$run_tag" in *[!A-Za-z0-9._-]*|'') echo "invalid run tag" >&2; exit 2;; esac
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
fixture_root="$repo_root/.local-test-evidence/2026-08-21/sdk-context-authority-$run_tag"
[ ! -e "$fixture_root" ] || { echo "fixture exists: $fixture_root" >&2; exit 3; }
mkdir -p "$fixture_root/userdata" "$fixture_root/workspace" "$fixture_root/evidence"
: > "$fixture_root/.deskpet-isolated-fixture"
printf '%s\n' 'CTX3-FIRST-LINE-2026-08-21' 'This is public test content.' > "$fixture_root/workspace/public-context-fixture.txt"
printf '%s\n' 'PUBLIC-INSPECTOR-CANARY-ONLY' > "$fixture_root/workspace/public-canary.txt"
echo "FIXTURE_ROOT=$fixture_root"
echo "ISOLATED_USERDATA=$fixture_root/userdata"
echo "WORKSPACE=$fixture_root/workspace"
echo "ATTACHMENT=$fixture_root/workspace/public-context-fixture.txt"
echo "EVIDENCE_DIR=$fixture_root/evidence"
echo "Enter Provider credentials only through App UI; never record them."
