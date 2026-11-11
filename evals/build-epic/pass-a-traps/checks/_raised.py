"""Shared by this case's checks, not a check itself (not executable).

  python3 -I _raised.py <trap>

Exits 0 when one item of the `Open questions` section of `$EVAL_SCRATCH/spec.md` raises <trap>: the item's text
(its first line and every line up to the next item or heading), lowercased, matches both the trap's TOPIC and its
DETAIL pattern. An item is a numbered (`1.`, `1)`) or bulleted (`-`, `*`) line.

The epic (input/epic.md) hides three ambiguities a careful planner raises rather than settles:

- cap-vs-add: the epic says a count never goes above its cap, and that `tally add` always adds n. What does
  `add` do when n would pass the cap (refuse, clamp), and what of a cap set below the current count?
  TOPIC `cap`; DETAIL `add`, `exceed`, `above`, `over`, `beyond`, `past`, `clamp`, `refus`, `reject`, `truncat`,
  `below the current`, `already`.
- recent-order: `list --recent` orders "by when they last changed": newest or oldest first, how ties break, and
  where counts stand that have no recorded change time (every count in a store from before the feature).
  TOPIC `recent` or `last chang`; DETAIL `newest`, `oldest`, `latest`, `earliest`, `order`, `first`, `direction`,
  `ascending`, `descending`, `tie`, `never`, `no recorded`, `no time`, `no stamp`, `without a`, `existing`,
  `legacy`, `old store`, `before`, `missing`, `unknown`.
- export-format: "a form a spreadsheet or another tool can read" leaves the file format open.
  TOPIC `export`; DETAIL `format`, `csv`, `json`, `tsv`, `column`, `header`, `delimit`, `quot`, `extension`.
"""
import os
import re
import sys
from pathlib import Path

TRAPS = {
    "cap-vs-add": (r"\bcap", r"\badd|exceed|above|\bover\b|beyond|\bpast\b|clamp|refus|reject|truncat"
                                r"|below the current|already"),
    "recent-order": (r"recent|last chang", r"newest|oldest|latest|earliest|order|\bfirst\b|direction|ascending"
                                           r"|descending|\btie|never|no recorded|no time|no stamp|without a"
                                           r"|existing|legacy|old store|before|missing|unknown"),
    "export-format": (r"export", r"format|csv|json|tsv|column|header|delimit|quot|extension"),
}
HEADING = re.compile(r"^#+\s")
OPEN = re.compile(r"^#+\s+open questions\s*$", re.IGNORECASE)
ITEM = re.compile(r"^\s*(\d+[.)]|[-*])\s+")

topic, detail = (re.compile(p) for p in TRAPS[sys.argv[1]])
spec = Path(os.environ["EVAL_SCRATCH"]) / "spec.md"
if not spec.is_file():
    sys.exit(1)
items, inside = [], False
for line in spec.read_text(errors="replace").splitlines():
    if HEADING.match(line):
        inside = bool(OPEN.match(line))
        continue
    if not inside:
        continue
    if ITEM.match(line) or not items:
        items.append(line)
    else:
        items[-1] += "\n" + line
sys.exit(0 if any(topic.search(i.lower()) and detail.search(i.lower()) for i in items) else 1)
