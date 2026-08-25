#!/usr/bin/env bash
set -euo pipefail

fixture_root=${1:?usage: cleanup-project-session-fixture.sh ABSOLUTE_FIXTURE_ROOT}
case "$fixture_root" in
  /*) ;;
  *) echo "fixture root must be absolute" >&2; exit 2 ;;
esac
case "$fixture_root" in
  /|/Users|/tmp) echo "fixture root is too broad" >&2; exit 2 ;;
esac
marker="$fixture_root/.project-session-fixture.json"
if [[ ! -f "$marker" ]] || ! grep -q '"fixture":"project-scoped-sessions"' "$marker"; then
  echo "refusing cleanup: exact fixture marker missing" >&2
  exit 2
fi
if [[ "$fixture_root" != *project-session* ]]; then
  echo "refusing cleanup: path lacks project-session marker" >&2
  exit 2
fi

rm -rf -- "$fixture_root"
printf 'removed marked fixture only: %s\n' "$fixture_root"
printf 'raw evidence under .local-test-evidence was not removed\n'
