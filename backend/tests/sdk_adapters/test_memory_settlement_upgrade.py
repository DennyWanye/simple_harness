"""Actual old installed catalog to the new Host public-migration composition."""
import hashlib
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime


@pytest.mark.asyncio
async def test_host_upgrades_actual_m616_store_and_reopens_same_public_receipts(tmp_path):
    legacy = os.environ.get("HM_TEST_LEGACY_MEMORY_TARGET")
    if not legacy:
        pytest.skip("Requires frozen M616 installed target for cross-version acceptance")
    assert Path(legacy).is_dir()
    path = tmp_path / "memory.db"
    owner_result = tmp_path / "old-owner.json"
    host_backend = Path(__file__).resolve().parents[2]
    script = '''
import asyncio, json, sys
from dataclasses import asdict
from pathlib import Path
from importlib.metadata import version
sys.path[:0] = [sys.argv[2], sys.argv[3]]
assert version('simple-harness-memory-sdk') == '0.6.16'
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
async def run():
    runtime = HumanMemoryV7Runtime(sys.argv[1])
    try:
        assert await runtime.pending_occurrences(()) == ()
        Path(sys.argv[4]).write_text(json.dumps(asdict(runtime.registration_receipt)))
    finally:
        await runtime.close()
asyncio.run(run())
'''
    subprocess.run([sys.executable, "-I", "-B", "-c", script,
                              str(path), str(host_backend), legacy, str(owner_result)],
                             capture_output=True, text=True, check=True, timeout=20)
    original_owner = json.loads(owner_result.read_text())
    runtime = HumanMemoryV7Runtime(path)
    try:
        assert await runtime.pending_occurrences(()) == ()
        assert asdict(runtime.registration_receipt) == original_owner
        receipt = runtime.settlement_schema_upgrade_receipt
        assert receipt is not None
        backup = path.with_name(path.name + ".pre-schema-7.3.backup")
        assert receipt.backup_sha256 == hashlib.sha256(backup.read_bytes()).hexdigest()
        backup_hash = receipt.backup_sha256
    finally:
        await runtime.close()
    reopened = HumanMemoryV7Runtime(path)
    try:
        assert await reopened.pending_occurrences(()) == ()
        assert asdict(reopened.registration_receipt) == original_owner
        assert reopened.settlement_schema_upgrade_receipt == receipt
        assert hashlib.sha256(backup.read_bytes()).hexdigest() == backup_hash
    finally:
        await reopened.close()
