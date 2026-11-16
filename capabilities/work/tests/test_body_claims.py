#!/usr/bin/env python3
"""A task body carries no state, enforced by the suite rather than promised in an evidence file.

`docs/work-items.md` is the body authority for this program: the problem statement and the
acceptance live there, and nothing else does. Identity, status, claims, leases and dependency edges
live in the registry, where a change appends an event and a revision. A `**Status.**` line or a
`Depends on …` sentence in a body is a second copy of that state with no transaction behind it, and
it drifts. It drifted in this file: twelve sections claimed `Reviewed` or `Ready` while the registry
recorded six of them integrated with receipts, and the validator named every one of the eight that
disagreed.

Two claims are forbidden in a body, and each has a detector here:

1. **A status claim.** A `Status`/`State` label, or a line that is nothing but a registry status
   (`Reviewed.`). The vocabulary is read from `schemas/task.schema.json`, so the detector cannot
   drift from the statuses the registry actually accepts.
2. **A dependency claim.** A `Depends on`/`Dependencies`/`Blocked by`/`Blocks` label, or a
   dependency phrase that names a task (`… depends on LOOM-5`). The edge belongs in the registry,
   where `./loom dep add` records it and readiness is computed from it.

A bare reference is not a claim: `See LOOM-5 for the attempt model.` stays allowed, because it
asserts nothing about the relation.

The detectors, and the problem-statement check, are exercised on synthetic sections below, so a
refactor cannot quietly turn them into no-ops. A detector that has stopped biting looks exactly like
a clean body — which is the failure this file exists to stop, one layer up.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
REPO = WORK.parents[1]
BODY = REPO / "docs" / "work-items.md"
SCHEMA = WORK / "schemas" / "task.schema.json"

# --------------------------------------------------------------------------
# What counts as a claim
# --------------------------------------------------------------------------

# A markdown line's opening furniture: indentation, a blockquote or list marker, and emphasis. A
# label after it is a label the reader sees, whether or not it is bolded.
LEAD = r"^\s*(?:[>*+-]\s*)*[*_`]{0,2}"

# `**Status.** Reviewed.`, `Status: integrated`, `- State: claimed`.
STATUS_LABEL = re.compile(
    LEAD + r"(?:status|state)\b[*_`]{0,2}\s*[.:]",
    re.IGNORECASE,
)

# A markdown table row whose first cell is the label: `| Status | Ready |`. The label is still a
# label the reader sees, so it is a claim even though it carries no colon.
STATUS_ROW = re.compile(r"^\s*\|\s*(?:status|state)\s*\|", re.IGNORECASE)

# `**Depends.** LOOM-5`, `Dependencies: LOOM-4`, `- Blocked by LOOM-3`. A label only claims an edge
# when it is punctuated or names a task: prose that wraps onto a line beginning with the word
# "dependency" is describing the rule, not asserting one.
DEPENDENCY_LABEL = re.compile(
    LEAD + r"(?:depends(?:\s+on)?|dependencies|dependency|blocked\s+by|blocks)\b"
    r"(?P<after>[*_`]{0,2}\s*[.:])?",
    re.IGNORECASE,
)

# The table shape: `| Dependencies | LOOM-4 |`. Punctuation is the cell boundary, not a colon.
DEPENDENCY_ROW = re.compile(
    r"^\s*\|\s*(?:depends(?:\s+on)?|dependencies|dependency|blocked\s+by|blocks)\s*\|",
    re.IGNORECASE,
)

# The same relation written into a sentence rather than a label. It is only a claim when it names a
# task; prose that merely uses the word is not an edge.
DEPENDENCY_PHRASE = re.compile(
    r"\b(?:depends?\s+on|dependent\s+on|blocked\s+by|dependencies?)\b",
    re.IGNORECASE,
)

# A task reference: an optional `<program>/` prefix, then a name that starts uppercase, carries a
# number and has at least one hyphen segment (`LOOM-5`, `W10-A`, `dutch-ref/W10-B`). Lowercase
# hyphenated words and paths are not ids.
TASK_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:[a-z][a-z0-9.-]{0,62}/)?"
    r"(?=[A-Za-z0-9._/-]*\d)"
    r"[A-Z][A-Za-z0-9._]*(?:-[A-Za-z0-9._]+)+"
)


def status_vocabulary(path: Path = SCHEMA) -> tuple[str, ...]:
    """The registry's statuses, read from the schema so this list cannot drift from it."""
    schema = json.loads(path.read_text(encoding="utf-8"))
    return tuple(schema["properties"]["status"]["enum"])


def normalise_word(text: str) -> str:
    """A status word with its punctuation, emphasis, bullet and hyphens flattened away."""
    text = re.sub(r"^(?:[>*+-]\s*)+", "", text.strip())
    text = text.strip("*_`").strip()
    text = text.rstrip(".:").strip().strip("*_`").strip()
    return text.casefold().replace("-", " ")


def status_forms(words: tuple[str, ...]) -> set[str]:
    """The vocabulary in the shape a bare line is normalised to."""
    return {normalise_word(word) for word in words}


def bare_status_claim(line: str, forms: set[str]) -> bool:
    """True when the line is nothing but a status word: `Reviewed.`, `**Ready.**`, `- integrated`."""
    return normalise_word(line) in forms


class Claim:
    """One finding: where it was, which kind of claim, and the line that carried it."""

    def __init__(self, line: int, kind: str, token: str):
        self.line = line
        self.kind = kind
        self.token = token

    def __repr__(self) -> str:  # a readable failure message
        return f"line {self.line}: {self.kind} claim {self.token!r}"


def is_dependency_claim(line: str) -> bool:
    """True when the line asserts a dependency edge, whether as a label or inside a sentence."""
    if DEPENDENCY_ROW.match(line):
        return True
    names_a_task = TASK_REFERENCE.search(line) is not None
    label = DEPENDENCY_LABEL.match(line)
    if label and (label.group("after") or names_a_task):
        return True
    return names_a_task and DEPENDENCY_PHRASE.search(line) is not None


def body_claims(text: str, vocabulary: tuple[str, ...] | None = None) -> list[Claim]:
    """Every status and dependency claim in a body document, in line order."""
    forms = status_forms(status_vocabulary() if vocabulary is None else vocabulary)
    claims: list[Claim] = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if STATUS_LABEL.match(line) or STATUS_ROW.match(line) or bare_status_claim(line, forms):
            claims.append(Claim(number, "status", stripped))
        if is_dependency_claim(line):
            claims.append(Claim(number, "dependency", stripped))
    return claims


def sections(text: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """Split a body document on `## ` headings: (heading, [(line number, line), …])."""
    found: list[tuple[str, list[tuple[int, str]]]] = []
    current: list[tuple[int, str]] | None = None
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            current = []
            found.append((line[3:].strip(), current))
        elif current is not None:
            current.append((number, line))
    return found


def problem_statement(lines: list[tuple[int, str]]) -> list[str]:
    """A section's prose, ending where its acceptance begins.

    The cut is what makes the check real. Acceptance paragraphs wrap onto several lines, and a
    filter that skips only the `**Acceptance.**` line counts those continuation lines as the
    problem statement — so a section with no problem statement at all still passes. This was the
    state of the check when review first read it.
    """
    prose: list[str] = []
    for _, line in lines:
        if line.strip().startswith("**Acceptance.**"):
            break
        if line.strip():
            prose.append(line)
    return prose


# --------------------------------------------------------------------------
# The detectors still bite
# --------------------------------------------------------------------------


class ClaimDetectorTest(unittest.TestCase):
    """Both detectors are exercised on synthetic sections.

    Without this, a detector that has stopped matching looks exactly like a clean body: the
    document tests below pass because no section carries a claim. Review of `eb3deed` replaced a
    boundary pattern with a never-matching one and the suite stayed green, which is the precedent
    this class follows.
    """

    HEADING = "## LOOM-1 — Remove portfolio content from the public engine\n\n"

    def claims(self, body: str, vocabulary: tuple[str, ...] | None = None) -> list[Claim]:
        return body_claims(self.HEADING + body, vocabulary)

    def kinds(self, body: str, vocabulary: tuple[str, ...] | None = None) -> set[str]:
        return {claim.kind for claim in self.claims(body, vocabulary)}

    def test_a_status_label_is_a_status_claim(self):
        shapes = {
            "bold label with a period": "**Status.** Reviewed.\n",
            "plain label with a colon": "Status: integrated\n",
            "bullet": "- Status: ready\n",
            "blockquote state": "> **State.** claimed\n",
            "label alone": "**Status.**\n",
            "table row": "| Status | Ready |\n",
        }
        for label, body in shapes.items():
            with self.subTest(shape=label):
                self.assertIn("status", self.kinds(body), f"{label} was not seen as a claim")

    def test_a_bare_status_word_is_a_status_claim(self):
        for body in ("Reviewed.\n", "**Ready.**\n", "- integrated\n", "accepted\n",
                     "in-progress.\n", "In progress\n"):
            with self.subTest(body=body):
                self.assertIn("status", self.kinds(body), f"{body!r} was not seen as a claim")

    def test_every_schema_status_is_recognised(self):
        vocabulary = status_vocabulary()
        self.assertIn("integrated", vocabulary)
        for word in vocabulary:
            with self.subTest(status=word):
                self.assertIn("status", self.kinds(f"{word}.\n", vocabulary),
                              f"{word} is a registry status but a bare line of it was not flagged")

    def test_prose_about_state_is_not_a_claim(self):
        body = (
            "Bodies live here; identity, state and claims live in the registry.\n"
            "The status of the tick is not recorded in this document.\n"
            "A claimed validator result is re-derived rather than copied.\n"
            "so the order of the tick's phases stops being load-bearing.\n"
        )
        self.assertEqual(self.claims(body), [], "prose that names state was read as a claim")

    def test_a_dependency_label_is_a_dependency_claim(self):
        shapes = {
            "bold label": "**Depends.** LOOM-5\n",
            "sentence": "Depends on LOOM-5 and LOOM-9.\n",
            "plural label": "Dependencies: LOOM-4\n",
            "blocked bullet": "- Blocked by LOOM-3\n",
            "inline after a status": "**Status.** Ready. Depends on LOOM-5.\n",
            "table row": "| Dependencies | LOOM-4 |\n",
        }
        for label, body in shapes.items():
            with self.subTest(shape=label):
                self.assertIn("dependency", self.kinds(body), f"{label} was not seen as a claim")

    def test_a_dependency_phrase_naming_a_task_is_a_claim(self):
        for body in ("This work depends on LOOM-8.\n",
                     "The handler is blocked by dutch-ref/W10-B.\n",
                     "It rests on nothing; see dependencies of LOOM-2.\n"):
            with self.subTest(body=body):
                self.assertIn("dependency", self.kinds(body), f"{body!r} was not seen as a claim")

    def test_a_bare_reference_is_not_a_dependency_claim(self):
        body = (
            "See LOOM-5 for how an attempt is identified.\n"
            "Commit cost therefore grows with registry size, not with LOOM-11's diff.\n"
            "The number is not a claim about LOOM-2.\n"
        )
        self.assertEqual(self.claims(body), [], "a reference with no relation was read as an edge")

    def test_prose_wrapped_onto_the_word_dependency_is_not_a_claim(self):
        body = (
            "A suite test fails on a synthetic section carrying a status claim or a\n"
            "dependency claim, and its detector is exercised on that fixture.\n"
            "Dependency edges live in the registry, not in this file.\n"
        )
        self.assertEqual(self.claims(body), [],
                         "a sentence that wrapped onto the word 'dependency' was read as an edge")

    def test_the_problem_statement_check_is_not_satisfied_by_acceptance_text(self):
        acceptance_only = [
            (1, "**Acceptance.** The regression is covered."),
            (2, "so a wrapped second line is still acceptance text."),
        ]
        self.assertEqual(problem_statement(acceptance_only), [],
                         "acceptance text was counted as a problem statement")
        self.assertTrue(problem_statement([(1, "The engine must fail.")] + acceptance_only),
                        "a section with real prose was read as having none")


# --------------------------------------------------------------------------
# The document carries no claim
# --------------------------------------------------------------------------


class WorkItemsBodyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = BODY.read_text(encoding="utf-8")
        cls.sections = sections(cls.text)

    def test_the_parse_sees_every_section(self):
        headings = [line for line in self.text.splitlines() if line.startswith("## ")]
        self.assertEqual(len(self.sections), len(headings),
                         "the section parser dropped a heading; the checks below would be partial")
        self.assertTrue(self.sections, "no `## ` section was parsed at all")

    def test_no_body_carries_a_status_claim(self):
        hits = [claim for claim in body_claims(self.text) if claim.kind == "status"]
        self.assertEqual([repr(hit) for hit in hits], [], (
            "a body carries a status; the registry owns it, so read it with `./loom show <task>` "
            "and let the validator compare the two instead of writing it twice"))

    def test_no_body_carries_a_dependency_claim(self):
        hits = [claim for claim in body_claims(self.text) if claim.kind == "dependency"]
        self.assertEqual([repr(hit) for hit in hits], [], (
            "a body carries a dependency edge; record it in the registry with `./loom dep add` so "
            "readiness is computed rather than asserted"))

    def test_every_section_keeps_its_problem_statement(self):
        for heading, lines in self.sections:
            with self.subTest(section=heading):
                self.assertTrue(problem_statement(lines),
                                f"{heading} has no problem statement before its acceptance")

    def test_every_acceptance_keeps_its_criteria(self):
        for heading, lines in self.sections:
            for index, (_, line) in enumerate(lines):
                if not line.strip().startswith("**Acceptance.**"):
                    continue
                inline = line.split("**Acceptance.**", 1)[-1].strip()
                following = [text for _, text in lines[index:] if text.strip()]
                with self.subTest(section=heading):
                    self.assertTrue(inline or len(following) > 1,
                                    f"{heading} states an acceptance with no criteria under it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
