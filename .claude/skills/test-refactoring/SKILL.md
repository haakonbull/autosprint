---
name: test-refactoring
description: Consolidate a test suite by deleting overlap, parametrising clones, and pruning dead coverage — never at the cost of losing a line of coverage. Use when user asks to refactor tests, deduplicate tests, or mentions test bloat, test consolidation, or "too many tests".
---

Refactor the marked tests (or the current test file if nothing is marked) following these rules. **This skill is distinct from `python-refactoring`** because the dominant operation here is **deletion** — not restructure, not rename — and deletion in tests has a failure mode that restructuring in production code doesn't: silently losing coverage.

## The load-bearing invariant

**Coverage must not decrease.** If any executable line that was covered before is uncovered after, the refactor has failed, regardless of how many tests you consolidated or how clean the new code looks. This is not a soft preference — it's the only thing separating "good test consolidation" from "I deleted tests that were actually guarding something."

## Preflight

Before touching any test, establish a baseline:

1. **Run `pytest --cov=<pkg> --cov-report=json -m "not slow"`** (substitute the project's real package path and marker). Save the resulting `coverage.json` to a temp location.
2. **Note the passing test count.** It must match exactly at the end.
3. **If coverage cannot be measured** (project has no coverage tooling configured, or `--cov` fails), STOP and tell the user. Do not refactor tests in a project where coverage drift can't be verified — the invariant is untestable, and you're gambling.

## Rules for deleting a test

A test may be deleted **only if** all of these hold:

1. **A surviving test subsumes its coverage.** You must name the surviving test by file + function name in the RESULT summary (e.g. `"deleted test_foo::test_basic_case (subsumed by test_foo::test_parametrised_cases)"`). Vague claims like "the smoke tests cover this" are not acceptable — name the test.
2. **The surviving test asserts the same outcome**, not just exercises the same code path. Two tests that both call `build_report()` but one checks the manifest and the other checks the filesystem layout are NOT redundant — they guard different contracts.
3. **Same-fixture is not evidence of overlap.** Two tests using the same `tmp_path` fixture can still be guarding entirely different invariants. Overlap must be proven at the *assertion* level.
4. **The test is not the sole guard on a branch.** If deleting it would drop coverage on any line, it stays — even if it looks cosmetically similar to another test. The coverage report is the final arbiter.

## Rules for parametrising tests

When you find N tests that only differ in input values, convert them to `@pytest.mark.parametrize`:

1. **The assertion shape must be identical across cases.** If test-A asserts `len(result) == 3` and test-B asserts `result[0] == "x"`, they are NOT parametrisable — they're testing different contracts. Leave them.
2. **Error-path and happy-path cases should stay separated.** A parametrised test that mixes `(input, expected)` with `(bad_input, pytest.raises(ValueError))` is harder to read than two tests; don't force them into one.
3. **Each parametrised case needs a distinct `id=`** so a failure line in pytest output points at the exact case. Anonymous cases (`parametrize("x", [1, 2, 3])` with no ids) are fine for simple types; named cases (`id="negative"`, `id="zero"`, `id="very_large"`) are required when the semantics differ meaningfully.
4. **Don't parametrise to save lines at the cost of readability.** If the parametrised version is denser than the three originals, keep the originals.

## Rules for cloned smoke tests

"Smoke real <module>" tests are the most common source of bloat (sprints 44/45/47/48 in the ds-sit-test run were four clones of the same shape). Treatment:

1. **First, verify they're clones.** Read all N. If the assertion shape is identical and only the module under test differs, proceed. If not, treat them as distinct tests — do not merge.
2. **Prefer parametrising the module-under-test** as the parameter dimension, with a shared assertion block. Each module becomes one `pytest.param(module_name)` entry.
3. **Do not collapse smoke tests that hit different error paths.** A smoke test that exercises the happy path and another that exercises the "no winner" path are not clones — the user's failure mode is that we break the unhappy-path contract while the happy-path test still passes.

## Never-touch list

The following tests are off-limits to this skill:

1. **Tests marked `@pytest.mark.regression`** — they exist to pin a specific past bug. Deleting one means a specific regression is no longer guarded.
2. **Tests that appear in `tests/test_<pkg>.py::test_<specific_bug_name>`** where the name references an ADR, an issue number, or a dated incident. These are regression guards even without the marker.
3. **Tests that import from `conftest.py` fixtures not used anywhere else** — the fixture was probably built for this test. Deleting both the test and the fixture is a separate, bigger refactor; skip for now.
4. **The only test exercising a branch in the coverage report.** No matter how ugly it is.

## Output

End your response with a `---RESULT---` block. Required format:

```text
---RESULT---
{"status": "success", "summary": "Consolidated <N> tests → <M>: deleted test_foo::test_a (subsumed by test_foo::test_parametrised); parametrised test_bar's 4 cases into one. Coverage: <before>% → <after>% (no lines regressed)."}
---END---
```

Or on failure (coverage dropped, a test couldn't be unified safely, or a surviving test broke):

```text
---RESULT---
{"status": "failure", "reason": "Coverage dropped: ds_sit_test/foo.py:123 was covered before, uncovered after (deleted test_foo::test_b removed the only call). Reverting."}
---END---
```

The summary must:

- Name specific files and test functions that changed.
- Quote the before/after coverage percentages.
- Fit in ≤120 chars (same hard limit as the implement skill — git commit body).

## What this skill is NOT

- **Not a refactor for production code.** Use `python-refactoring` for that. Tests have genuinely different hygiene rules (deletion is the primary operation; coverage is the invariant).
- **Not a license to ship fewer tests because "there are too many."** Test count is a symptom to investigate, not a metric to minimise. A project with 1200 tests for 38k LOC is not automatically bloated — check overlap, not count.
- **Not an opportunity to rewrite tests for style.** Drive-by reformatting a test while consolidating its neighbour mixes two concerns and makes the diff unreviewable. Style changes go in a separate sprint.

## If in doubt

Don't delete. A failed test-refactoring sprint that deletes real guards is much worse than a succeeded sprint that left duplication in place. When the rules above don't clearly authorise a deletion, leave the test.
