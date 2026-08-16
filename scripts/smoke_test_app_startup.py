#!/usr/bin/env python3
"""Minimal smoke test for Simple Harness desktop app after SDK vendoring.

Verifies that vendored SDK wheel doesn't break application startup.
Does NOT test SDK integration (T6 deferred to v0.2.0).

Usage:
    python scripts/smoke_test_app_startup.py
"""

import subprocess
import sys
import time
from pathlib import Path


def check_backend_starts():
    """Verify backend can start with vendored SDK."""
    print("🔍 Testing backend startup...")

    backend_dir = Path(__file__).parent.parent / "backend"

    # Start backend
    proc = subprocess.Popen(
        ["uv", "run", "python", "main.py"],
        cwd=backend_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Wait for startup (look for "Uvicorn running" in logs)
        time.sleep(5)

        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            print(f"❌ Backend crashed during startup")
            print(f"STDOUT:\n{stdout}")
            print(f"STDERR:\n{stderr}")
            return False

        print("✅ Backend started successfully")
        return True

    finally:
        proc.terminate()
        proc.wait(timeout=5)


def check_sdk_imports():
    """Verify SDK can be imported from backend venv."""
    print("🔍 Testing SDK imports in backend venv...")

    result = subprocess.run(
        ["uv", "run", "python", "-c",
         "import simple_harness; "
         "from deskpet.sdk_adapters.composition import build_product_runtime; "
         "print(f'SDK version: {simple_harness.__version__}')"],
        cwd=Path(__file__).parent.parent / "backend",
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"❌ SDK imports failed")
        print(f"STDERR:\n{result.stderr}")
        return False

    print(f"✅ {result.stdout.strip()}")
    return True


def check_old_harness_imports():
    """Verify old harness code still importable (T6 not yet cutover)."""
    print("🔍 Testing old harness imports (should still work)...")

    result = subprocess.run(
        ["uv", "run", "python", "-c",
         "from deskpet.harness.bootstrap import build_harness_runtime; "
         "print('Old harness imports: OK')"],
        cwd=Path(__file__).parent.parent / "backend",
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"❌ Old harness imports failed (CRITICAL - T6 not cutover yet)")
        print(f"STDERR:\n{result.stderr}")
        return False

    print(f"✅ {result.stdout.strip()}")
    return True


def main():
    """Run minimal smoke tests."""
    print("=" * 60)
    print("Simple Harness SDK Vendoring Smoke Test")
    print("=" * 60)
    print()
    print("This verifies that vendored SDK doesn't break the app.")
    print("Does NOT test SDK integration (T6 deferred to v0.2.0).")
    print()

    tests = [
        ("SDK imports", check_sdk_imports),
        ("Old harness imports", check_old_harness_imports),
        ("Backend startup", check_backend_starts),
    ]

    results = []
    for name, test_fn in tests:
        try:
            success = test_fn()
            results.append((name, success))
        except Exception as e:
            print(f"❌ {name} raised exception: {e}")
            results.append((name, False))
        print()

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, success in results if success)
    total = len(results)

    for name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} - {name}")

    print()
    print(f"Result: {passed}/{total} tests passed")

    if passed < total:
        print()
        print("⚠️  CRITICAL: Vendored SDK broke application startup")
        print("    Do NOT proceed with release until fixed")
        sys.exit(1)
    else:
        print()
        print("✅ Application startup verified after SDK vendoring")
        print("   Old harness still works (T6 cutover pending v0.2.0)")
        sys.exit(0)


if __name__ == "__main__":
    main()
