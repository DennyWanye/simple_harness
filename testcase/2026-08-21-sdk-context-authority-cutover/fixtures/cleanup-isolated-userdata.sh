#!/bin/sh
set -eu
fixture_root="${1:-}"
[ -n "$fixture_root" ] && [ -f "$fixture_root/.deskpet-isolated-fixture" ] || {
  echo "refusing cleanup: exact marked fixture root required" >&2; exit 2; }
case "$fixture_root" in */.local-test-evidence/2026-08-21/sdk-context-authority-*) ;;
  *) echo "refusing cleanup outside isolated namespace" >&2; exit 2;; esac
if [ "${2:-}" = "--purge-userdata" ]; then
  rm -rf -- "$fixture_root/userdata" "$fixture_root/workspace"
  echo "Removed isolated userdata/workspace; retained $fixture_root/evidence"
else
  echo "Dry run. Stop App processes, hash evidence, then add --purge-userdata."
fi
