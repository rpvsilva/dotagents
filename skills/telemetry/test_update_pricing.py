import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
LITELLM = str(FIXTURES / "litellm.json")


class PriceCheck(unittest.TestCase):
    """Fixture prices: model-a 1/10/0.1 in pricing.json; LiteLLM lists its cache read at 0.05."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.pricing = Path(self.folder.name) / "pricing.json"
        shutil.copy(FIXTURES / "pricing.json", self.pricing)

    def tearDown(self):
        self.folder.cleanup()

    def check(self, *args, source=LITELLM):
        proc = subprocess.run(
            [sys.executable, str(HERE / "update_pricing.py"), "--source", source,
             "--pricing", str(self.pricing), *args],
            capture_output=True, text=True,
        )
        return proc

    def prices(self):
        return json.loads(self.pricing.read_text())

    def test_shows_changes_without_writing(self):
        before = self.pricing.read_text()
        out = self.check("model-a").stdout
        self.assertIn("model-a: cacheRead 0.1 → 0.05", out)
        self.assertEqual(self.pricing.read_text(), before)

    def test_shows_a_model_missing_from_the_file_as_new(self):
        out = self.check("model-b").stdout
        self.assertIn("model-b: new (input 3, output 15, cacheRead 0.3, cacheWrite5m 3.75, cacheWrite1h 6)", out)

    def test_apply_writes_the_changes_and_dates_them(self):
        self.check("model-a", "model-b", "--apply")
        prices = self.prices()
        self.assertEqual(prices["asOf"], date.today().isoformat())
        self.assertEqual(prices["models"]["model-a"],
                         {"input": 1, "output": 10, "cacheRead": 0.05, "cacheWrite5m": 1.25, "cacheWrite1h": 2})
        self.assertEqual(prices["models"]["model-b"]["cacheWrite1h"], 6)

    def test_leaves_a_model_litellm_does_not_list(self):
        out = self.check("model-z", "--apply").stdout
        self.assertIn("model-z: not in LiteLLM; prices unchanged", out)
        self.assertNotIn("model-z", self.prices()["models"])
        self.assertEqual(self.prices()["asOf"], "2026-01-01")

    def test_leaves_a_model_without_a_one_hour_cache_price(self):
        out = self.check("model-c", "--apply").stdout
        self.assertIn("model-c: LiteLLM has no 1-hour cache write price; prices unchanged", out)
        self.assertNotIn("model-c", self.prices()["models"])

    def test_changes_nothing_when_litellm_is_unavailable(self):
        before = self.pricing.read_text()
        proc = self.check("model-a", "--apply", source=str(FIXTURES / "missing.json"))
        self.assertEqual(proc.returncode, 3)
        self.assertIn("LiteLLM unavailable; prices unchanged", proc.stdout)
        self.assertEqual(self.pricing.read_text(), before)

    def test_apply_dates_a_check_that_found_no_changes(self):
        self.check("model-a", "--apply")
        prices = self.prices()
        prices["asOf"] = "2020-01-01"
        self.pricing.write_text(json.dumps(prices))
        self.check("model-a", "--apply")
        self.assertEqual(self.prices()["asOf"], date.today().isoformat())

    def test_reports_an_up_to_date_model(self):
        self.check("model-a", "--apply")
        self.assertIn("model-a: unchanged", self.check("model-a").stdout)


if __name__ == "__main__":
    unittest.main()
