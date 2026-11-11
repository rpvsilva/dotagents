#!/usr/bin/env bash
# Passes when an open question in spec.md raises how `tally add` meets a cap (the epic's two rules conflict).
# _raised.py names the trap and the keywords it matches.
exec python3 -I "$(dirname "$0")/_raised.py" cap-vs-add
