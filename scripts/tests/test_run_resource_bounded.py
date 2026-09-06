"""Real POSIX children, including an orphan ignoring SIGTERM; no model imports."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

RUNNER = Path(os.environ.get("RESOURCE_RUNNER_TEST_PATH",
    str(Path(__file__).resolve().parents[1] / "run_resource_bounded.py")))


@unittest.skipUnless(os.name == "posix", "POSIX process groups")
class ResourceRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.evidence = self.base / "evidence"
        self.lock = self.base / "slot.lock"

    def command(self, code, *options):
        return [sys.executable, str(RUNNER), "--evidence-dir", str(self.evidence),
                "--lock-file", str(self.lock), *options, "--", sys.executable, "-c", code]

    def execute(self, code, *options):
        result = subprocess.run(self.command(code, *options), capture_output=True,
                                text=True, timeout=15)
        receipt = json.loads((self.evidence / "resource.json").read_text())
        self.assertEqual(receipt["remaining_group_members"], [])
        self.assertIsNone(receipt["cleanup_error"])
        return result, receipt

    def disk_module(self):
        spec = importlib.util.spec_from_file_location("disk_resource_under_test", RUNNER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_low_disk_admission_starts_no_child_or_evidence(self):
        module = self.disk_module()
        marker = self.base / "must-not-start"
        from types import SimpleNamespace
        with patch("shutil.disk_usage", return_value=SimpleNamespace(free=439*1024**2)):
            result = module.run([sys.executable, "-c", "from pathlib import Path;Path("+repr(str(marker))+").touch()"],
                evidence=self.evidence, lock_path=self.lock, rss_mib=128, seconds=5)
        self.assertEqual(result, 125)
        self.assertFalse(marker.exists())
        self.assertFalse(self.evidence.exists())
        with self.lock.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_disk_probe_failure_before_spawn_is_closed(self):
        module = self.disk_module()
        marker = self.base / "must-not-start"
        with patch("shutil.disk_usage", side_effect=OSError("controlled disk probe failure")):
            result = module.run([sys.executable, "-c", "from pathlib import Path;Path("+repr(str(marker))+").touch()"],
                evidence=self.evidence, lock_path=self.lock, rss_mib=128, seconds=5)
        self.assertEqual(result, 125)
        self.assertFalse(marker.exists())
        self.assertFalse(self.evidence.exists())

    def test_disk_drop_stops_owned_child_and_keeps_evidence(self):
        module = self.disk_module()
        from types import SimpleNamespace
        marker = self.base / "started"
        child = "from pathlib import Path;import time;Path("+repr(str(marker))+").touch();time.sleep(60)"
        samples = []
        def disk_sample(path):
            samples.append(Path(path))
            if len(samples) <= 2:
                return SimpleNamespace(free=4*1024**3)
            until = time.monotonic()+5
            while not marker.exists() and time.monotonic() < until:
                time.sleep(.01)
            return SimpleNamespace(free=128*1024**2)
        with patch("shutil.disk_usage", disk_sample):
            result = module.run([sys.executable, "-c", child], evidence=self.evidence,
                lock_path=self.lock, rss_mib=128, seconds=.4)
        receipt = json.loads((self.evidence / "resource.json").read_text())
        self.assertTrue(marker.exists())
        self.assertEqual(result, 125)
        self.assertEqual(receipt["stop_reason"], "disk_limit")
        self.assertEqual(receipt["min_disk_free_mib"], 128)
        self.assertEqual(receipt["remaining_group_members"], [])
        self.assertIsNone(receipt["cleanup_error"])
        self.assertIn(Path.cwd(), samples)
        self.assertIn(self.base, samples)
        with self.assertRaises(ProcessLookupError):
            os.kill(receipt["group_id"], 0)

    def test_command_failure_keeps_its_exit_status(self):
        result, receipt = self.execute("raise SystemExit(7)")
        self.assertEqual(result.returncode, 7)
        self.assertIsNone(receipt["stop_reason"])

    def test_success_is_not_a_resource_failure(self):
        result, receipt = self.execute("print('done')")
        self.assertEqual(result.returncode, 0)
        self.assertIsNone(receipt["stop_reason"])

    def test_orphan_is_reaped_even_when_parent_has_exited(self):
        ready = self.base / "child-ready"
        child = "import signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path("+repr(str(ready))+").touch();time.sleep(60)"
        parent = "import subprocess,sys,time;from pathlib import Path;subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);p=Path("+repr(str(ready))+");\nwhile not p.exists():time.sleep(.01)"
        unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], start_new_session=True)
        try:
            result, receipt = self.execute(parent)
            self.assertEqual(result.returncode, 125)
            self.assertEqual(receipt["stop_reason"], "orphaned_group_after_parent_exit")
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=5)

    def test_deadline_stops_the_command(self):
        result, receipt = self.execute("import time;time.sleep(60)", "--seconds", ".4")
        self.assertEqual(result.returncode, 125)
        self.assertEqual(receipt["stop_reason"], "deadline")

    def test_rss_limit_stops_actual_allocation(self):
        result, receipt = self.execute("import time;data=bytearray(64*1024*1024);time.sleep(60)", "--rss-mib", "32")
        self.assertEqual(result.returncode, 125)
        self.assertEqual(receipt["stop_reason"], "rss_limit")
        self.assertGreater(receipt["peak_group_rss_kib"], 32*1024)

    def test_busy_slot_does_not_start_child(self):
        marker = self.base / "should-not-exist"
        with self.lock.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(self.command("from pathlib import Path;Path("+repr(str(marker))+").touch()"), capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 75)
        self.assertFalse(marker.exists())
        self.assertFalse(self.evidence.exists())

    def test_runner_sigterm_cleans_group_and_releases_slot(self):
        ready = self.base / "running"
        code = "from pathlib import Path;import time;Path("+repr(str(ready))+").touch();time.sleep(60)"
        with (self.base / "signal.log").open("w") as log:
            process = subprocess.Popen(self.command(code), stdout=log, stderr=subprocess.STDOUT)
            try:
                until = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < until:
                    time.sleep(.01)
                self.assertTrue(ready.exists())
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=10), 125)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
        receipt = json.loads((self.evidence / "resource.json").read_text())
        self.assertEqual(receipt["stop_reason"], "signal_15")
        self.assertEqual(receipt["remaining_group_members"], [])
        with self.lock.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_failed_probe_still_kills_term_resistant_process(self):
        spec = importlib.util.spec_from_file_location("resource_under_test", RUNNER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ready = self.base / "ready"
        child = "import signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path("+repr(str(ready))+").touch();time.sleep(60)"
        def broken_probe(_group):
            until = time.monotonic()+5
            while not ready.exists() and time.monotonic() < until:
                time.sleep(.01)
            raise RuntimeError("controlled ps probe failure")
        with patch.object(module, "members", broken_probe):
            result = module.run([sys.executable, "-c", child], evidence=self.evidence,
                lock_path=self.lock, rss_mib=128, seconds=5)
        receipt = json.loads((self.evidence / "resource.json").read_text())
        pid = receipt["group_id"]
        try:
            self.assertEqual(result, 125)
            self.assertEqual(receipt["cleanup_error"], "RuntimeError")
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            # Also cleans the intentionally failing old-code counterexample.
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_child_forked_between_snapshot_and_parent_exit_is_not_green(self):
        spec = importlib.util.spec_from_file_location("resource_race", RUNNER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        go = self.base / "go"
        ready = self.base / "parent-ready"
        child_ready = self.base / "child-ready"
        child = "import time,signal;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path("+repr(str(child_ready))+").touch();time.sleep(60)"
        parent = ("import time,subprocess,sys;from pathlib import Path;Path("+repr(str(ready))+").touch();\n"
            "while not Path("+repr(str(go))+").exists():time.sleep(.01)\n"
            "subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);\n"
            "while not Path("+repr(str(child_ready))+").exists():time.sleep(.01)")
        original = module.members
        first = True
        def delayed_snapshot(group):
            nonlocal first
            if not first:
                return original(group)
            first = False
            until = time.monotonic()+5
            while not ready.exists() and time.monotonic() < until:
                time.sleep(.01)
            snapshot = original(group)
            go.touch()
            while time.monotonic() < until:
                if child_ready.exists() and not any(r["pid"] == group for r in original(group)):
                    break
                time.sleep(.01)
            return snapshot
        with patch.object(module, "members", delayed_snapshot):
            result = module.run([sys.executable, "-c", parent], evidence=self.evidence,
                lock_path=self.lock, rss_mib=128, seconds=10)
        receipt = json.loads((self.evidence / "resource.json").read_text())
        self.assertEqual(result, 125)
        self.assertEqual(receipt["stop_reason"], "orphaned_group_after_parent_exit")
        self.assertEqual(receipt["remaining_group_members"], [])

    def test_sigterm_between_spawn_and_assignment_keeps_cleanup_ownership(self):
        spec = importlib.util.spec_from_file_location("resource_signal_race", RUNNER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ready = self.base / "ready"
        child = "import time;from pathlib import Path;Path("+repr(str(ready))+").touch();time.sleep(60)"
        real_popen = subprocess.Popen
        spawned = []
        def signal_before_return(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            if not spawned:
                spawned.append(process)
                until = time.monotonic()+5
                while not ready.exists() and time.monotonic() < until:
                    time.sleep(.01)
                os.kill(os.getpid(), signal.SIGTERM)
            return process
        try:
            with patch.object(module.subprocess, "Popen", signal_before_return):
                result = module.run([sys.executable, "-c", child], evidence=self.evidence,
                    lock_path=self.lock, rss_mib=128, seconds=10)
            receipt = json.loads((self.evidence / "resource.json").read_text())
            self.assertEqual(result, 125)
            self.assertEqual(receipt["group_id"], spawned[0].pid)
            self.assertIsNotNone(spawned[0].poll())
        finally:
            if spawned and spawned[0].poll() is None:
                os.killpg(spawned[0].pid, signal.SIGKILL)
                spawned[0].wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
