#!/usr/bin/env bash
set -euo pipefail

lane=${1:-critical}
case "$lane" in
  critical)
    printf '%s\n' 'launch' 'project registration' 'new project Session' 'chat send/terminal' 'history reopen' 'read-only Inspector'
    ;;
  affected)
    printf '%s\n' 'projectless chat/handoff' 'project/execution split' 'missing/relocate' 'Session rename/archive/delete/resume' 'Memory and Provider binding' 'pagination/refresh'
    ;;
  full)
    printf '%s\n' 'all critical surfaces' 'all affected surfaces' 'settings' 'memory' 'file/terminal success and failure' 'permission/wait/cancel/recovery' 'attachments/artifacts'
    ;;
  *)
    echo 'usage: surface-smoke-draft.sh critical|affected|full' >&2
    exit 2
    ;;
esac

printf '%s\n' 'Draft only: execute each UI entry with real Computer Use and write raw evidence to .local-test-evidence.'

