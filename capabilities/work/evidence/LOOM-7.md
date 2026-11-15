# Evidence — LOOM-7 bind an integration receipt to the content it approved

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-7` (worktree `/Users/hritvikdinkar/.local/state/loom/worktrees/loom-loom-7`)
Status: green, submitted for independent review

## Goal

A receipt records the paths and blobs it covered, so integration verifies exact content
instead of re-diffing a history that may have moved.

## What changed

- `engine.py`
  - `approved_content` computes the paths the approved commit changed (base..source, or the
    root diff) and the object each path resolves to at the source; `content_digest` hashes the
    sorted path/blob pairs. `verified_review` records both under `review.paths` and
    `review.digest`, and the `reviewed` transition event carries `review_digest` and
    `review_path_count`.
  - `require_reviewed_source` now consumes the receipt: it checks the recorded digest against
    the pairs, verifies each recorded blob at the integration tip (`blob_at` / `diverged_content`)
    and fails with the exact path list. It no longer re-diffs `base..source` at integration time.
  - A receipt with no digest is refused as incomplete; it is never silently repaired.
  - The transition to `integrated` records `approved_digest` and `approved_path_count`, so the
    integration event is bound to the content it approved.
  - `annotate --base` records `approved_paths` and `approved_digest` in the receipt and rejects
    moves with the same path list; without `--base` the digest stays null.
  - `plan_integration` classifies a legacy receipt as `receipt_incomplete` before any git work;
    `loom show`, `loom tick`, and `loom integration-plan` report it. `paths_diverged` reporting
    reads the receipt's pairs rather than recomputing them from history.
- `schemas/task.schema.json`: `review.paths` is an array of `{path, blob}` (blob may be null for
  a deletion) and `review.digest` is a nullable SHA-256.
- `validate_work.py`: the reviewed transition must carry the receipt's digest, the digest must
  recompute over the recorded pairs, and a `reviewed` receipt without a digest is reported as
  incomplete.
- `linear_projection.py`: a receipt with no digest lands in `needs_review`, not
  `awaiting_integration`.
- `README.md`, `PROTOCOL.md`, `AGENT-GUIDE.md`: the receipt contract, the exact-path failure, and
  the legacy/incomplete rule.
- Tests: `tests/test_work.py` covers the recorded pairs and digest, the exact moved-path error,
  a rename verifying by blob (deletion recorded as null), a legacy receipt refused and reported
  incomplete by transition/tick/validator, and a hand-edited digest failing validation.
  `tests/test_linear_projection.py` covers the legacy lane.

## Decision points

- **Digest over path and blob, sorted by path.** Deletions are recorded with a null blob, so a
  path being deleted at the source counts as approved content rather than as an absent entry.
  Object ids (not file contents) are hashed, which covers every file type including gitlinks
  without loading blobs.
- **The receipt is authoritative.** Integration reads `review.paths`; it never recomputes the
  approved range. That is what makes the check stable when history moves and why a legacy
  receipt cannot be integrated without a new review.
- **Legacy receipts stay null.** They are reported incomplete by `show`, `tick`
  (`receipt_incomplete`), `integration-plan` and the validator; the repair is a re-review, not an
  inferred digest.

## Verification

- `python3 -m unittest discover -s tests` — 227 tests, all pass.
- `python3 validate_work.py` — PASS. It now also reports the live registry's legacy receipts:
  `23 reviewed receipt(s) record no approved content digest and are incomplete`.
- `python3 loom show loom/LOOM-1` (a legacy reviewed task) prints
  `content digest: none (incomplete)`.
- `python3 loom integration-plan --out <tmp>` emits the new
  `Receipt incomplete (legacy)` bucket and the per-task reopen commands.

## Limits

- The 23 legacy reviewed receipts need a reopen and a fresh review; this change deliberately does
  not compute their digests. The same applies to the 24 pre-migration `annotate` receipts written
  without `--base`, which keep a null digest and are labelled incomplete in `loom show`.
- The digest binds path content, not file modes. A mode-only change does not fail integration; it
  is not content.
- As before, the engine proves the commit is in the local integration branch's history. It still
  does not establish that the commit reached a remote.
