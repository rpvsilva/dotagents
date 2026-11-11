"""The comparison report of two refs, as Markdown (`markdown()`) or one self-contained HTML page (`html()`).

Both take one `comparison` dict: `tool`, `model`, `runs` (per case), `pricing_as_of`, `base` and `head` (each
`{ref, sha}`, plus `model` when the two refs ran different models) and `verdict` (the output of `verdict.verdict()`).
The report holds a header naming those, one table row per case with both refs' numbers (median and min-max), the
difference and the verdict, one sub-row per rubric criterion, then the cases worth more runs. A missing value prints
`unknown`, never 0.
"""
import html as _html

import verdict
from verdict import METRICS

COLUMNS = ("case", "pass rate", "cost", "turns", "time", "rubric", "verdict")
UNKNOWN = "unknown"
PASS_RATE_ONLY = ("Under {MIN_RUNS} runs a side, only the pass rate decides; cost, turns, time and rubric are shown but "
                  "cannot make a case better or worse")


def _num(n):
    return f"{n:g}" if isinstance(n, int) or float(n).is_integer() else f"{n:.1f}"


def _money(n, sign=False):
    return f"{'-' if n < 0 else '+' if sign else ''}${abs(n):.2f}"


def _secs(n, sign=False):
    return f"{n:+.0f} s" if sign else f"{n:.0f} s"


def _count(n, sign=False):
    return ("+" if sign and n >= 0 else "") + _num(n)


FORMATS = {"cost_usd": _money, "turns": _count, "duration_s": _secs}


def _rate(r, sign=False):
    return f"{r * 100:+.0f}%" if sign else f"{r * 100:.0f}%"


def _spread(s, fmt):
    if s is None:
        return UNKNOWN
    if s["min"] == s["max"]:
        return fmt(s["median"])
    return f"{fmt(s['median'])} ({fmt(s['min'])}–{fmt(s['max'])})"


def _cell(base, head, diff, show, fmt):
    """`base → head (diff)`; `show` turns one side's value into text."""
    return f"{show(base)} → {show(head)} ({UNKNOWN if diff is None else fmt(diff, sign=True)})"


def _metric_cells(entry):
    """The pass rate and `METRICS` cells of one case of `verdict()["cases"]`."""
    m = entry["metrics"]
    if not m:  # a case one side lacks: its summaries, no difference
        base, head = entry.get("base") or {}, entry.get("head") or {}
        m = {"pass_rate": {"base": base.get("pass_rate"), "head": head.get("pass_rate"), "diff": None},
             **{k: {"base": base.get(k), "head": head.get(k), "diff": None} for k in METRICS}}
    pr = m["pass_rate"]
    cells = [_cell(pr["base"], pr["head"], pr["diff"], lambda r: UNKNOWN if r is None else _rate(r), _rate)]
    for k in METRICS:
        fmt = FORMATS[k]
        cells.append(_cell(m[k]["base"], m[k]["head"], m[k]["diff"], lambda s, f=fmt: _spread(s, f), fmt))
    return cells


def _rows(v):
    rows = []
    for case, entry in v["cases"].items():
        rows.append([case] + _metric_cells(entry) + ["", entry["verdict"]])
        for criterion, r in (entry["metrics"].get("rubric") or {}).items():
            rubric = _cell(r["base"], r["head"], r["diff"], lambda s: _spread(s, _num), _count)
            rows.append([f"{case} · {criterion}", "", "", "", "", rubric, r["verdict"]])
    return rows


def _text(value):
    return UNKNOWN if value is None else str(value)


def _side(c, side):
    s = c.get(side) or {}
    out = f"{_text(s.get('ref'))} ({_text(s.get('sha'))})"
    return out + (f", model {s['model']}" if s.get("model") else "")


def _header(c):
    return [("Tool", _text(c.get("tool"))), ("Model", _text(c.get("model"))),
            ("Base", _side(c, "base")), ("Head", _side(c, "head")),
            ("Runs per case", _text(c.get("runs"))), ("Prices as of", _text(c.get("pricing_as_of")))]


def _md_cell(text):
    return str(text).replace("|", "\\|").replace("\r\n", "<br>").replace("\r", "<br>").replace("\n", "<br>")


def markdown(comparison):
    """The report as Markdown."""
    v = comparison["verdict"]
    lines = ["# Eval comparison", ""]
    lines += [f"- {k}: {_md_cell(val)}" for k, val in _header(comparison)]
    lines += ["", "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    lines += ["| " + " | ".join(_md_cell(c) for c in row) + " |" for row in _rows(v)]
    lines += ["", PASS_RATE_ONLY.format(MIN_RUNS=verdict.MIN_RUNS) + ": " + ", ".join(map(_md_cell, v["pass_rate_only"]))
              ] if v.get("pass_rate_only") else []
    lines += ["", "## Worth more runs", ""]
    lines += [f"- {_md_cell(c)}" for c in v["more_runs"]] or ["No case."]
    return "\n".join(lines) + "\n"


def _h(text):
    return _html.escape(str(text)).replace("\r\n", "<br>").replace("\r", "<br>").replace("\n", "<br>")


STYLE = ("body{font-family:sans-serif;margin:2em}table{border-collapse:collapse}"
         "th,td{border:1px solid #ccc;padding:.3em .6em;text-align:left;vertical-align:top}")


def html(comparison):
    """The report as one self-contained HTML page: the same content as `markdown()`, HTML-escaped."""
    v = comparison["verdict"]
    out = ["<!DOCTYPE html>", '<html><head><meta charset="utf-8"><title>Eval comparison</title>',
           f"<style>{STYLE}</style></head><body>", "<h1>Eval comparison</h1>", "<ul>"]
    out += [f"<li>{_h(k)}: {_h(val)}</li>" for k, val in _header(comparison)]
    out += ["</ul>", "<table>", "<tr>" + "".join(f"<th>{_h(c)}</th>" for c in COLUMNS) + "</tr>"]
    out += ["<tr>" + "".join(f"<td>{_h(c)}</td>" for c in row) + "</tr>" for row in _rows(v)]
    out += ["</table>"]
    if v.get("pass_rate_only"):
        out += [f"<p>{_h(PASS_RATE_ONLY.format(MIN_RUNS=verdict.MIN_RUNS))}: {_h(', '.join(v['pass_rate_only']))}</p>"]
    out += ["<h2>Worth more runs</h2>"]
    more = v["more_runs"]
    out += ["<ul>"] + [f"<li>{_h(c)}</li>" for c in more] + ["</ul>"] if more else ["<p>No case.</p>"]
    out += ["</body></html>"]
    return "\n".join(out) + "\n"
