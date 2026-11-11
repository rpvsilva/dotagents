#!/usr/bin/env bash
# Planted bug: reset reads counts[name] unguarded, so an unknown name crashes (KeyError) instead of failing with tally: no count.
exec python3 -I "$(dirname "$0")/_cites.py" tally/cli.py 41 44 KeyError\|crash\|traceback\|no\ count\|unknown\|missing\|does\ not\ exist\|absent
