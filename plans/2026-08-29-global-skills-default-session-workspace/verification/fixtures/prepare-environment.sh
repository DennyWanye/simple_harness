#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <repo-root> <run-id>" >&2
  exit 64
fi

repo_root=$(cd "$1" && pwd -P)
run_id=$2
case "$run_id" in
  *[!A-Za-z0-9._-]*|'') echo "invalid run-id" >&2; exit 64 ;;
esac

evidence_root="$repo_root/.local-test-evidence/2026-08-29/$run_id"
mkdir -p "$evidence_root/user-data" "$evidence_root/selected/Unicode-目录" "$evidence_root/invalid"
printf 'selected-canary\n' > "$evidence_root/selected/Unicode-目录/canary.txt"
printf 'not-a-directory\n' > "$evidence_root/invalid/file-target"
mkdir -p "$evidence_root/invalid/read-only"
chmod 500 "$evidence_root/invalid/read-only"

printf 'DESKPET_USER_DATA_DIR=%s\n' "$evidence_root/user-data"
printf 'GS_SELECTED_DIR=%s\n' "$evidence_root/selected/Unicode-目录"
printf 'GS_FILE_TARGET=%s\n' "$evidence_root/invalid/file-target"
printf 'GS_READONLY_TARGET=%s\n' "$evidence_root/invalid/read-only"
printf 'GS_EVIDENCE_ROOT=%s\n' "$evidence_root"
