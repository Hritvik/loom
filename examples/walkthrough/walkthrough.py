#!/usr/bin/env python3
"""A synthetic portfolio: two programs, three tasks, one complete cycle.

Run it to see the protocol behave end to end:

    python3 examples/walkthrough/walkthrough.py

It creates a throwaway database, so nothing here touches your real state. The point is to show the
four things the engine exists for: an exclusive claim, a dependency that gates work, an independent
review, and an audit log that proves what happened.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

LOOM = Path(__file__).resolve().parents[2]
TASK = LOOM / "capabilities" / "work" / "loom"

DB = Path(tempfile.mkdtemp(prefix="loom-walkthrough-")) / "work.db"
COMMIT = ""
AGENT_A = "example/alpha-agent"
AGENT_B = "example/beta-agent"
REVIEWER = "example/reviewer"


def step(number: int, title: str) -> None:
    print(f"\n\033[1m{number}. {title}\033[0m")


def run(*args: str, expect: int = 0) -> str:
    result = subprocess.run([sys.executable, str(TASK), "--db", str(DB), *args],
                            capture_output=True, text=True)
    if result.returncode != expect:
        raise SystemExit(f"command failed: {' '.join(args)}\n{result.stdout}{result.stderr}")
    return (result.stdout + result.stderr).strip()


def seed() -> None:
    """Register two program boards, the way an adopter does: `loom init`, then `loom board add`."""
    root = Path(tempfile.mkdtemp(prefix="loom-walkthrough-repo-"))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "README.md").write_text("# example repository\n")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Example",
                    "-c", "user.email=example@example.invalid",
                    "commit", "-qm", "example"], check=True)
    global COMMIT
    COMMIT = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    (root / "alpha.md").write_text("## W-1 — Build the first thing\n\n**Status.** ready\n\n"
                                   "## W-2 — Build the second thing\n\n**Status.** ready\n")
    (root / "beta.md").write_text("## W-1 — Depend on alpha\n\n**Status.** ready\n")
    print(run("init", "--id", "alpha.program", "--kind", "program",
              "--repo", str(root), "--path", "alpha.md", "--agent", AGENT_A))
    print(run("board", "add", "--board", "beta.program", "--kind", "program",
              "--role", "consumer", "--repo", str(root), "--path", "beta.md",
              "--agent", AGENT_A))


def main() -> int:
    seed()
    print(f"database: {DB}")

    step(1, "Author work centrally")
    run("create", "--agent", AGENT_A, "--id", "alpha/W-1", "--board", "alpha.program",
        "--title", "Build the first thing", "--status", "ready", "--write-path", "src/first")
    run("create", "--agent", AGENT_A, "--id", "alpha/W-2", "--board", "alpha.program",
        "--title", "Build the second thing", "--status", "ready", "--write-path", "src/second",
        "--depends-on", "alpha/W-1")
    run("create", "--agent", AGENT_B, "--id", "beta/W-1", "--board", "beta.program",
        "--title", "Depend on alpha", "--status", "ready", "--write-path", "src/beta",
        "--depends-on", "alpha/W-1")
    print(run("list", "--ready"))

    step(2, "Claim with a lease, against the revision you read")
    claimed = run("claim", "alpha/W-1", "--revision", "0", "--ttl", "45m", "--agent", AGENT_A)
    print(claimed)
    print(run("claim", "alpha/W-1", "--revision", "1", "--ttl", "45m", "--agent", AGENT_B,
              expect=2).splitlines()[-1])

    # The claim prints its fencing epoch; the holder must present it on every later mutation.
    epoch = claimed.rsplit("epoch ", 1)[-1].strip()

    step(3, "Move it through the implementation loop")
    print(run("transition", "alpha/W-1", "--revision", "1", "--epoch", epoch,
              "--to", "red", "--agent", AGENT_A))
    print(run("transition", "alpha/W-1", "--revision", "2", "--epoch", epoch,
              "--to", "green", "--agent", AGENT_A))

    step(4, "An independent reviewer, never a claimant")
    print(run("transition", "alpha/W-1", "--revision", "3", "--to", "reviewed",
              "--reviewer", AGENT_A, "--decision", "approve", "--agent", AGENT_B,
              expect=2).splitlines()[-1])
    asked = run("ask", "alpha/W-1", "--to", REVIEWER, "--agent", AGENT_A,
                "--question", "Review the submitted revision",
                "--review-source", COMMIT, "--review-evidence", "evidence/W-1.md")
    print(asked)
    seq = int(asked.rsplit("event ", 1)[-1].rstrip(")"))
    answered = run("answer", "alpha/W-1", "--question", str(seq), "--decision", "approve",
                   "--note", "Reproduced the checks from the pinned revision", "--agent", REVIEWER)
    print(answered)
    answer_seq = int(answered.rsplit("event ", 1)[-1].rstrip(")"))
    # The CLI names the addressee of an answer, which is the asker; the decision below is the
    # reviewer's, recorded under the reviewer's identity in the event log.
    print(f"  {REVIEWER} recorded approve (event {answer_seq})")
    print(run("transition", "alpha/W-1", "--revision", "3", "--epoch", epoch, "--to", "reviewed",
              "--reviewer", REVIEWER, "--decision", "approve", "--review-answer", str(answer_seq),
              "--agent", AGENT_B))

    step(5, "Evidence, then integration")
    print(run("transition", "alpha/W-1", "--revision", "4", "--epoch", epoch, "--to", "integrated",
              "--evidence", "evidence/W-1.md", "--integrated-source", COMMIT, "--agent", AGENT_A))

    step(6, "The dependency gate opens on its own")
    print(run("list", "--ready"))

    step(7, "The audit log proves the sequence")
    print(run("events", "--task", "alpha/W-1"))
    print(run("events", "--verify").splitlines()[-1])

    print("\n\033[1mDone.\033[0m The database is a throwaway; delete it or inspect it with sqlite3.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
