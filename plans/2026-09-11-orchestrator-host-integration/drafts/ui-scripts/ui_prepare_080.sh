#!/bin/bash
# Prepare an isolated COPY of the real user data for the native UI test and launch the app.
set -e
H=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness
EV=$H/.local-test-evidence/2026-09-10/native-ui-080
REAL="$HOME/Library/Application Support/com.dennywanye.simpleharness"
rm -rf $EV/userdata; mkdir -p $EV/userdata
for n in data capabilities companion-local-identity.json llm_runtime.json config.toml; do
  [ -e "$REAL/$n" ] && cp -R "$REAL/$n" $EV/userdata/ ; done
find $EV/userdata -name "*.writer.lock" -delete
sqlite3 $EV/userdata/data/simple-harness-sdk/execution-v6.sqlite3 "SELECT group_concat(version) FROM sdk_schema_migrations" > $EV/schema-before.txt
echo "schema before: $(cat $EV/schema-before.txt)"
BUNDLE="$H/tauri-app/src-tauri/target/debug/bundle/macos/SimpleHarness Agent Verify f0027c98p18120.app"
exec $H/backend/.venv/bin/python $H/scripts/native/launch_native_candidate.py \
  --source $H --bundle "$BUNDLE" --evidence-root $EV --userdata $EV/userdata \
  --installed-target $H/.local-test-evidence/2026-09-10/installed-h080-s0313 \
  --fallback-model deepseek-v4-pro --fallback-env-file $H/.local-test-evidence/2026-09-07/credentials/deepseek.env \
  --launch
