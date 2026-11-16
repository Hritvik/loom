#!/usr/bin/env python3
"""Evidence claims are re-derived by the suite instead of being believed.

An evidence file is read by a reviewer who cannot re-run every command, so a sentence such as
"the suite ran 276 tests, OK" is taken on trust. Fifteen review rounds on one evidence file
produced fifteen wrong numbers, each true when written and false within a merge. The fix is not
more careful writing; it is to stop trusting the sentence.

A **machine-checked span** writes the value between two HTML comments that name a derivation:

    The suite collects <!-- loom:tests.count -->276<!-- /loom --> tests.

`engine.check_evidence` re-derives the value from the tree and reports a difference as stale, so
the number is a claim on the tree rather than on the author. HTML comments render blank, so a
reader sees the sentence. Everything outside a span is prose, which no checker reads — and the
template in `engine.EVIDENCE_TEMPLATE` says so at the top of every evidence file it starts.

The checks here prove three things the task asks for:

1. A stale test count fails rather than persuading — `StaleClaimTest`, and the live sweep in
   `RepositoryEvidenceTest` over every file under `evidence/`.
2. A claimed validator result is re-derived rather than copied — `ValidatorClaimTest` drives a
   stub validator whose exit status flips, and the claim follows the validator, not the file.
3. The template marks machine-checked lines and prose as different things — `EvidenceTemplateTest`,
   which also checks that the rendered starter is current instead of a snapshot of some past tree.

And one thing the first round of this work got wrong: a committed claim has to be a property of
the **commit**. The validator reads the portfolio registry, which a clone does not have and another
board can change, so the validator derivations are declared in `engine.SHARED_STATE_DERIVATIONS`
and no committed evidence file may claim them. `RepositoryEvidenceTest` fails if one does, and
runs the guard in a subprocess with an empty HOME to prove the documented `unittest` command is
green in a fresh clone rather than only on a machine with a registry.

A *prose* number is not checked, by construction. The boundary is worth saying plainly: if a
sentence states something a machine can re-derive, mark it, or accept that a reviewer is being
asked to believe it.

Run: python3 -m unittest discover -s tests -v
Refresh the evidence in this repository, from `capabilities/work`:
    LOOM_EVIDENCE_REFRESH=1 python3 -m unittest discover -s tests -p test_evidence.py
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

WORK = Path(__file__).resolve().parents[1]
REPO = WORK.parents[1]

sys.path.insert(0, str(WORK))
import engine  # noqa: E402 — sibling module, resolved from the work home
import store as work_store  # noqa: E402 — the fixture registry below is a real store

# The derivations the engine itself registers, captured before this module adds the repository's
# own sweep below, so the template can be held to documenting exactly the built-ins.
ENGINE_DERIVATIONS = frozenset(engine.EVIDENCE_DERIVATIONS)

REFRESH = "LOOM_EVIDENCE_REFRESH"


def load(name: str, path: Path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# The boundary detectors are already proved on synthetic input in test_boundary; reuse them here
# rather than writing a second scanner that could disagree with the first.
boundary = load("evidence_boundary", WORK / "tests" / "test_boundary.py")


def derive_boundary_sweep(root, argument, cache):
    """Tracked files carrying an absolute home path or a personal mailbox, counted."""
    if argument:
        raise engine.EvidenceClaimError("sweep.boundary-tree takes no argument")
    hits = 0
    for path in boundary.tracked_files(Path(root)):
        text = boundary.read_text(path)
        if text is not None and (boundary.home_paths(text) or boundary.personal_emails(text)):
            hits += 1
    return str(hits)


engine.register_evidence_derivation("sweep.boundary-tree", derive_boundary_sweep)

# Fixtures assembled from parts, because this file is tracked and scanned for home paths and
# personal mailboxes by test_boundary.
HOME_PATH = boundary._absolute("Users", "somebody")
MAILBOX = boundary._mailbox("somebody", "mail.acme")


def constant(value: str):
    def derive(root, argument, cache):
        return value

    return derive


def write(root: Path, name: str, text: str) -> Path:
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def collected_tests() -> str:
    """The suite size the derivation reports, so a fixture can be wrong by one."""
    return engine.EVIDENCE_DERIVATIONS["tests.count"](WORK, "", {})


class SpanParsingTest(unittest.TestCase):
    """A marker that does not form a span must fail, never fall back to prose."""

    def test_a_span_yields_its_claim_argument_and_line(self):
        text = "intro\nThe suite collects <!-- loom:tests.count -->276<!-- /loom --> tests.\n"
        claims, problems = engine.parse_evidence_claims(text)
        self.assertEqual(problems, [])
        self.assertEqual(
            [(claim["claim"], claim["argument"], claim["recorded"], claim["line"])
             for claim in claims],
            [("tests.count", "", "276", 2)])

    def test_two_spans_on_one_line_are_two_claims(self):
        text = ("<!-- loom:tests.count -->276<!-- /loom --> tests, "
                "<!-- loom:validator.verdict -->PASS<!-- /loom -->")
        claims, problems = engine.parse_evidence_claims(text)
        self.assertEqual(problems, [])
        self.assertEqual([claim["claim"] for claim in claims],
                         ["tests.count", "validator.verdict"])

    def test_an_argument_after_the_derivation_is_kept(self):
        text = "<!-- loom:tracked.count:templates/ -->9<!-- /loom -->"
        claims, _problems = engine.parse_evidence_claims(text)
        self.assertEqual(claims[0]["argument"], "templates/")
        self.assertEqual(engine.evidence_spec(claims[0]), "tracked.count:templates/")

    def test_prose_is_not_a_claim(self):
        result = engine.check_evidence(
            "The suite ran 9999 tests.\n"
            "<!-- loom:tests.count -->" + collected_tests() + "<!-- /loom -->\n",
            root=WORK)
        self.assertTrue(result["ok"], engine.evidence_report([result]))
        self.assertEqual(len(result["claims"]), 1)

    def test_an_unclosed_span_is_a_problem(self):
        claims, problems = engine.parse_evidence_claims("<!-- loom:tests.count -->276")
        self.assertEqual(claims, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("not a span", problems[0]["problem"])

    def test_a_stray_closing_marker_is_a_problem(self):
        _claims, problems = engine.parse_evidence_claims("value<!-- /loom -->")
        self.assertEqual(len(problems), 1)
        self.assertIn("no span", problems[0]["problem"])

    def test_a_marker_in_a_fenced_block_is_a_quotation(self):
        text = ("Example:\n\n```\n<!-- loom:tests.count -->0<!-- /loom -->\n```\n\n"
                "<!-- loom:tests.count -->" + collected_tests() + "<!-- /loom -->\n")
        claims, problems = engine.parse_evidence_claims(text)
        self.assertEqual(problems, [])
        self.assertEqual(len(claims), 1, "a fenced example was read as a claim")
        result = engine.check_evidence(text, root=WORK)
        self.assertTrue(result["ok"], engine.evidence_report([result]))

    def test_refresh_leaves_a_fenced_example_alone(self):
        text = "```\n<!-- loom:tests.count -->0<!-- /loom -->\n```\n"
        updated, changes = engine.refresh_evidence_text(text, root=WORK)
        self.assertEqual(updated, text)
        self.assertEqual(changes, [])

    def test_an_unclosed_fence_is_a_problem(self):
        """An unclosed fence would exempt the rest of the file from checking."""
        text = ("```\n"
                "<!-- loom:tests.count -->0<!-- /loom -->\n")
        claims, problems = engine.parse_evidence_claims(text)
        self.assertEqual(claims, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("unclosed code fence", problems[0]["problem"])
        result = engine.check_evidence(text, root=WORK)
        self.assertFalse(result["ok"])

    def test_refresh_reports_an_unclosed_fence(self):
        text = "```\n<!-- loom:tests.count -->0<!-- /loom -->\n"
        updated, changes = engine.refresh_evidence_text(text, root=WORK)
        self.assertEqual(updated, text)
        self.assertTrue(any("unclosed code fence" in change for change in changes))

    def test_an_unknown_derivation_fails_even_when_the_value_looks_fine(self):
        result = engine.check_evidence(
            "<!-- loom:tests.cuont -->0<!-- /loom -->",
            derivations={"tests.count": constant("0")})
        self.assertFalse(result["ok"])
        self.assertEqual(result["claims"][0]["derived"], None)
        self.assertIn("unknown derivation", result["problems"][0]["problem"])

    def test_a_derivation_that_cannot_answer_is_a_problem(self):
        def refuse(root, argument, cache):
            raise engine.EvidenceClaimError("nothing to count")

        result = engine.check_evidence("<!-- loom:test.refuse -->0<!-- /loom -->",
                                       derivations={"test.refuse": refuse})
        self.assertFalse(result["ok"])
        self.assertIn("nothing to count", result["problems"][0]["problem"])


class StaleClaimTest(unittest.TestCase):
    """Acceptance 1: a stale test count fails the suite instead of persuading the reviewer."""

    def test_a_stale_test_count_fails_the_check(self):
        recorded = int(collected_tests()) - 1
        result = engine.check_evidence(
            f"The suite collects <!-- loom:tests.count -->{recorded}<!-- /loom --> tests.",
            root=WORK)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["stale"]), 1)
        self.assertEqual(result["stale"][0]["recorded"], str(recorded))
        self.assertEqual(result["stale"][0]["derived"], collected_tests())
        self.assertIn("STALE", engine.evidence_report([result]))

    def test_a_current_test_count_passes(self):
        result = engine.check_evidence(
            f"The suite collects <!-- loom:tests.count -->{collected_tests()}<!-- /loom --> tests.",
            root=WORK)
        self.assertTrue(result["ok"], engine.evidence_report([result]))

    def test_the_report_names_the_file_and_the_line(self):
        root = Path(tempfile.mkdtemp())
        path = write(root, "evidence/W-1.md",
                     "header\n\nThe suite ran <!-- loom:tests.count -->0<!-- /loom --> tests.\n")
        report = engine.evidence_report([engine.check_evidence_file(path, root=WORK)])
        self.assertIn(f"{path}:3", report)
        self.assertIn("tests.count", report)
        self.assertIn("recorded 0", report)
        self.assertIn(f"derived {collected_tests()}", report)

    def test_refresh_rewrites_only_the_stale_span(self):
        prose = "Prose stays exactly as written, including the word 9999."
        text = f"{prose}\n<!-- loom:tests.count -->0<!-- /loom -->\n"
        updated, changes = engine.refresh_evidence_text(text, root=WORK)
        self.assertEqual(len(changes), 1)
        self.assertIn(prose, updated)
        self.assertIn(f"<!-- loom:tests.count -->{collected_tests()}<!-- /loom -->", updated)
        result = engine.check_evidence(updated, root=WORK)
        self.assertTrue(result["ok"], engine.evidence_report([result]))

    def test_refresh_leaves_a_current_span_byte_identical(self):
        text = ("<!--  loom:test.fixed  -->7<!--  /loom  --> and "
                "<!-- loom:test.fixed:any-argument -->7<!-- /loom -->")
        updated, changes = engine.refresh_evidence_text(
            text, derivations={"test.fixed": constant("7")})
        self.assertEqual(updated, text, "refresh rewrote a span that was already current")
        self.assertEqual(changes, [])

    def test_refresh_reports_a_derivation_it_cannot_answer(self):
        def refuse(root, argument, cache):
            raise engine.EvidenceClaimError("no such sweep")

        text = "<!-- loom:test.refuse -->1<!-- /loom -->"
        updated, changes = engine.refresh_evidence_text(text, derivations={"test.refuse": refuse})
        self.assertEqual(updated, text)
        self.assertEqual(len(changes), 1)
        self.assertIn("no such sweep", changes[0])


class TestsCountTest(unittest.TestCase):
    """The count is a property of the tree, not of how this process was launched."""

    def test_the_count_ignores_a_test_name_filter_on_the_shared_loader(self):
        before = collected_tests()
        loader = unittest.defaultTestLoader
        had = hasattr(loader, "testNamePatterns")
        original = getattr(loader, "testNamePatterns", None)
        loader.testNamePatterns = ["nothing-matches-this-pattern"]
        try:
            self.assertEqual(collected_tests(), before,
                             "the derivation used the loader that `unittest -k` filters")
        finally:
            if had:
                loader.testNamePatterns = original
            else:
                del loader.testNamePatterns

    def test_a_test_module_that_will_not_import_is_a_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "tests/test_broken.py", "def unclosed(:\n")
            derive = engine.EVIDENCE_DERIVATIONS["tests.count"]
            with self.assertRaises(engine.EvidenceClaimError) as caught:
                derive(root, "", {})
        self.assertIn("could not be collected", str(caught.exception))

    def test_a_missing_test_directory_is_a_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            derive = engine.EVIDENCE_DERIVATIONS["tests.count"]
            with self.assertRaises(engine.EvidenceClaimError):
                derive(Path(tmp), "", {})


VALIDATOR_STUB = '''\
import pathlib
import sys

failing = pathlib.Path(__file__).with_name("failing")
if failing.exists():
    print("ERROR subject-one: the first problem")
    print("ERROR other: an unrelated problem")
    print("FAIL (2 errors, 0 warnings)")
    sys.exit(1)
print("PASS")
'''


class ValidatorClaimTest(unittest.TestCase):
    """Acceptance 2: the validator result is re-derived, not copied out of the evidence file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write(self.root, "validate_work.py", VALIDATOR_STUB)
        self.flag = self.root / "failing"

    def tearDown(self):
        self.tmp.cleanup()

    def stub(self, text: str) -> None:
        write(self.root, "validate_work.py", text)

    def verdict_text(self, recorded: str) -> str:
        return f"The validator reports <!-- loom:validator.verdict -->{recorded}<!-- /loom -->."

    def test_the_verdict_follows_the_validator_both_ways(self):
        self.assertTrue(engine.check_evidence(self.verdict_text("PASS"), root=self.root)["ok"])
        self.flag.write_text("", encoding="utf-8")
        stale = engine.check_evidence(self.verdict_text("PASS"), root=self.root)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["stale"][0]["derived"], "FAIL")
        self.assertTrue(engine.check_evidence(self.verdict_text("FAIL"), root=self.root)["ok"])

    def test_a_flipped_validator_makes_a_current_claim_stale(self):
        self.flag.write_text("", encoding="utf-8")
        text = self.verdict_text("FAIL")
        self.assertTrue(engine.check_evidence(text, root=self.root)["ok"])
        self.flag.unlink()
        stale = engine.check_evidence(text, root=self.root)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["stale"][0]["derived"], "PASS")

    def test_errors_are_counted_and_filtered_by_name(self):
        self.flag.write_text("", encoding="utf-8")
        text = ("<!-- loom:validator.errors -->0<!-- /loom --> total, "
                "<!-- loom:validator.errors:subject-one -->0<!-- /loom --> mine, "
                "<!-- loom:validator.errors:subject-two -->0<!-- /loom --> someone else's")
        result = engine.check_evidence(text, root=self.root)
        self.assertFalse(result["ok"])
        self.assertEqual([claim["derived"] for claim in result["claims"]], ["2", "1", "0"])

    def test_a_missing_validator_is_a_problem_not_a_pass(self):
        with tempfile.TemporaryDirectory() as empty:
            result = engine.check_evidence(self.verdict_text("PASS"), root=Path(empty))
        self.assertFalse(result["ok"])
        self.assertIn("no validate_work.py", result["problems"][0]["problem"])

    def test_a_validator_that_cannot_count_is_not_reported_as_zero(self):
        """A `FAIL` with no ERROR lines is a broken run, not a clean one."""
        self.stub("import sys\nprint('FAIL: could not load schemas/task.schema.json')\n"
                  "sys.exit(1)\n")
        result = engine.check_evidence(
            "<!-- loom:validator.errors -->0<!-- /loom -->", root=self.root)
        self.assertFalse(result["ok"])
        self.assertIn("cannot be counted", result["problems"][0]["problem"])

    def test_a_validator_with_no_registry_says_what_is_missing(self):
        """The registry is shared state a clone does not have; say so instead of counting it."""
        self.stub("import sys\nprint('FAIL: no registry in sqlite:/nowhere/work.db')\n"
                  "sys.exit(1)\n")
        result = engine.check_evidence(
            "<!-- loom:validator.errors -->0<!-- /loom -->", root=self.root)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["claims"][0]["derived"])
        self.assertIn("no registry", result["problems"][0]["problem"])
        self.assertIn("clone does not have", result["problems"][0]["problem"])

    def test_the_validator_derivations_are_declared_as_shared_state(self):
        """A claim only the machine can settle is named, so committed evidence can refuse it."""
        self.assertEqual(engine.SHARED_STATE_DERIVATIONS,
                         frozenset({"validator.verdict", "validator.errors"}))
        for name in engine.SHARED_STATE_DERIVATIONS:
            with self.subTest(derivation=name):
                self.assertIn(name, engine.EVIDENCE_DERIVATIONS)

    def test_a_tally_that_disagrees_with_the_error_lines_is_a_problem(self):
        self.stub("import sys\nprint('ERROR only-one: x')\nprint('FAIL (2 errors, 0 warnings)')\n"
                  "sys.exit(1)\n")
        result = engine.check_evidence(
            "<!-- loom:validator.errors -->1<!-- /loom -->", root=self.root)
        self.assertFalse(result["ok"])
        self.assertIn("no longer matches", result["problems"][0]["problem"])

    def test_a_validator_that_exits_zero_with_errors_is_a_problem(self):
        self.stub("print('ERROR impossible: x')\n")
        result = engine.check_evidence(
            "<!-- loom:validator.errors -->0<!-- /loom -->", root=self.root)
        self.assertFalse(result["ok"])
        self.assertIn("exited 0", result["problems"][0]["problem"])

    def test_the_verdict_takes_no_argument(self):
        with self.assertRaises(engine.EvidenceClaimError):
            engine.EVIDENCE_DERIVATIONS["validator.verdict"](self.root, "extra", {})


def build_validator_fixture(root: Path) -> Path:
    """The repository's own validator over a real, empty registry, all under `root`.

    Copying the validator rather than stubbing it is what makes the claim end to end; building the
    registry under `root` is what makes the answer a property of the fixture rather than of this
    machine's store.
    """
    root = Path(root)
    for name in ("validate_work.py", "store.py"):
        shutil.copyfile(WORK / name, root / name)
    shutil.copytree(WORK / "schemas", root / "schemas")
    (root / "program.md").write_text("# Fixture programme\n\n## Fixture board\n", encoding="utf-8")
    db = root / "work.db"
    fixture = work_store.make_store(root, "sqlite", str(db))
    fixture.registry = {
        "kind": work_store.REGISTRY_KIND,
        "version": 1,
        "updated": "2026-01-01",
        "boards": [{"id": "fixture.board", "kind": "program", "role": "provider",
                    "repo": str(root), "path": "program.md"}],
        "tasks": [], "edges": [], "claims": [],
    }
    with fixture.lock():
        fixture.commit(event="genesis", agent="fixture",
                       registry_sha256="0" * 64, tasks=0)
    connection = getattr(fixture, "connection", None)
    if connection is not None:
        connection.close()
    return db


class RealValidatorFixtureTest(unittest.TestCase):
    """A claimed validator result against the repository's own validator and a real registry.

    The stub in `ValidatorClaimTest` proves the wiring; this proves it end to end without touching
    this machine's store: `validate_work.py` and `store.py` are copied into a temporary root, the
    registry is built there, and `WORK_DB` points the run at it. A verdict is then re-derived from
    the fixture, and breaking the fixture moves it.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = build_validator_fixture(self.root)
        self.env = mock.patch.dict(os.environ, {
            "WORK_DB": str(self.db), "WORK_PROGRAMS": "", "WORK_CONTRACTS": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    @staticmethod
    def verdict(recorded: str) -> str:
        return f"The validator reports <!-- loom:validator.verdict -->{recorded}<!-- /loom -->."

    def test_the_real_validator_verdict_is_re_derived_both_ways(self):
        current = engine.check_evidence(self.verdict("PASS"), root=self.root)
        self.assertTrue(current["ok"], engine.evidence_report([current]))
        self.assertEqual(current["claims"][0]["derived"], "PASS")
        (self.root / "program.md").unlink()
        stale = engine.check_evidence(self.verdict("PASS"), root=self.root)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["stale"][0]["derived"], "FAIL")
        self.assertTrue(engine.check_evidence(self.verdict("FAIL"), root=self.root)["ok"])

    def test_the_real_validator_error_count_is_re_derived(self):
        text = "<!-- loom:validator.errors -->0<!-- /loom -->"
        self.assertTrue(engine.check_evidence(text, root=self.root)["ok"])
        (self.root / "program.md").unlink()
        stale = engine.check_evidence(text, root=self.root)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["stale"][0]["derived"], "1",
                         "the missing board file is exactly one ERROR line")


class TrackedCountTest(unittest.TestCase):
    """A count of tracked files is a claim about the index, so read the index."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        for name in ("templates/one.md", "templates/two.md", "templates/nested/three.md",
                     "docs/index.md"):
            write(self.root, name, "# a file\n")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_tracked_count_comes_from_the_index(self):
        derive = engine.EVIDENCE_DERIVATIONS["tracked.count"]
        self.assertEqual(derive(self.root, "templates", {}), "3")
        self.assertEqual(derive(self.root, "docs", {}), "1")
        result = engine.check_evidence(
            "<!-- loom:tracked.count:templates -->2<!-- /loom --> starters", root=self.root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stale"][0]["derived"], "3")

    def test_an_untracked_file_does_not_count(self):
        write(self.root, "templates/untracked.md", "# not added\n")
        derive = engine.EVIDENCE_DERIVATIONS["tracked.count"]
        self.assertEqual(derive(self.root, "templates", {}), "3")

    def test_a_pathspec_cannot_escape_the_repository(self):
        derive = engine.EVIDENCE_DERIVATIONS["tracked.count"]
        for argument in ("", "-n", "/etc", "../outside"):
            with self.subTest(argument=argument):
                with self.assertRaises(engine.EvidenceClaimError):
                    derive(self.root, argument, {})


class EvidenceTemplateTest(unittest.TestCase):
    """Acceptance 3: the template says which lines are machine-checked and which are prose."""

    def test_the_template_marks_both_kinds_of_line(self):
        template = engine.EVIDENCE_TEMPLATE
        self.assertIn("**machine-checked**", template)
        self.assertIn("**prose**", template)
        self.assertIn("<!-- loom:<derivation> --><value><!-- /loom -->", template)
        self.assertIn("## Delivered", template)
        self.assertIn("## Verification", template)
        self.assertIn("## Limits", template)

    def test_every_engine_derivation_is_named_in_the_template(self):
        for name in sorted(ENGINE_DERIVATIONS):
            with self.subTest(derivation=name):
                self.assertIn(name, engine.EVIDENCE_TEMPLATE)

    def test_the_template_names_which_derivations_need_more_than_the_tree(self):
        template = engine.EVIDENCE_TEMPLATE
        self.assertIn("no — the registry", template)
        self.assertIn("clone does not have", template)

    def test_the_template_claims_only_what_the_tree_can_answer(self):
        """A starter file is checkable in a fresh clone, or it is a starter that cannot be used."""
        claims, problems = engine.parse_evidence_claims(engine.EVIDENCE_TEMPLATE)
        self.assertEqual(problems, [])
        self.assertTrue(claims, "the template shows no machine-checked span to copy")
        shared = [engine.evidence_spec(claim) for claim in claims
                  if claim["claim"] in engine.SHARED_STATE_DERIVATIONS]
        self.assertEqual(
            shared, [],
            "a committed evidence file is checked in a fresh clone, so it cannot claim "
            f"{', '.join(sorted(engine.SHARED_STATE_DERIVATIONS))}; "
            "state that result as prose and say in Limits what it depends on")

    def test_the_rendered_template_is_current_and_not_empty(self):
        text = engine.render_evidence_template(root=WORK)
        result = engine.check_evidence(text, root=WORK)
        self.assertTrue(result["ok"], engine.evidence_report([result]))
        self.assertGreaterEqual(len(result["claims"]), 1)
        for claim in result["claims"]:
            with self.subTest(claim=engine.evidence_spec(claim)):
                self.assertNotEqual(claim["recorded"], "",
                                    "rendering left a machine-checked span empty")

    def test_the_rendered_template_carries_the_real_suite_size(self):
        text = engine.render_evidence_template(root=WORK)
        claims, _problems = engine.parse_evidence_claims(text)
        counts = {claim["recorded"] for claim in claims if claim["claim"] == "tests.count"}
        self.assertEqual(counts, {collected_tests()})

    def test_rendering_is_idempotent(self):
        once = engine.render_evidence_template(root=WORK)
        twice, changes = engine.refresh_evidence_text(once, root=WORK)
        self.assertEqual(twice, once)
        self.assertEqual(changes, [])

    def test_rendering_a_starter_never_runs_the_validator(self):
        """The fresh-clone path: a template that needed the registry could not start a file."""
        text = engine.render_evidence_template(root=WORK)
        claims, _problems = engine.parse_evidence_claims(text)
        self.assertEqual([claim["claim"] for claim in claims], ["tests.count"])

    def test_the_worker_brief_points_at_the_template(self):
        self.assertIn("EVIDENCE_TEMPLATE", engine.WORKER_BRIEF)
        self.assertIn("<!-- loom:<derivation> -->", engine.WORKER_BRIEF)


class BoundarySweepTest(unittest.TestCase):
    """The one derivation this repository registers outside the engine also has to bite."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, name: str, text: str) -> None:
        path = Path(self.root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", name], check=True)

    def test_a_clean_tree_counts_zero(self):
        self.add("README.md", "# nothing to see\n")
        derive = engine.EVIDENCE_DERIVATIONS["sweep.boundary-tree"]
        self.assertEqual(derive(self.root, "", {}), "0")

    def test_a_planted_home_path_is_counted(self):
        self.add("README.md", "# nothing to see\n")
        self.add("docs/leak.md", f"clone into {HOME_PATH} instead\n")
        derive = engine.EVIDENCE_DERIVATIONS["sweep.boundary-tree"]
        self.assertEqual(derive(self.root, "", {}), "1")

    def test_a_planted_mailbox_is_counted(self):
        self.add("docs/contact.md", f"write to {MAILBOX}\n")
        derive = engine.EVIDENCE_DERIVATIONS["sweep.boundary-tree"]
        self.assertEqual(derive(self.root, "", {}), "1")
    def test_the_sweep_takes_no_argument(self):
        with self.assertRaises(engine.EvidenceClaimError):
            engine.EVIDENCE_DERIVATIONS["sweep.boundary-tree"](self.root, "extra", {})


class RepositoryEvidenceTest(unittest.TestCase):
    """The live guard: every machine-checked claim in this repository is current right now."""

    def test_the_guard_does_not_read_shared_state(self):
        """A committed claim is a property of the commit, not of the machine that checks it."""
        paths = sorted((WORK / "evidence").glob("*.md"))
        self.assertTrue(paths, "there is no evidence to check")
        offenders = []
        for path in paths:
            claims, _problems = engine.parse_evidence_claims(
                path.read_text(encoding="utf-8"))
            for claim in claims:
                if claim["claim"] in engine.SHARED_STATE_DERIVATIONS:
                    offenders.append(f"{path.name}:{claim['line']}  {engine.evidence_spec(claim)}")
        self.assertEqual(
            offenders, [],
            "these claims read the portfolio registry: a clone does not have it and another "
            "board's edit can change it, so the guard fails for reasons this diff never made. "
            "State the result as prose instead:\n  " + "\n  ".join(offenders))

    def test_the_guard_passes_in_a_fresh_clone(self):
        """Empty HOME and no registry: the committed evidence must still check clean."""
        with tempfile.TemporaryDirectory() as empty:
            env = dict(os.environ)
            env["HOME"] = empty
            env["WORK_DB"] = str(Path(empty) / "absent.db")
            for name in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", REFRESH):
                env.pop(name, None)
            process = subprocess.run(
                [sys.executable, "-m", "unittest",
                 "test_evidence.RepositoryEvidenceTest"
                 ".test_every_claim_in_the_evidence_is_current"],
                cwd=str(WORK / "tests"), env=env, capture_output=True, text=True, timeout=300,
            )
        self.assertEqual(
            process.returncode, 0,
            "the evidence guard depends on the machine rather than the commit:\n"
            + (process.stdout or "") + (process.stderr or ""))

    def test_every_claim_in_the_evidence_is_current(self):
        paths = sorted((WORK / "evidence").glob("*.md"))
        self.assertTrue(paths, "there is no evidence to check")
        rewritten = []
        if os.environ.get(REFRESH) == "1":
            for path in paths:
                changes = engine.refresh_evidence_file(path, root=WORK)
                if changes:
                    rewritten.append(f"{path.name}: " + "; ".join(changes))
        results = [engine.check_evidence_file(path, root=WORK) for path in paths]
        if rewritten:
            print("\nrefreshed under " + REFRESH + "=1: " + " | ".join(rewritten))
        claims = sum(len(result["claims"]) for result in results)
        self.assertGreater(
            claims, 0,
            "no evidence file carries a machine-checked claim, so this guard is passing "
            "vacuously; mark the numbers instead of typing them")
        stale = [result for result in results if not result["ok"]]
        if stale:
            self.fail(
                "\n" + engine.evidence_report(stale) + "\n"
                "Refresh instead of retyping, from capabilities/work: "
                + REFRESH + "=1 python3 -m unittest discover -s tests -p test_evidence.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
