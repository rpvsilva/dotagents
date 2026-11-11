#!/usr/bin/env bash
# Planted hollow test: assertNotEqual(stdout, "tea 1") passes with the name rule reverted.
exec python3 -I "$(dirname "$0")/_cites.py" tests/cli_test.py 38 40 hollow\|revert\|weak\|assertNotEqual\|still\ pass\|would\ pass\|regardless\|vacuous\|always\ pass
