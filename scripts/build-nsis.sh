#!/usr/bin/env bash
# Build + sign NSIS installer in Git Bash (empty signing password via
# < /dev/null so any hidden prompt EOFs instead of hanging — see
# release/README.md §5). Backend bundled from backend/dist-portable junction
# (-> F:\deskpet-build\dist, fresh 6/28 backend with the empty-Bearer fix).
set -euo pipefail
cd /g/projects/deskpet/tauri-app

export TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri/deskpet.key)"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""

echo "=== tauri build (build:relay -> vite --mode relay; bundles nsis) ==="
npm run tauri build -- --bundles nsis < /dev/null
echo "NSIS_BUILD_EXIT=$?"

echo "=== artifacts ==="
ls -la src-tauri/target/release/bundle/nsis/ | grep -i beta.9 || true
