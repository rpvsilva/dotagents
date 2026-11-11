#!/usr/bin/env bash
# Planted rule break: rename's no-count error goes to stdout, not stderr.
exec python3 -I "$(dirname "$0")/_cites.py" tally/cli.py 60 62 stdout\|stderr\|fail\\\(\\\)\|fail\ helper
