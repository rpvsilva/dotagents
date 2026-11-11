#!/usr/bin/env bash
# Planted cross-task bug: rename never checks the new name against the T-01 name rule.
exec python3 -I "$(dirname "$0")/_cites.py" tally/cli.py 55 65 valid\|bad\ name\|name\ rule\|regex\|lowercase\|T-01\|criterion\ 1
