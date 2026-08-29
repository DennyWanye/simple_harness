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

target="$repo_root/.local-test-evidence/2026-08-29/$run_id"
case "$target" in
  "$repo_root"/.local-test-evidence/2026-08-29/*) ;;
  *) echo "refusing unsafe cleanup target" >&2; exit 65 ;;
esac

if [ -d "$target/invalid/read-only" ]; then
  chmod 700 "$target/invalid/read-only"
fi
echo "Evidence retained at $target; cleanup is intentionally non-destructive."
