"""The agent protocol: one JSON schema per role (schemas/<role>.json, its `input` and its `output`), one validator.

Agents, scripts and orchestrators exchange data only. A prompt is one line naming the agent and its brief, then the
role's input object as JSON; an agent writes its output object as JSON to the `output` path its prompt names and ends
with the message `{"status": ..., "output": <path>}`; with no `output` named (a remote run), its final message is the
output object itself. check.py validates all three; render.py turns an output into what a human reads.

The validator covers the JSON Schema keywords the schemas use, and no more: type, enum, const, required, properties,
additionalProperties (false), items, minItems, minLength, maxLength, minimum, pattern, oneOf, if/then/else and
$ref (`#/$defs/x` in the same file, `common.json#/$defs/x` across files).
"""
import json
import re
from pathlib import Path

SCHEMAS = Path(__file__).resolve().parent / "schemas"
MESSAGE_MAX = 400
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def roles():
    return sorted(p.stem for p in SCHEMAS.glob("*.json") if p.stem != "common")


def schema(role):
    """The role's schema file as a dict; KeyError for an unknown role."""
    path = SCHEMAS / f"{role}.json"
    if role == "common" or not path.is_file():
        raise KeyError(role)
    return json.loads(path.read_text(encoding="utf-8"))


def _load(name, cache={}):
    if name not in cache:
        cache[name] = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    return cache[name]


def _type_ok(kind, value):
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, TYPES[kind])


def validate(node, value, doc, where="$"):
    """Every problem with `value` against schema `node` (in file `doc`), as `<json path>: <problem>` strings."""
    if "$ref" in node:
        target, _, pointer = node["$ref"].partition("#")
        doc = target or doc
        sub = _load(doc)
        for part in pointer.strip("/").split("/"):
            sub = sub[part]
        return validate(sub, value, doc, where)
    problems = []
    kinds = node.get("type")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if not any(_type_ok(k, value) for k in kinds):
            return [f"{where}: not {'|'.join(kinds)}"]
    if "const" in node and value != node["const"]:
        problems.append(f"{where}: not {json.dumps(node['const'])}")
    if "enum" in node and value not in node["enum"]:
        problems.append(f"{where}: not one of {'|'.join(map(str, node['enum']))}")
    if isinstance(value, str):
        if len(value) < node.get("minLength", 0):
            problems.append(f"{where}: empty" if not value else f"{where}: too short")
        if "maxLength" in node and len(value) > node["maxLength"]:
            problems.append(f"{where}: over {node['maxLength']} characters")
        if "pattern" in node and not re.search(node["pattern"], value):
            problems.append(f"{where}: does not match {node['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in node and value < node["minimum"]:
        problems.append(f"{where}: under {node['minimum']}")
    if isinstance(value, list):
        if len(value) < node.get("minItems", 0):
            problems.append(f"{where}: fewer than {node['minItems']} items")
        if "items" in node:
            for i, item in enumerate(value):
                problems += validate(node["items"], item, doc, f"{where}[{i}]")
    if isinstance(value, dict):
        for key in node.get("required", []):
            if key not in value:
                problems.append(f"{where}: missing {key}")
        props = node.get("properties", {})
        for key, item in value.items():
            if key in props:
                problems += validate(props[key], item, doc, f"{where}.{key}")
            elif node.get("additionalProperties") is False:
                problems.append(f"{where}: unknown key {key}")
    if "oneOf" in node:
        if sum(not validate(alt, value, doc, where) for alt in node["oneOf"]) != 1:
            problems.append(f"{where}: matches no single allowed form")
    if "if" in node:
        branch = "then" if not validate(node["if"], value, doc, where) else "else"
        if branch in node:
            problems += validate(node[branch], value, doc, where)
    return problems


def check(role, part, value):
    """Problems with `value` as the role's `input` or `output`."""
    doc = schema(role)
    return validate(doc[part], value, f"{role}.json")


def parse_json(text):
    """The JSON object in `text`; ValueError when there is none.

    The object may sit in one code fence, or after a line of prose (a remote agent's final message often opens with
    one): the first `{` that starts an object running to the end of the text, a closing fence aside, is taken.
    """
    body = text.strip()
    lines = body.splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        body = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(body)
    except ValueError:
        value = _trailing_object(body)
    if not isinstance(value, dict):
        raise ValueError("not a JSON object")
    return value


def _trailing_object(text):
    """The object starting at the first `{` whose JSON runs to the end of `text`, an optional closing fence aside."""
    decoder = json.JSONDecoder()
    for start in (i for i, c in enumerate(text) if c == "{"):
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            continue
        if text[end:].strip() in ("", "```"):
            return value
    raise ValueError("no JSON object")


def prompt(role, inputs):
    """The prompt for `role` with `inputs`: the agent and brief line, then the inputs as JSON. ValueError on a bad input."""
    problems = check(role, "input", inputs)
    if problems:
        raise ValueError("; ".join(problems))
    doc = schema(role)
    head = f"You are an {doc['agent']}." if doc["agent"][0] in "aeiou" else f"You are a {doc['agent']}."
    return f"{head} Follow ~/.agents/skills/{doc['brief']}.\n{json.dumps(inputs, indent=1, ensure_ascii=False)}\n"


def prompt_inputs(text):
    """The input object of a prompt `prompt()` wrote: everything after its first line."""
    _, _, rest = text.partition("\n")
    return parse_json(rest)
