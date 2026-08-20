#!/bin/sh
set -eu
mode="${1:---list}"
critical='launch provider-settings session-list chat-send chat-stop history-reopen context-modal'
affected='provider-add provider-edit provider-delete provider-reorder session-model-params chat-ordinary chat-tool chat-attachment permission waiting cancel recovery thinking-group tool-outcome context-empty context-measured context-missing-usage context-failure context-restart context-multisession'
full_extra='memory-recall memory-search file-success file-failure shell-success shell-failure web-success web-failure'
case "$mode" in --critical) cases="$critical";; --affected) cases="$affected";;
  --full) cases="$critical $affected $full_extra";;
  --list) echo 'usage: surface-smoke-draft.sh --critical|--affected|--full'; exit 0;;
  *) echo "unknown mode" >&2; exit 2;; esac
echo "mode=$mode"
for id in $cases; do
  echo "NOT_RUN $id | snapshot -> declare coordinate/action/oracle -> real UI action -> screenshot -> redacted log"
done
echo 'Draft only: never reports PASS; record execution through the gate later.'
