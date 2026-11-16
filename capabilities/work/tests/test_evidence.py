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
import subprocess
import sys
import tempfile
import textwrap
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
REPO = WORK.parents[1]

sys.path.insert(0, str(WORK))
import engine  # noqa: E402 — sibling module, resolved from the work home

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
        self.stub("import sys\nprint('FAIL: no registry in the store')\nsys.exit(1)\n")
        result = engine.check_evidence(
            "<!-- loom:validator.errors -->0<!-- /loom -->", root=self.root)
        self.assertFalse(result["ok"])
        self.assertIn("cannot be counted", result["problems"][0]["problem"])

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

    def test_every_example_in_the_template_table_is_a_real_derivation(self):
        claims, problems = engine.parse_evidence_claims(engine.EVIDENCE_TEMPLATE)
        self.assertEqual(problems, [])
        self.assertGreaterEqual(len(claims), 4, "the derivation table lost its examples")
        known = engine.evidence_derivations()
        for claim in claims:
            with self.subTest(claim=engine.evidence_spec(claim)):
                self.assertIn(claim["claim"], known)

    def test_the_rendered_template_is_current_and_not_empty(self):
        text = engine.render_evidence_template(root=WORK)
        result = engine.check_evidence(text, root=WORK)
        self.assertTrue(result["ok"], engine.evidence_report([result]))
        self.assertGreaterEqual(len(result["claims"]), 4)
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
