#!/usr/bin/env python3
"""Check pricing.json against LiteLLM's price list for the given models; --apply writes the changes."""
import argparse
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
LITELLM = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
# pricing.json field -> LiteLLM field (LiteLLM prices are per token).
FIELDS = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "cacheRead": "cache_read_input_token_cost",
    "cacheWrite5m": "cache_creation_input_token_cost",
    "cacheWrite1h": "cache_creation_input_token_cost_above_1hr",
}


def fetch(source):
    try:
        if source.startswith("https://"):
            with urllib.request.urlopen(source, timeout=20) as response:
                return json.load(response)
        return json.loads(Path(source).read_text())
    except (OSError, ValueError):
        return None


def per_million(entry):
    """LiteLLM entry as pricing.json fields, or the name of the first field it lacks."""
    prices = {}
    for field, source_field in FIELDS.items():
        if source_field not in entry:
            return source_field
        prices[field] = round(entry[source_field] * 1_000_000, 6)
    return prices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+")
    parser.add_argument("--apply", action="store_true", help="write the changes to pricing.json")
    parser.add_argument("--pricing", default=HERE / "pricing.json")
    parser.add_argument("--source", default=LITELLM, help="LiteLLM price list URL or file")
    args = parser.parse_args()

    catalog = fetch(args.source)
    if catalog is None:
        print("LiteLLM unavailable; prices unchanged")
        sys.exit(3)

    pricing_path = Path(args.pricing)
    pricing = json.loads(pricing_path.read_text())
    updates = {}
    unchecked = []
    for model in args.models:
        if model not in catalog:
            print(f"{model}: not in LiteLLM; prices unchanged")
            unchecked.append(model)
            continue
        new = per_million(catalog[model])
        if isinstance(new, str):
            label = "1-hour cache write" if new == FIELDS["cacheWrite1h"] else new
            print(f"{model}: LiteLLM has no {label} price; prices unchanged")
            unchecked.append(model)
            continue
        old = pricing["models"].get(model)
        if old is None:
            print(f"{model}: new (" + ", ".join(f"{k} {v:g}" for k, v in new.items()) + ")")
        elif old == new:
            print(f"{model}: unchanged")
            continue
        else:
            changed = [f"{k} {old.get(k, 0):g} → {v:g}" for k, v in new.items() if old.get(k) != v]
            print(f"{model}: " + ", ".join(changed))
        updates[model] = new

    if not args.apply or len(unchecked) == len(args.models):
        return
    pricing["models"].update(updates)
    # asOf vouches for every price, so it moves only when every model was checked.
    if unchecked:
        print(f"asOf kept at {pricing.get('asOf')}: {', '.join(unchecked)} not checked")
    else:
        pricing["asOf"] = date.today().isoformat()
    pricing_path.write_text(json.dumps(pricing, indent=2) + "\n")
    print(f"Updated {pricing_path}")


if __name__ == "__main__":
    main()
