"""`EvalSandbox`: the unittest base every evals test uses, so no test reads or writes the real home, store or tokens.

Each test gets a temp `HOME` (with `XDG_CONFIG_HOME` and `CLAUDE_CONFIG_DIR` inside it), `EVALS_RESULTS` in a temp
folder of its own, a fake-binary folder first on `PATH` (`fake_bin()` writes one) and fake token values, kept in
`tokens` so a test can prove they never reach a kept file. Every variable is restored, and the temp folder removed,
even when the test fails.
"""
import os
import secrets
import shutil
import stat
import tempfile
import unittest
from pathlib import Path


class EvalSandbox(unittest.TestCase):
    TOKEN_VARS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "CODEX_API_KEY", "CURSOR_API_KEY")
    VARS = ("HOME", "XDG_CONFIG_HOME", "CLAUDE_CONFIG_DIR", "EVALS_RESULTS", "PATH") + TOKEN_VARS

    def setUp(self):
        super().setUp()
        saved = {v: os.environ.get(v) for v in self.VARS}
        self.addCleanup(self._restore, saved)
        self.tmp = Path(tempfile.mkdtemp(prefix="evals-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.results = self.tmp / "results"
        self.bin = self.tmp / "bin"
        for d in (self.home / ".config", self.home / ".claude", self.results, self.bin):
            d.mkdir(parents=True)
        os.environ.update({
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
            "EVALS_RESULTS": str(self.results),
            "PATH": str(self.bin) + os.pathsep + (saved["PATH"] or os.defpath),
        })
        self.tokens = {v: f"fake-{v.lower()}-{secrets.token_hex(8)}" for v in self.TOKEN_VARS}
        os.environ.update(self.tokens)

    def fake_bin(self, name, body):
        """Write an executable `sh` script `name` into the fake-binary folder; returns its path."""
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return path

    @staticmethod
    def _restore(saved):
        for var, value in saved.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
