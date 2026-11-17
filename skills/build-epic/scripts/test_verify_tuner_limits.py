"""Tests for `verify_tuner.py`'s time limits and interrupts. They wait on the clock, so they sit apart from
`test_verify_tuner.py` and run beside it."""
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_verify_tuner import VERIFY, VerifyTunerCase  # noqa: E402

# A loop that never ends but sleeps, so a case waiting out its limit leaves the CPU to the suites running beside it.
LOOP = 'while True: __import__("time").sleep(0.05)'


class VerifyTunerLimitsTest(VerifyTunerCase):
    # The looping cases wait out their whole limit, so they cap the Checks line low; 2 s still lets the green run pass.
    def test_a_looping_mutation_is_stopped_by_the_checks_time_limit_and_counts_as_red(self):
        self.checks("capped --mb 2000 --secs 2 -- python3 -m unittest -q")
        self.write_report([("calc.py", 6, "return a - b", LOOP)])
        before = self.snapshot()
        start = time.monotonic()
        p = self.verify()
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(p.stdout, "OK 01-tests verified mutations=1\n")
        self.assertEqual(self.snapshot(), before)
        log = (self.kit.run_dir / "logs" / "01-tests-verify_tuner.log").read_text(encoding="utf-8")
        self.assertIn("timeout after 2s", log)

    def test_a_looping_green_suite_fails_on_the_time_limit(self):
        self.checks("capped --mb 2000 --secs 1 -- python3 -m unittest -q")
        self.tune(f"\n    def test_slow(self):\n        {LOOP}\n")
        self.write_report([("calc.py", 6, "a - b", "a + b")])
        p = self.verify()
        self.assertFails(p, "timeout capped --mb 2000 --secs 1 -- python3 -m unittest -q")
        log = (self.kit.run_dir / "logs" / "01-tests-verify_tuner.log").read_text(encoding="utf-8")
        self.assertIn("timeout after 1s", log)

    # Windows delivers neither signal to a handler: `send_signal(SIGTERM)` ends the process outright, and SIGINT is
    # refused. Nothing there can restore the file.
    @unittest.skipIf(os.name == "nt", "Windows sends no SIGTERM or SIGINT a process can catch")
    def test_an_interrupted_mutation_restores_the_worktree(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(sig=sig):
                self.write_report([("calc.py", 6, "return a - b", LOOP)])
                before = self.snapshot()
                p = subprocess.Popen([sys.executable, "-I", str(VERIFY), str(self.kit.run_dir), "01-tests",
                                      "--report", str(self.report), "--timeout", "60"], env=self.kit.env, text=True,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     encoding="utf-8")
                calc = self.wt / "calc.py"
                deadline = time.monotonic() + 15
                while "while True" not in calc.read_text(encoding="utf-8") and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertIn("while True", calc.read_text(encoding="utf-8"))
                p.send_signal(sig)
                out, err = p.communicate(timeout=15)
                self.assertEqual(p.returncode, 1, out + err)
                self.assertTrue(out.startswith("FAIL 01-tests interrupted (log: "), out)
                self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
