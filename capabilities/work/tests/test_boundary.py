#!/usr/bin/env python3
"""The public boundary, enforced by the suite rather than claimed in an evidence file.

The engine ships to repositories that share none of this portfolio's identities. Three things must
never travel with it, and each has a test here:

1. **A program namespace in engine source.** A task id is `<program>/<NAME>`; naming a real program,
   or branching on one (`task.get("program") == "…"`, `re.match(r"…/W0[0-9]", id)`), binds the
   engine to one portfolio. A branch on the prefix alone counts too: `id.startswith("…/")`,
   `re.match(r"^…/", id)` and `id.split("/")[0] == "…"` name a program without spelling a whole task
   id. Engine source uses `<program>` placeholders, and any literal that looks like a task id fails.
2. **An absolute home path or a personal email in a tracked file.** Both name a person's machine or
   mailbox. Use `~`, `$HOME` or a reserved example domain instead.
3. **The names that the operator guards.** Those live in a denylist *outside* this repository; the
   sweep reads it from `LOOM_BOUNDARY_DENYLIST` and never copies it in. When the file is absent the
   check is skipped with a pointer, so a public checkout still runs its suite.

The detectors are exercised on synthetic source below, so a refactor cannot quietly turn them into
no-ops.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
REPO = WORK.parents[1]

# A task id literal: a plausible program namespace, then a task name that starts uppercase and
# carries a number (`W-1`, `W11-A`, `LOOM-4`). The shape deliberately excludes agent identities
# (`opencode/loom-worker`, `a2a/agent-1`) and mime types (`text/plain`), which are lowercase after
# the slash or carry no number.
TASK_ID_LITERAL = re.compile(
    r"(?<![A-Za-z0-9_/])([a-z][a-z0-9.-]{0,62})/"
    r"([A-Z][A-Za-z0-9._-]*\d[A-Za-z0-9._-]*)"
)

# Expressions that *are* a program namespace. Compared against a string literal, they are a branch
# on a specific portfolio.
NAMESPACE_NAMES = {
    "program", "namespace", "program_id", "programid", "context_id", "contextid",
}

# The namespace half of a task id. A prefix branch tests a task id against one of these, whether or
# not the literal carries the rest of an id: `startswith("widget/")`, `re.match(r"^widget/", id)`.
NAMESPACE_PREFIX = re.compile(r"(?<![A-Za-z0-9_/])[a-z][a-z0-9.-]{0,62}/")
# A namespace with no separator: the half you get from `id.split("/")[0]`. Task names start
# uppercase, so a bare lowercase literal compared against a task id names a program.
NAMESPACE_WORD = re.compile(r"[a-z][a-z0-9._-]{0,62}")

# Keys and expressions that hold a task id. Their prefix is the program namespace. Any field named
# `id` counts: a literal compared against an id is a branch on one portfolio's data, which is the
# binding this test forbids, whether the id names a task, a board or a program.
TASK_ID_KEYS = {"id", "task_id", "taskid", "identifier", "task"}
TASK_ID_NAMES = {"task_id", "taskid", "identifier"}

# Methods that take a literal and compare it against (a piece of) the string they are called on.
STRING_METHODS = {"startswith", "endswith", "split", "partition", "removeprefix",
                  "removesuffix", "rsplit", "rpartition"}

# Methods that extract the namespace from a task id.
SPLIT_METHODS = {"split", "rsplit", "partition", "rpartition"}

# Options a public engine must never default to a named program or task.
NAMESPACE_OPTIONS = {"--program", "--namespace", "--focus"}

# Reserved domains (RFC 2606 / RFC 6761); a mailbox here cannot reach a person.
PLACEHOLDER_DOMAINS = ("example.com", "example.net", "example.org", "example",
                       "invalid", "test", "localhost")

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
POSIX_HOME = re.compile(r"/(?:" + "|".join(("Users", "home", "root")) + r")/[A-Za-z0-9._-]+")
WINDOWS_HOME = re.compile(
    r"[A-Za-z]:[\\/]{1,2}(?:" + "|".join(("Users", "home", "root"))
    + r")[\\/]{1,2}[A-Za-z0-9._-]+"
)

DENYLIST_ENV = "LOOM_BOUNDARY_DENYLIST"


class Hit:
    """One finding: where it was, and the text that matched."""

    def __init__(self, path: Path, line: int, token: str):
        self.path = path
        self.line = line
        self.token = token

    def __repr__(self) -> str:  # a readable failure message
        return f"{self.path}:{self.line}: {self.token!r}"


# --------------------------------------------------------------------------
# What gets scanned
# --------------------------------------------------------------------------


def tracked_files(root: Path = REPO) -> list[Path]:
    """Every file git tracks, or a tree walk when the checkout is not a repository.

    The engine also runs as a release tree copied out of git, so the fallback keeps the suite
    usable there.
    """
    result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                            capture_output=True, text=True)
    if result.returncode == 0:
        return [root / name for name in result.stdout.split("\0") if name]
    skip = {".git", "__pycache__", ".venv", "venv", "node_modules"}
    return sorted(path for path in root.rglob("*")
                  if path.is_file() and not skip & set(path.parts))


def engine_sources() -> list[Path]:
    """The engine's own Python: everything under capabilities/work except the tests.

    Test fixtures may name example programs; shipped modules may not.
    """
    paths = [path for path in (WORK.rglob("*.py"))
             if "tests" not in path.relative_to(WORK).parts
             and "__pycache__" not in path.parts]
    script = WORK / "loom"
    if script.is_file():
        paths.append(script)
    return sorted(paths)


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


# --------------------------------------------------------------------------
# Detectors (pure functions, so the tests below can prove they still bite)
# --------------------------------------------------------------------------


def namespaced_literals(tree: ast.AST, source: str = "") -> list[tuple[int, str]]:
    """String literals in source that name a program and a task id."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for match in TASK_ID_LITERAL.finditer(node.value):
                found.append((node.lineno, match.group(0)))
    return found


def is_namespace_target(node: ast.AST) -> bool:
    """True when the expression *is* the program namespace, not a dict lookup that merely holds it."""
    if isinstance(node, ast.Name):
        return node.id.lower() in NAMESPACE_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr.lower() in NAMESPACE_NAMES
    if isinstance(node, ast.Subscript):
        key = node.slice
        return (isinstance(key, ast.Constant) and isinstance(key.value, str)
                and key.value.lower() in NAMESPACE_NAMES)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        first = node.args[0] if node.args else None
        return (isinstance(first, ast.Constant) and isinstance(first.value, str)
                and first.value.lower() in NAMESPACE_NAMES)
    return False


def _holds_task_id(node: ast.AST) -> bool:
    """True when the expression is a task id: `task["id"]`, `task.get("id")`, `task.id`."""
    if isinstance(node, ast.Name):
        return node.id.lower() in TASK_ID_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr.lower() in TASK_ID_KEYS
    if isinstance(node, ast.Subscript):
        key = node.slice
        return (isinstance(key, ast.Constant) and isinstance(key.value, str)
                and key.value.lower() in TASK_ID_KEYS)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        first = node.args[0] if node.args else None
        return (isinstance(first, ast.Constant) and isinstance(first.value, str)
                and first.value.lower() in TASK_ID_KEYS)
    return False


def is_namespace_of_task_id(node: ast.AST) -> bool:
    """True for `<task id>.split("/")[0]` and friends: the namespace, derived from an id."""
    if not isinstance(node, ast.Subscript):
        return False
    call = node.value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr in SPLIT_METHODS and _holds_task_id(call.func.value)):
        return False
    separator = call.args[0] if call.args else None
    if separator is not None and not (isinstance(separator, ast.Constant)
                                      and separator.value in {"/", "\\"}):
        return False
    return isinstance(node.slice, ast.Constant) and node.slice.value == 0


def names_a_namespace(value: str) -> bool:
    """True when a literal names a program namespace: `widget/`, `^widget/`, `widget`."""
    return bool(NAMESPACE_PREFIX.search(value)) or bool(NAMESPACE_WORD.fullmatch(value))


def _string_constants(node: ast.AST) -> list[ast.Constant]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node]
    if isinstance(node, (ast.Tuple, ast.Set, ast.List)):
        return [item for item in node.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)]
    return []


def namespace_branches(tree: ast.AST, source: str = "") -> list[tuple[int, str]]:
    """Places that branch on a literal namespace rather than on the registry's own data."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for index, operand in enumerate(operands):
                constants = _string_constants(operand)
                if not constants:
                    continue
                others = [other for position, other in enumerate(operands) if position != index]
                if any(is_namespace_target(other) or is_namespace_of_task_id(other)
                       for other in others):
                    found.append((node.lineno, ast.unparse(node)))
                    break
                if any(_holds_task_id(other) and names_a_namespace(constant.value)
                       for other in others for constant in constants):
                    found.append((node.lineno, ast.unparse(node)))
                    break
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        method = node.func.attr
        target = node.func.value
        if method in STRING_METHODS:
            values = [argument.value for argument in node.args
                      if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                      and argument.value]
            if is_namespace_target(target) and values:
                found.append((node.lineno, ast.unparse(node)))
            elif (any(_holds_task_id(part) for part in ast.walk(target))
                  and any(names_a_namespace(value) for value in values)):
                found.append((node.lineno, ast.unparse(node)))
        if (method in {"match", "search", "fullmatch", "findall", "sub"}
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "re"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and (TASK_ID_LITERAL.search(node.args[0].value)
                     or (names_a_namespace(node.args[0].value)
                         and any(_holds_task_id(argument) for argument in node.args[1:])))):
            found.append((node.lineno, ast.unparse(node)))
    return found


def namespace_defaults(tree: ast.AST, source: str = "") -> list[tuple[int, str]]:
    """Options that default to a literal program or task instead of requiring one."""
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument" and node.args):
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value in NAMESPACE_OPTIONS):
            continue
        for keyword in node.keywords:
            if (keyword.arg == "default" and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)):
                found.append((node.lineno, ast.unparse(node)))
    return found


def home_paths(text: str) -> list[str]:
    return [match.group(0) for match in POSIX_HOME.finditer(text)] + \
           [match.group(0) for match in WINDOWS_HOME.finditer(text)]


def is_placeholder_address(address: str) -> bool:
    domain = address.rsplit("@", 1)[-1].lower()
    return any(domain == name or domain.endswith("." + name) for name in PLACEHOLDER_DOMAINS)


def personal_emails(text: str) -> list[str]:
    return [match.group(0) for match in EMAIL.finditer(text)
            if not is_placeholder_address(match.group(0))]


def parse_denylist(text: str) -> list[str]:
    entries = []
    for line in text.splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            entries.append(entry)
    return entries


def denylist_hits(entries: list[str], paths: list[Path]) -> list[Hit]:
    """Literal, case-insensitive, whole-token matches over the given files."""
    patterns = [(entry, re.compile(r"(?<![A-Za-z0-9_])" + re.escape(entry) + r"(?![A-Za-z0-9_])",
                                   re.IGNORECASE))
                for entry in entries]
    hits = []
    for path in paths:
        text = read_text(path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for entry, pattern in patterns:
                if pattern.search(line):
                    hits.append(Hit(path, number, entry))
    return hits


def denylist_path() -> Path | None:
    value = os.environ.get(DENYLIST_ENV)
    return Path(value).expanduser() if value else None


def load_denylist(path: Path) -> list[str]:
    return parse_denylist(path.read_text(encoding="utf-8"))


def _scan_sources(finder) -> list[Hit]:
    hits = []
    for path in engine_sources():
        text = read_text(path)
        if text is None:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as error:  # a broken module is its own test failure
            raise AssertionError(f"cannot parse {path}: {error}") from error
        for line, token in finder(tree, text):
            hits.append(Hit(path.relative_to(REPO), line, token))
    return hits


# --------------------------------------------------------------------------
# 1. Engine source names no program
# --------------------------------------------------------------------------


class SourceDetectorTest(unittest.TestCase):
    """The three source detectors are exercised on synthetic source.

    Without this, a detector that has stopped biting looks exactly like a clean module: the
    real-source tests pass because no literal names a program. Review of `eb3deed` replaced
    `TASK_ID_LITERAL` with a never-matching pattern and planted a real task id in `engine.py`; the
    suite stayed green. The fixtures below are assembled from parts because this file is itself
    tracked and swept for the operator's guarded names.
    """

    NAMESPACE = "acme" + "widget"

    def scan(self, detector, source: str) -> list[tuple[int, str]]:
        return detector(ast.parse(source), source)

    def test_namespaced_literals_finds_a_task_id(self):
        task_id = self.NAMESPACE + "/" + "W-1"
        hits = self.scan(namespaced_literals, f"LIMIT = {task_id!r}\n")
        self.assertEqual([token for _, token in hits], [task_id])

    def test_namespaced_literals_ignores_placeholders_and_identities(self):
        source = (
            'A = "<program>/<NAME>"\n'
            'B = "<program>/W-1"\n'
            'C = "opencode/loom-worker"\n'
            'D = "text/plain"\n'
            'E = "mcp/server.py"\n'
        )
        self.assertEqual(self.scan(namespaced_literals, source), [])

    def test_namespace_branches_finds_every_shape_of_prefix_branch(self):
        namespace = self.NAMESPACE
        cases = {
            "dict lookup compared": f'if task.get("program") == {namespace!r}: pass\n',
            "regex on a task id":
                f'import re\nif re.match(r"^{namespace}/", task["id"]): pass\n',
            "regex on a task's id field":
                f'import re\nif re.match(r"^{namespace}/W[0-9]", item["task"]): pass\n',
            "startswith on a task id":
                f'if task["id"].startswith("{namespace}/"): pass\n',
            "bare prefix on a task id":
                f'if task["id"].startswith({namespace!r}): pass\n',
            "startswith on a namespace taken from a task id":
                f'if task["id"].split("/")[0].startswith({namespace!r}): pass\n',
            "split then compare":
                f'if task["id"].split("/")[0] == {namespace!r}: pass\n',
            "rsplit then compare":
                f'if task["id"].rsplit("/", 1)[0] == {namespace!r}: pass\n',
            "membership on a task id":
                f'if "{namespace}/" in task["id"]: pass\n',
        }
        for label, source in cases.items():
            with self.subTest(shape=label):
                self.assertTrue(self.scan(namespace_branches, source),
                                f"{label} was not seen as a namespace branch: {source.strip()}")

    def test_namespace_branches_ignores_generic_task_id_code(self):
        source = (
            'import re\n'
            'namespace = task["id"].split("/", 1)[0]\n'
            'if dep.startswith(namespace + "/"):\n    pass\n'
            'if a.startswith(b + "/"):\n    pass\n'
            'if re.fullmatch(r"[a-z][a-z0-9.-]{2,63}", program):\n    pass\n'
            'if task["id"] == other:\n    pass\n'
            'if task["id"].startswith("W-"):\n    pass\n'
            'if task["id"].split("/"):\n    pass\n'
        )
        self.assertEqual(self.scan(namespace_branches, source), [])

    def test_namespace_defaults_finds_a_named_default(self):
        for option in sorted(NAMESPACE_OPTIONS):
            with self.subTest(option=option):
                source = f"parser.add_argument({option!r}, default={self.NAMESPACE!r})\n"
                self.assertTrue(self.scan(namespace_defaults, source),
                                f"{option} with a named default was not seen")

    def test_namespace_defaults_ignores_a_required_option(self):
        source = (
            'parser.add_argument("--program", required=True)\n'
            'parser.add_argument("--namespace", default=None)\n'
            'parser.add_argument("--depth", default="3")\n'
        )
        self.assertEqual(self.scan(namespace_defaults, source), [])


class EngineSourceTest(unittest.TestCase):
    def test_engine_source_carries_no_task_id_literal(self):
        hits = _scan_sources(namespaced_literals)
        self.assertEqual(hits, [], (
            "engine source names a program namespace; use <program>/<NAME> in examples and read "
            "the namespace from the registry"))

    def test_engine_source_does_not_branch_on_a_namespace(self):
        hits = _scan_sources(namespace_branches)
        self.assertEqual(hits, [], (
            "engine source branches on a literal namespace; take the program from the task or the "
            "arguments instead"))

    def test_no_option_defaults_to_a_named_program_or_task(self):
        hits = _scan_sources(namespace_defaults)
        self.assertEqual(hits, [], (
            "an option defaults to a named program or task; require it explicitly"))

    def test_the_scan_covers_every_shipped_module(self):
        relative = {str(path.relative_to(WORK)) for path in engine_sources()}
        self.assertLessEqual({"engine.py", "store.py", "loom", "validate_work.py",
                              "mcp/server.py", "a2a/server.py", "linear_sync.py"}, relative)


# --------------------------------------------------------------------------
# 2. A tracked file carries no machine path or personal mailbox
# --------------------------------------------------------------------------


def _absolute(kind: str, name: str) -> str:
    """Build a POSIX home path without writing one into this tracked file."""
    return "/" + kind + "/" + name


def _windows(drive: str, kind: str, name: str, separator: str) -> str:
    """Build a Windows home path without writing one into this tracked file."""
    return drive + ":" + separator + kind + separator + name


def _mailbox(local: str, domain: str) -> str:
    """Build a mailbox without writing one into this tracked file."""
    return local + "@" + domain


class TreeDetectorTest(unittest.TestCase):
    """The path and mailbox detectors are exercised on synthetic text.

    Without this, a broken branch (a Windows separator that never matches) looks exactly like a
    clean tree: the real-tree tests pass because nothing in the checkout violates the rule. The
    fixtures are assembled from parts because this file is itself tracked and scanned.
    """

    def test_home_paths_finds_posix_and_windows_homes(self):
        cases = [
            _absolute("Users", "alice") + "/project",
            _absolute("home", "bob"),
            _absolute("root", ".config"),
            _windows("C", "Users", "alice", "\\"),
            _windows("C", "Users", "alice", "/"),
            _windows("D", "home", "bob", "\\"),
            _windows("E", "root", "carol", "\\\\"),
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertTrue(home_paths(text), f"{text!r} was not seen as a home path")

    def test_home_paths_ignores_relative_and_placeholder_paths(self):
        cases = ("~/repositories/loom", "capabilities/work/tests", "$HOME/x", "<home>/x")
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(home_paths(text), [])

    def test_personal_emails_flags_only_reachable_mailboxes(self):
        placeholders = [
            _mailbox("a", "example.com"),
            _mailbox("b", "sub.example.org"),
            _mailbox("c", "invalid"),
            _mailbox("d", "localhost"),
        ]
        reachable = _mailbox("e", "real-domain.io")
        text = " ".join([*placeholders, reachable, _mailbox("loom-worker", "example.invalid")])
        self.assertEqual(personal_emails(text), [reachable])


class TrackedTreeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = tracked_files()

    def test_no_absolute_home_path_in_a_tracked_file(self):
        hits = []
        for path in self.files:
            text = read_text(path)
            if text is None:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                for token in home_paths(line):
                    hits.append(Hit(path.relative_to(REPO), number, token))
        self.assertEqual(hits, [], "use ~ or $HOME instead of an absolute home path")

    def test_no_personal_email_in_a_tracked_file(self):
        hits = []
        for path in self.files:
            text = read_text(path)
            if text is None:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                for token in personal_emails(line):
                    hits.append(Hit(path.relative_to(REPO), number, token))
        self.assertEqual(hits, [], "use a reserved example domain instead of a personal mailbox")


# --------------------------------------------------------------------------
# 3. The operator's denylist stays outside the repository
# --------------------------------------------------------------------------


class PrivateDenylistTest(unittest.TestCase):
    def test_the_sweep_finds_a_guarded_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = root / "clean.py"
            clean.write_text("namespace = '<program>'\n", encoding="utf-8")
            leak = root / "leak.md"
            leak.write_text("owner: AcmeWidget\n", encoding="utf-8")
            entries = parse_denylist("# a comment\n\nacmewidget\n")
            hits = denylist_hits(entries, [clean, leak])
            self.assertEqual([(hit.path.name, hit.line, hit.token) for hit in hits],
                             [("leak.md", 1, "acmewidget")])

    def test_the_sweep_matches_whole_tokens_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.md"
            path.write_text("acmewidgetry is not the name\nACMEWIDGET is\n", encoding="utf-8")
            hits = denylist_hits(parse_denylist("acmewidget"), [path])
            self.assertEqual([(hit.line, hit.token) for hit in hits], [(2, "acmewidget")])

    def test_the_denylist_lives_outside_the_repository(self):
        path = denylist_path()
        if path is None:
            self.skipTest(f"{DENYLIST_ENV} is unset; the operator supplies the list")
        self.assertFalse(path.resolve().is_relative_to(REPO.resolve()),
                         f"{path} is inside the public checkout; move the list out of it")

    def test_no_guarded_name_in_a_tracked_file(self):
        path = denylist_path()
        if path is None or not path.is_file():
            self.skipTest(f"no denylist at {DENYLIST_ENV}; set it to a file outside this repository")
        entries = load_denylist(path)
        self.assertTrue(entries, f"{path} holds no entries")
        hits = denylist_hits(entries, tracked_files())
        self.assertEqual(hits, [], "a guarded name reached a tracked file")


if __name__ == "__main__":
    unittest.main(verbosity=2)
