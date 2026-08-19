#!/usr/bin/env bash
# C1 verify: re-vendored wheel SHA + version single-source-of-truth + import smoke.
set -euo pipefail
cd "$(dirname "$0")/../../.."

PY=backend/.venv/bin/python

"$PY" scripts/verify_sdk_wheel.py backend/vendor/simple_harness_sdk-0.1.4-py3-none-any.whl

PYTHONPATH=backend "$PY" -c "
import hashlib
from deskpet.sdk_adapters.sdk_candidate import (
    SDK_VERSION, SDK_MEMORY_VERSION, SDK_WHEEL_SHA256, SDK_MEMORY_WHEEL_SHA256,
)
import simple_harness, simple_harness_memory
assert SDK_VERSION == simple_harness.__version__, (SDK_VERSION, simple_harness.__version__)
assert SDK_MEMORY_VERSION == simple_harness_memory.__version__, (SDK_MEMORY_VERSION, simple_harness_memory.__version__)
for fn, expected in [
    ('backend/vendor/simple_harness_sdk-0.1.4-py3-none-any.whl', SDK_WHEEL_SHA256),
    ('backend/vendor/simple_harness_memory_sdk-0.2.0-py3-none-any.whl', SDK_MEMORY_WHEEL_SHA256),
]:
    actual = hashlib.sha256(open(fn, 'rb').read()).hexdigest()
    assert actual == expected, (fn, actual, expected)
print('C1 verify OK: versions + SHAs consistent')
"
