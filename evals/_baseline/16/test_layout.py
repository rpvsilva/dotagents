"""The #16 acceptance folder in the shape `../402/quality.py --acceptance` reads: a feature for every accept_*.py."""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACCEPTANCE = HERE / "acceptance"
sys.path.insert(0, str(HERE.parent / "402"))

import quality  # noqa: E402


class Layout(unittest.TestCase):
    def test_every_accept_file_has_one_feature_per_issue(self):
        features = quality.load_features(ACCEPTANCE)
        files = sorted(p.name for p in ACCEPTANCE.glob("accept_*.py"))
        self.assertEqual(sorted(features), files)
        self.assertEqual(sorted(v.split("-")[0] for v in features.values()), ["17", "18", "19", "20", "21", "22"])
        self.assertEqual(json.loads((ACCEPTANCE / "features.json").read_text(encoding="utf-8")), features)

    def test_pytest_config_and_no_test_files(self):
        ini = (ACCEPTANCE / "pytest.ini").read_text(encoding="utf-8")
        self.assertIn("python_files = accept_*.py", ini)
        self.assertIn("strict:", ini)
        self.assertTrue((ACCEPTANCE / "conftest.py").is_file())
        self.assertEqual(list(ACCEPTANCE.glob("test_*.py")), [])  # python.test.sh must never run them


if __name__ == "__main__":
    unittest.main()
