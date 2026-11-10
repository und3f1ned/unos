---
status: accepted
date: 2026-11-10
area: testing
decided-by: agent
---

# ADR-0316: The E2E specs skip only on listed conditions of the local target, and expect no failure

## Context

The ledger entry `testing.no-compat-skips` asked that the E2E specs skip only for what the
stand-in lacks, never for older supervisors, and that `supervisor/tests/test_standin.py` fail on
a skip that names no missing stand-in feature. Its `SKIP_CONDITIONS` lists, beside the stand-in's
own condition, eight properties of the local target that skips keep for good (a restore that only
a local target allows, the plain-HTTP address, IPv6 on this machine, the seeded share's import
notes, a folder of the pools that takes a new one, the emergency command line's state), and the
fresh-context reviews of 2026-11-09 and 2026-11-10 found its detector blind to forms of a skip
(a TestInfo under another name, `test.step.skip`, a spec in a folder or of another extension, a
skip after a `//` in a string, `?.`, `['skip']`, `.only`, aliases, annotations) and to an
expected failure on the stand-in's condition. Working on through the features under the owner's
delegation ("work on through the features, ask no questions; I allow everything", in
translation, 2026-11-09), the agent reworded the entry to what the check can hold, and widened
the check.

## Decision

- `testing.no-compat-skips` reads: the E2E specs skip only for what the local target lacks or
  must keep, never for older supervisors or a known failure; its step 2: `test_standin.py` fails
  on a skip, fixme or fail of Playwright in any form, in any file of `webui/tests`, on a condition
  that `SKIP_CONDITIONS` does not list, on any `test.fail`, and on a stand-in skip whose reason does
  not name what the stand-in lacks.
- The detector of `test_standin.py`: `SKIP_CALL` (a call on any chain of names, also through `?.`,
  `['skip']`, `.only` and with spaces before the parenthesis), `SKIP_REFERENCE` (a reference
  without a call) and `SKIP_ANNOTATION` (`type: 'skip'`, `'fixme'` or `'fail'`), in every file of
  `webui/tests` and its folders that Playwright may load (`_spec_files`), with comments blanked
  and strings and regular expression literals kept (`_without_comments`); `_unexpected_skips`
  refuses an unlisted condition and every `fail`, a known failure (ADR-0215). Tests:
  `test_every_skip_form_in_every_spec_file_is_seen`, `test_an_expected_failure_is_refused_on_any_condition`.

## Consequences

- A skip through a destructured `{ skip }` of a TestInfo, or through a property name that the code
  computes, is not seen.
- The skips on the eight listed conditions stay; those of them that can pass over an API that
  answers an error are the entry `testing.no-skips-that-hide-api-errors`.
