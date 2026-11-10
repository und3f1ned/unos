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
own condition, eight properties of the local target that skips keep today (a target with
`E2E_STAND_IN=1`, a restore that only a local target allows, the plain-HTTP address, IPv6 on this
machine, the seeded share's import notes, a folder of the pools that takes a new one, the
emergency command line's state). The fresh-context reviews of 2026-11-09 and 2026-11-10 found
the check's detector, regular expressions over the files' text, blind to forms of a skip (a
TestInfo under another name, `test.step.skip`, a spec in a folder or of another extension, a skip
after a `//` in a string, `?.`, `['skip']`, `.only`, aliases, annotations, then a destructured
`{ skip }`, a template literal in brackets, a parenthesized `(test)`), to an expected failure on
the stand-in's condition, and liable to misread a division or a regular expression literal and so
hide the code after it. Working on through the features under the owner's delegation ("work on
through the features, ask no questions; I allow everything", in translation, 2026-11-09), the
agent reworded the entry to what the check can hold, and gave the check a parser.

## Decision

- `testing.no-compat-skips` reads: the E2E specs skip only for what the local target lacks or
  must keep, never for older supervisors or a known failure; its step 2: `test_standin.py` fails
  on a skip, fixme or fail of Playwright that a file of `webui/tests` names in any form (a call or
  a reference on any object, a destructured name, an annotation) on a condition that it does not
  list, on any `test.fixme` or `test.fail`, and on a stand-in skip whose reason does not name what
  the stand-in lacks.
- `test_standin.py` reads every file of `webui/tests` and its folders that Playwright may load
  (`_spec_files`) with TypeScript's parser: `SKIP_SCAN`, run by node with
  `webui/node_modules/typescript`, finds every member named `skip`, `fixme` or `fail` (through `.`,
  `?.` or brackets with a literal, on any object, also parenthesized, after a non-null assertion
  or in a cast, and `test.fail.only`): called, it gives its condition and reason, else it is a
  reference; it also finds such a name destructured, in a declaration or an assignment, and an
  annotation of that type. A file that does not parse fails the check. `_unexpected_skips`
  refuses an unlisted condition, which every reference, destructured name and annotation is, and
  every `test.fixme` and `test.fail`, the marks of a known failure (ADR-0215). Tests:
  `test_every_skip_form_in_every_spec_file_is_seen`,
  `test_an_expected_failure_is_refused_on_any_condition`,
  `test_a_file_that_typescript_cannot_parse_fails_the_check`.

## Consequences

- A method that the code reaches through a name it computes or passes as a value (`test[name]`,
  `Reflect.get(test, 'skip')`) is not seen.
- Without node or without TypeScript in `webui/node_modules` (`make setup`), the checks of skips
  skip, as the other node checks of `test_standin.py` do (ADR-0215); `make check` needs both for
  its frontend part anyway.
- The listed conditions stay until `testing.no-skips-that-hide-api-errors` changes them: it takes
  up those that can pass over an API that answers an error, and its step 2 turns the skip on
  `notes.length === 0` into a failure.
