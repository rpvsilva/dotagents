#!/usr/bin/env bash
# Planted hollow test: assertIn("tea", list) passes with reset reverted.
exec python3 -I "$(dirname "$0")/_cites.py" tests/cli_test.py 38 41 hollow\|revert\|weak\|assertIn\|still\ pass\|would\ pass\|substring\|regardless\|not.\*\(check\|assert\).\*0
