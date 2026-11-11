#!/usr/bin/env bash
# Planted bug: rename onto an existing name overwrites its count (data loss) instead of failing with count exists.
exec python3 -I "$(dirname "$0")/_cites.py" tally/cli.py 55 65 overwr\|exists\|existing\|clobber\|merg\|data\ loss\|lost\|replac
