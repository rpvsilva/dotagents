"""Runs gen.py on a fixture repo (a tempdir copy of fixtures/, through PARTS_REPO) and checks what it writes."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEN = HERE / "gen.py"
PARTS = HERE.parent.parent / "scripts" / "parts.sh"
TOOLS = (("claude", ".md"), ("codex", ".toml"), ("cursor", ".md"))


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def deps_of(yml):
    """The `path:` dependencies of an apm.yml, as written."""
    return [l.split('"')[1] for l in Path(yml).read_text().splitlines() if l.strip().startswith("- path:")]


def parse_yaml(path):
    """Strict YAML through ruby's Psych, as the repo's frontmatter check does; None without ruby."""
    if not shutil.which("ruby"):
        return None
    done = subprocess.run(["ruby", "-ryaml", "-rjson", "-e", "puts JSON.generate(YAML.safe_load(File.read(ARGV[0])))",
                           str(path)], capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(f"{path}: not valid YAML: {done.stderr}")
    return json.loads(done.stdout)


class GenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.repo = cls.tmp / "repo"
        shutil.copytree(HERE / "fixtures", cls.repo)
        cls.out = cls.tmp / "out"
        cls.before = tree(cls.repo)
        done = cls.run_gen(cls.out)
        assert done.returncode == 0, done.stderr

    @classmethod
    def run_gen(cls, out, *args):
        env = {**os.environ, "PARTS_REPO": str(cls.repo)}
        return subprocess.run([sys.executable, "-I", str(GEN), "--out", str(out), *args], env=env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def closure(self, group):
        done = subprocess.run(["bash", str(PARTS), "closure", group], capture_output=True, text=True,
                              env={**os.environ, "PARTS_REPO": str(self.repo)})
        self.assertEqual(done.returncode, 0, done.stderr)
        return set(done.stdout.split())

    def reach(self, out, package):
        """The names of every package the given one depends on, transitively."""
        seen, todo = set(), [package]
        while todo:
            for dep in deps_of(out / "packages" / todo.pop() / "apm.yml"):
                name = dep.rsplit("/", 1)[1]
                if name not in seen:
                    seen.add(name)
                    todo.append(name)
        return seen

    def unexpressed(self):
        return [tuple(l.split(" ", 1)) for l in (self.out / "unexpressed.txt").read_text().splitlines()]

    def part_of(self, package):
        for tool, _ in TOOLS:
            if package.endswith("-" + tool):
                return package[: -len(tool) - 1]
        return package

    def test_one_meta_package_per_public_group_and_none_for_personal(self):
        names = sorted(p.name for p in (self.out / "packages").glob("dotagents-*"))
        self.assertEqual(names, ["dotagents-alpha", "dotagents-beta", "dotagents-core"])

    def test_meta_package_closure_equals_parts_closure_minus_core(self):
        core = self.closure("core")
        listed = {part for part, reason in self.unexpressed() if not reason.startswith("left out")}
        for group in ("alpha", "beta"):
            closure = self.closure(group) - core
            reached = {self.part_of(p) for p in self.reach(self.out, f"dotagents-{group}")} - {"dotagents-core"}
            self.assertTrue(reached <= closure, group)
            self.assertEqual(reached | (closure & listed), closure, group)
            self.assertEqual(reached & listed, set(), group)

    def test_closure_follows_required_needs_and_leaves_optional_ones(self):
        self.assertIn("two", self.reach(self.out, "one"))
        self.assertNotIn("three", self.reach(self.out, "dotagents-alpha"))
        self.assertIn("three", self.reach(self.out, "dotagents-beta"))

    def test_core_ships_configure_and_shared_files_under_a_name_without_underscore(self):
        core = self.out / "packages" / "dotagents-core"
        self.assertTrue((core / ".apm/skills/configure/SKILL.md").is_file())
        self.assertTrue((core / ".apm/skills/dotagents-shared/conventions.md").is_file())
        self.assertTrue((core / ".apm/skills/dotagents-shared/scripts/settings.sh").is_file())
        self.assertEqual([p for p in (self.out / "packages").rglob("*") if p.name.startswith("_")], [])

    def test_each_agent_has_a_package_per_tool_with_its_own_wrapper(self):
        for tool, ext in TOOLS:
            wrapper = self.out / "packages" / f"one-agent-{tool}" / ".apm/agents" / f"one-agent{ext}"
            source = self.repo / "adapters" / tool / "agents" / f"one-agent{ext}"
            self.assertEqual(wrapper.read_bytes(), source.read_bytes())
            self.assertIn(f"one-agent-{tool}", self.reach(self.out, "dotagents-alpha"))

    def test_hooks_mod_remote_and_mcp_are_listed_with_a_reason(self):
        listed = dict(self.unexpressed())
        for part in ("guard", "flow", "remote", "mcp"):
            self.assertTrue(listed.get(part), f"{part} is not in unexpressed.txt")
        self.assertEqual({"guard", "flow", "remote", "mcp"} & {self.part_of(p) for p in self.reach(self.out, "dotagents-beta")}, set())

    def test_optional_needs_a_group_leaves_out_are_listed(self):
        self.assertIn(("three", "left out of alpha: optional need of one"), self.unexpressed())

    def test_every_apm_yml_parses_and_every_path_dependency_exists(self):
        ymls = sorted((self.out / "packages").glob("*/apm.yml"))
        self.assertTrue(ymls)
        for yml in ymls:
            data = parse_yaml(yml)
            if data is not None:
                self.assertEqual(data["name"], yml.parent.name)
                self.assertEqual([d["path"] for d in data.get("dependencies", {}).get("apm", [])], deps_of(yml))
            for dep in deps_of(yml):
                self.assertTrue((yml.parent / dep / "apm.yml").is_file(), f"{yml}: {dep}")

    def test_the_source_tree_is_unchanged(self):
        self.assertEqual(tree(self.repo), self.before)
        self.assertEqual(list(self.repo.rglob("apm.yml")), [])

    def test_group_flag_limits_the_groups_written(self):
        out = self.tmp / "only-beta"
        done = self.run_gen(out, "--group", "beta")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((out / "packages" / "dotagents-beta" / "apm.yml").is_file())
        self.assertFalse((out / "packages" / "dotagents-alpha").exists())

    def test_a_group_that_is_not_public_is_refused(self):
        for group in ("personal", "nope"):
            done = self.run_gen(self.tmp / "refused", "--group", group)
            self.assertEqual(done.returncode, 2, group)
            self.assertIn("not a public group", done.stderr)

    def test_an_out_folder_inside_the_repo_is_refused(self):
        done = self.run_gen(self.repo / "out")
        self.assertEqual(done.returncode, 2)
        self.assertFalse((self.repo / "out").exists())

    def test_two_runs_write_the_same_files(self):
        out = self.tmp / "again"
        self.assertEqual(self.run_gen(out).returncode, 0)
        self.assertEqual(tree(out), tree(self.out))


if __name__ == "__main__":
    unittest.main()
