"""corecmd.path: a core command is found in `$HOME/.agents/bin` first, then on PATH."""
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from steplib import corecmd  # noqa: E402


def make(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / name
    f.write_text("#!/bin/sh\n")
    f.chmod(f.stat().st_mode | stat.S_IXUSR)
    return str(f)


class CoreCmdTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.pathdir = self.root / "onpath"
        self.pathdir.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "PATH": str(self.pathdir)})
        env.start()
        self.addCleanup(env.stop)

    def test_home_copy_wins_over_a_path_copy(self):
        make(self.pathdir, "settings")
        mine = make(self.home / ".agents" / "bin", "settings")
        self.assertEqual(corecmd.path("settings"), mine)

    def test_path_is_used_when_home_has_none(self):
        theirs = make(self.pathdir, "capped")
        self.assertEqual(corecmd.path("capped"), theirs)

    def test_neither_present_returns_the_home_path(self):
        self.assertEqual(corecmd.path("remote-agent"),
                         os.path.join(str(self.home), ".agents", "bin", "remote-agent"))


if __name__ == "__main__":
    unittest.main()
