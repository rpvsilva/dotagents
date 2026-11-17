"""Puts the checkout under test (ACCEPT_CHECKOUT) first on `sys.path`, so `tally` is imported from it."""
import os
import sys
from pathlib import Path

CHECKOUT = Path(os.environ["ACCEPT_CHECKOUT"]).resolve()


def pytest_configure(config):
    sys.path.insert(0, str(CHECKOUT))
