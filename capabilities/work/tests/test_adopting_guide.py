#!/usr/bin/env python3
"""The guide's §3 is the first thing a stranger executes, so the suite executes it.

Review request 1549 was refused because §3 could not be followed as written: it registered `alpha`
and then `beta` against board files it never told the adopter to create, and `loom board add`
refuses a missing one. `walkthrough.py` hid the gap by creating its board files first, so the
example passed while the guide failed.

These tests read the fenced `bash` blocks out of `## 3.` of `docs/adopting.md` and run them, in
order, in a throwaway HOME with a throwaway database. A guide is only verified as an instruction;
reading it as prose is what let this rot.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

LOOM = Path(__file__).resolve().parents[3]
GUIDE = LOOM / "docs" / "adopting.md"
SECTION = "## 3."


def section_three() -> str:
    """The text of the guide between the §3 heading and the next numbered section.

    Board headings (`## W-1 — ...`) sit inside the guide's own code blocks and also begin with
    `## `, so the end of the section is the next *number* heading, not the next heading.
    """
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(SECTION)
    rest = text[start + len(SECTION):]
    following = re.search(r"^## \d+\.", rest, re.MULTILINE)
    return rest[:following.start()] if following else rest


def section_three_bash() -> list[str]:
    return re.findall(r"```bash\n(.*?)```", section_three(), re.DOTALL)


class AdoptingGuideTest(unittest.TestCase):
    def test_section_3_states_the_commands_it_needs(self):
        blocks = section_three_bash()
        self.assertTrue(blocks, "§3 has no bash block to follow")
        script = "\n".join(blocks)
        self.assertIn("loom init", script, "§3 never initialises the registry")
        self.assertIn("loom board add", script, "§3 never registers a second board")
        self.assertIn("loom create", script, "§3 never creates a task")

    def test_the_guide_creates_each_board_file_before_registering_the_board(self):
        """The file precedes the board: `loom board add` refuses a missing path, `loom init` does not."""
        section = section_three()
        created = section.find("cat > ")
        initialized = section.find("loom init")
        added = section.find("loom board add")
        self.assertNotEqual(created, -1, "§3 never creates a board file for the adopter")
        self.assertLess(created, initialized, "§3 registers the first board before creating its file")
        self.assertLess(initialized, added)
        self.assertIn("work-items.md", section[created:added],
                      "§3 creates a file nobody registers")

    def test_the_guide_section_3_commands_run_as_written(self):
        script = "\n".join(section_three_bash())
        with tempfile.TemporaryDirectory(prefix="loom-guide-") as tmp:
            home = Path(tmp) / "home"
            (home / "repositories").mkdir(parents=True)
            (home / "repositories" / "loom").symlink_to(LOOM)
            env = {key: value for key, value in os.environ.items()
                   if key not in {"WORK_DB", "WORK_HOME", "WORK_CONTENT_HOME", "WORK_CONTRACTS"}}
            env.update(HOME=str(home), WORK_DB=str(Path(tmp) / "work.db"))

            result = subprocess.run(["bash", "-e", "-c", script], cwd=str(home), env=env,
                                    capture_output=True, text=True, timeout=120)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, f"§3 failed as written:\n{output}")

            self.assertIn("initialized alpha.program", output)
            self.assertIn("added board beta.program", output)
            self.assertRegex(output, r"alpha/W-1\s+ready", "§3's task does not appear in `loom list`")
            for name in ("alpha", "beta"):
                self.assertTrue(
                    (home / "repositories" / name / "docs" / "work-items.md").is_file(),
                    f"§3 left no board file for {name}")


if __name__ == "__main__":
    unittest.main()
