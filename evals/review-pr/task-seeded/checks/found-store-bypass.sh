#!/usr/bin/env bash
# Planted rule break: reset writes the store itself, not through store.py (and not atomically).
exec python3 -I "$(dirname "$0")/_cites.py" tally/cli.py 47 49 store\\.py\|store\\.save\|bypass\|directly\|atomic\|rule\|through
