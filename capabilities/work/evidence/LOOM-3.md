# Evidence — LOOM-3 adopting guide and worked example

Repository: `~/repositories/loom`
Status: green, awaiting independent review

## Delivered

- `docs/adopting.md`: install, environment, first board and task, the full loop, MCP wiring, and the
  walkthrough.
- `examples/walkthrough/walkthrough.py`: a throwaway database, two synthetic programs, three tasks,
  and one complete cycle — claim, red, green, independent review, integration, dependency release,
  chain verification. No portfolio instance appears in it.

## What the example found

Writing it exercised the protocol rather than describing it, and two flags behave in ways their
names do not suggest:

| Flag | Expectation | Reality |
|---|---|---|
| `--review-answer` | the question's sequence | the **answer** event's sequence |
| `--integrated-source` | optional | must be the exact commit the reviewer approved |

Both are documented in `docs/adopting.md`. Neither is a bug — the checks are correct — but a
first-time adopter would hit them exactly as the example did.

## Verification

- `python3 examples/walkthrough/walkthrough.py` completes and prints `event chain OK`.
- `python3 -m unittest discover -s tests` — 132 tests pass.
- `python3 validate_work.py` — PASS.

## Limits

- The walkthrough seeds boards through the store, because board registration has no CLI command.
  That is a real gap for adopters; it should become `loom board add`.
