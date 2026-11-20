#!/usr/bin/env python3
"""Writes APM packages for every public Group into a folder outside the repo, for the APM study (docs/apm.md).

Usage: python3 -I tools/apm-study/gen.py --out <dir> [--group <g>]...

The Parts, their needs and the closures come only from scripts/parts.sh (the repo, or $PARTS_REPO). Under
<dir>/packages it writes one package per skill (named as the skill), per agent and tool (`<agent>-claude`,
`<agent>-codex`, `<agent>-cursor`), `dotagents-core` (the core skills and the _shared files, under the name
`dotagents-shared`) and one meta-package per Group (`dotagents-<group>`), each with an `apm.yml`. <dir>/unexpressed.txt
lists, one `<part> <reason>` per line, every part APM has no primitive for and every optional need a Group leaves out.
Nothing is written into the repo; an --out inside it is refused.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = {"claude": ".md", "codex": ".toml", "cursor": ".md"}
PRIVATE = "personal"
CORE = "core"
SHARED = "dotagents-shared"
CORE_PACKAGE = "dotagents-core"
REASONS = {
    "hook": "APM has no hook primitive: the script, its wiring and the hooks library are not installed",
    "mod": "APM has no Claude Code plugin primitive: the mod is not installed",
    "remote": "APM installs no commands on PATH: the remote-agent command and its scripts are not installed",
    "mcp": "APM has no way to fill ${ENV_VAR} credentials into MCP servers from mcp/servers.json: not installed",
    "global": "APM has no primitive for a tool's global instructions, settings or rules: not installed",
}


def die(message):
    print(f"gen: {message}", file=sys.stderr)
    sys.exit(2)


class Repo:
    def __init__(self):
        self.code = HERE.parent.parent
        self.root = Path(os.environ.get("PARTS_REPO") or self.code).resolve()
        self.script = self.code / "scripts" / "parts.sh"

    def parts(self, *args):
        done = subprocess.run(["bash", str(self.script), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env={**os.environ, "PARTS_REPO": str(self.root)})
        if done.returncode != 0:
            die(f"parts.sh {' '.join(args)} failed: {done.stderr.strip()}")
        return [line for line in done.stdout.splitlines() if line]

    def kinds(self):
        return {name: (kind, group) for name, kind, group, _ in (l.split("\t") for l in self.parts("list"))}

    def needs(self, part):
        return dict(l.split("\t") for l in self.parts("needs", part))

    def agents_of(self, skill):
        return sorted(p for p, (kind, _) in self.kinds().items()
                      if kind == "agent" and self.parts("owner", p) == [skill])


def yaml_str(text):
    # A JSON string is a YAML double-quoted scalar.
    return json.dumps(text, ensure_ascii=False)


def write_package(out, name, description, deps, files=()):
    """Writes packages/<name>/apm.yml and copies files, `(source Path, destination relative to the package)`."""
    folder = out / "packages" / name
    folder.mkdir(parents=True, exist_ok=True)
    for src, dst in files:
        target = folder / dst
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    lines = [f"name: {name}", 'version: "0.1.0"', f"description: {yaml_str(description)}"]
    if deps:
        lines.append("dependencies:")
        lines.append("  apm:")
        lines += [f"    - path: {yaml_str('../' + d)}" for d in deps]
    (folder / "apm.yml").write_text("\n".join(lines) + "\n")


def skill_files(repo, skill):
    """`(source, destination)` for every file the skill ships, under .apm/skills/<name>/."""
    base = Path(".apm/skills")
    for rel in repo.parts("files", skill):
        path = Path(rel)
        if path.parts[:2] == ("skills", skill):
            yield repo.root / rel, base / skill / Path(*path.parts[2:])
        else:
            yield repo.root / rel, base / skill / path


def shared_files(repo):
    base = Path(".apm/skills") / SHARED
    for rel in repo.parts("files", "_shared"):
        path = Path(rel)
        if path.parts[:2] == ("skills", "_shared"):
            yield repo.root / rel, base / Path(*path.parts[2:])
        else:
            yield repo.root / rel, base / path


def package_names(part, kind):
    return [f"{part}-{tool}" for tool in TOOLS] if kind == "agent" else [part]


def generate(repo, out, wanted):
    kinds = repo.kinds()
    about = dict(l.split("\t", 1) for l in repo.parts("groups"))
    groups = [g for g in about if g not in (CORE, PRIVATE)]
    for g in wanted:
        if g not in groups:
            die(f"not a public group: {g}")
    groups = wanted or groups
    shutil.rmtree(out / "packages", ignore_errors=True)
    core = set(repo.parts("closure", CORE))
    unexpressed = {}
    expressed = {}

    def express(part):
        """The packages of an expressible part; records the others in `unexpressed`."""
        kind = kinds[part][0]
        if kind not in ("skill", "agent"):
            unexpressed.setdefault(part, REASONS[kind])
            return []
        if kind == "agent":
            for tool, ext in TOOLS.items():
                if not (repo.root / "adapters" / tool / "agents" / (part + ext)).is_file():
                    unexpressed.setdefault(f"{part}-{tool}", f"no {tool} wrapper adapters/{tool}/agents/{part}{ext}")
        return package_names(part, kind)

    # Core: every core skill and the _shared files in one package.
    core_skills = [p for p in sorted(core) if p in kinds and kinds[p][0] == "skill"]
    for p in sorted(core):
        if p in kinds and kinds[p][0] != "skill":
            unexpressed.setdefault(p, REASONS[kinds[p][0]])
    files = list(shared_files(repo))
    for s in core_skills:
        files += list(skill_files(repo, s))
    write_package(out, CORE_PACKAGE, "dotagents core: the configure skill and the shared files", [], files)

    for group in groups:
        closure = [p for p in repo.parts("closure", group) if p not in core and p != "_shared"]
        deps = [CORE_PACKAGE]
        for part in closure:
            kind = kinds[part][0]
            packages = express(part)
            if not packages:
                continue
            for package in packages:
                tool = package.rsplit("-", 1)[1] if kind == "agent" else None
                if tool and (repo.root / "adapters" / tool / "agents" / (part + TOOLS[tool])).is_file():
                    src = repo.root / "adapters" / tool / "agents" / (part + TOOLS[tool])
                    write_package(out, package, f"{part} agent for {tool}", [], [(src, Path(".apm/agents") / src.name)])
                elif kind == "agent":
                    continue
                if kind == "skill":
                    needs = repo.needs(part)
                    sub = [d for n, how in sorted(needs.items()) if how == "required" and n not in core
                           for d in express(n)]
                    sub += [a for ag in repo.agents_of(part) for a in express(ag)]
                    sub = sorted(set(sub) - {package})
                    write_package(out, package, f"dotagents skill {part}", sub, list(skill_files(repo, part)))
                deps.append(package)
            # Optional needs the Group leaves out.
            if kind == "skill":
                for need, how in sorted(repo.needs(part).items()):
                    if how == "optional" and need not in closure and need not in core:
                        unexpressed.setdefault(f"{need}@{group}", f"left out of {group}: optional need of {part}")
        deps = [deps[0]] + sorted(d for d in set(deps[1:]) if (out / "packages" / d / "apm.yml").is_file())
        write_package(out, f"dotagents-{group}", about[group], deps)
        expressed[group] = deps

    lines = []
    for key, reason in sorted(unexpressed.items()):
        lines.append(f"{key.split('@')[0]} {reason}")
    (out / "unexpressed.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
    return expressed


def main():
    ap = argparse.ArgumentParser(description="Write APM packages for every public Group.")
    ap.add_argument("--out", required=True, help="folder to write into; outside the repo")
    ap.add_argument("--group", action="append", default=[], help="a public Group (default: all of them)")
    args = ap.parse_args()
    repo = Repo()
    out = Path(args.out).resolve()
    for root in {repo.code, repo.root}:
        if out == root or root in out.parents:
            die(f"--out {out} is inside the repo {root}")
    out.mkdir(parents=True, exist_ok=True)
    expressed = generate(repo, out, args.group)
    for group, deps in expressed.items():
        print(f"dotagents-{group}: {len(deps)} dependencies")
    print(f"unexpressed: {out / 'unexpressed.txt'}")


if __name__ == "__main__":
    main()
