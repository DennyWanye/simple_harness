#!/bin/bash
# Run one coding-agent task on the DeepSeek day-card lane, using the codex CLI as the agent runtime
# but pointing it at the local queueing gate instead of ~/.codex/config.toml's provider (which
# cc-switch now owns for the official lane).  Same interface as codex_task.sh.
# Usage: deepseek_task.sh <name> <cwd> <prompt-file> [effort=high] [sandbox=workspace-write] [resume-session-id]
# The key is read from the Host .env at call time, exported to the child only, and never printed.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$(cd "$HERE/../../.." && pwd)/.env"
[ -f "$ENV_FILE" ] || { echo "DEEPSEEK_TASK rc=96 env-file-not-found"; exit 96; }
DEEPSEEKER_APIKEY="$(python3 -c '
import sys
for line in open(sys.argv[1]):
    k, _, v = line.strip().partition("=")
    if k.strip().removeprefix("export ").strip() == "DEEPSEEKER_APIKEY":
        print(v.strip().strip("\"").strip("'"'"'")); break
' "$ENV_FILE")"
[ -n "$DEEPSEEKER_APIKEY" ] || { echo "DEEPSEEK_TASK rc=95 key-missing"; exit 95; }
export DEEPSEEKER_APIKEY
PORT="${DAYCARD_GATE_DEV_PORT:-28182}"
nc -z 127.0.0.1 "$PORT" 2>/dev/null || { echo "DEEPSEEK_TASK rc=94 gate-not-listening on 127.0.0.1:$PORT (start daycard_gate.py)"; exit 94; }
export CODEX_USE_GATE=0   # the base_url below already points at the gate
export CODEX_EXTRA_CONFIG="model_provider=\"daycard\"
model_providers.daycard.name=\"deepseek-daycard\"
model_providers.daycard.base_url=\"http://127.0.0.1:$PORT/v1\"
model_providers.daycard.wire_api=\"responses\"
model_providers.daycard.requires_openai_auth=false
model_providers.daycard.env_key=\"DEEPSEEKER_APIKEY\"
model=\"${DEEPSEEK_TASK_MODEL:-deepseek-v4.1-flash}\""
export AGENT_TASK_OUT="${AGENT_TASK_OUT:-$HOME/.cache/simpleharness-agent-tasks/deepseek}"
exec "$HERE/codex_task.sh" "$@"
