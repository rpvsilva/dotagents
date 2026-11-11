#!/usr/bin/env bash
# Planted spec gap: remove of an unknown name exits 0 silently instead of tally: no count.
exec python3 -I "$(dirname "$0")/_cites.py" tally/cli.py 46 52 no\ count\|unknown\|missing\|absent\|silent\|nonexist\|does\ not\ exist
