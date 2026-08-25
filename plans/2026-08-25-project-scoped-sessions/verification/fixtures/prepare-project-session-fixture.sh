#!/usr/bin/env bash
set -euo pipefail

fixture_root=${1:?usage: prepare-project-session-fixture.sh ABSOLUTE_FIXTURE_ROOT}
case "$fixture_root" in
  /*) ;;
  *) echo "fixture root must be absolute" >&2; exit 2 ;;
esac
case "$fixture_root" in
  /|/Users|/tmp) echo "fixture root is too broad" >&2; exit 2 ;;
esac
if [[ -e "$fixture_root" ]]; then
  echo "fixture root already exists: $fixture_root" >&2
  exit 2
fi

mkdir -p "$fixture_root/userdata" "$fixture_root/projects/git-repo/sub/child" \
  "$fixture_root/projects/plain-folder" "$fixture_root/projects/unrelated" \
  "$fixture_root/projects/execution-root" "$fixture_root/evidence-staging"
git -C "$fixture_root/projects/git-repo" init -q
git -C "$fixture_root/projects/git-repo" config user.name "simple_harness verification"
git -C "$fixture_root/projects/git-repo" config user.email "verification@example.invalid"
printf 'project-root-canary\n' > "$fixture_root/projects/git-repo/project-canary.txt"
printf 'execution-root-canary\n' > "$fixture_root/projects/execution-root/execution-canary.txt"
printf 'plain-folder-canary\n' > "$fixture_root/projects/plain-folder/plain-canary.txt"
printf 'unrelated-canary\n' > "$fixture_root/projects/unrelated/unrelated-canary.txt"
ln -s "$fixture_root/projects/git-repo" "$fixture_root/projects/git-repo-link"
printf '{"fixture":"project-scoped-sessions","version":1}\n' > "$fixture_root/.project-session-fixture.json"

printf 'export DESKPET_USER_DATA_DIR=%q\n' "$fixture_root/userdata"
printf 'export PROJECT_SESSION_FIXTURE_ROOT=%q\n' "$fixture_root"
printf 'raw evidence target: .local-test-evidence/<date>/<run-id>/\n'

