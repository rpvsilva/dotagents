#!/usr/bin/env bash
# Passes when an open question in spec.md raises the file format of `tally export`.
# _raised.py names the trap and the keywords it matches.
exec python3 -I "$(dirname "$0")/_raised.py" export-format
