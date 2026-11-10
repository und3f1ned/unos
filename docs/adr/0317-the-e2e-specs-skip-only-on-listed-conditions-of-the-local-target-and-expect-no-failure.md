---
status: accepted
date: 2026-11-10
area: testing
decided-by: agent
---

# ADR-0317: The E2E specs skip only on listed conditions of the local target, and expect no failure

## Context

The ledger entry `testing.no-compat-skips` asked that the E2E specs skip only for what the
stand-in lacks, never for older supervisors, and that `supervisor/tests/test_standin.py` fail on
a skip that names no missing stand-in feature. Its `SKIP_CONDITIONS` lists, beside the stand-in's
own condition, eight properties of the local target that skips keep today (a target with
`E2E_STAND_IN=1`, a restore that only a local target allows, the plain-HTTP address, IPv6 on this
machine, the seeded share's import notes, a folder of the pools that takes a new one, the
emergency command line's state). Five fresh-context reviews on 2026-11-09 and 2026-11-10 found
the check's detector blind to one form of a skip after another: regular expressions over the
files' text, then a parser that looked for the forms one by one, and then one that took a set of
forms (members, destructuring, import aliases, strings) missed what each left out: a cast around
a key, a key or a declaration named `fixme`, an enum member or a class field named
`expectedStatus` (the field that `test.fail` sets), JSX, and `test.describe.skip`, which takes a
title, not a condition. Working on through the features under the owner's delegation ("work on
through the features, ask no questions; I allow everything", in translation, 2026-11-09), the
agent reworded the entry to what the check can hold, and made the check refuse every use of the
names, whatever its form.

## Decision

- `testing.no-compat-skips` reads: the E2E specs skip only for what the local target lacks or
  must keep, never for older supervisors or a known failure; its step 2: `test_standin.py` fails
  on every use of the names skip, fixme, fail and expectedStatus in a file of `webui/tests`
  (every name and every string of them: a member, a key, a declaration and an annotation's type
  among them) other than a skip of Playwright, such as `test.skip` or `testInfo.skip`, on a
  condition that it lists, and on a stand-in skip whose reason does not name what the stand-in
  lacks.
- `test_standin.py` reads every file of `webui/tests` and its folders that Playwright may load
  (`_spec_files`) with TypeScript's parser: `SKIP_SCAN`, run by node with
  `webui/node_modules/typescript`, reports every token whose text is one of the names, an
  identifier, a string, a template or a part of one. The member of a call (through `.`,
  `?.` or brackets, on any object, with parentheses, a non-null assertion or a cast around the
  object or the key, also through `.only`) gives its condition and reason, and a skip of
  `test.describe` or `test.step` gives `(a title)`; every other use says what it is
  (`NOT_A_CALL`: a reference, a name, an annotation, a string). A file that does not parse, or
  that holds JSX, fails the check. `_unexpected_skips` refuses all of them but a skip on a
  condition that `SKIP_CONDITIONS` lists, and names every `test.fixme`, `test.fail` and
  `expectedStatus` a known failure (ADR-0215); `_missing_reasons` checks the reasons. Tests:
  `test_every_skip_form_in_every_spec_file_is_seen`,
  `test_an_expected_failure_is_refused_on_any_condition`,
  `test_a_file_that_typescript_cannot_parse_fails_the_check`, `test_jsx_in_a_spec_fails_the_check`,
  `test_every_skip_gives_its_reason`, `test_a_skip_without_its_reason_is_refused`.

## Consequences

- No spec, fake or helper of `webui/tests` uses the names skip, fixme, fail or expectedStatus for
  anything else, not even a variable (the one of `health.spec.ts` is `listingFails`), and the
  specs use no JSX.
- A name that the code builds (`'sk' + 'ip'`), reads from data or runs from a string (`eval`) is
  not seen, nor is a module that a spec imports from outside `webui/tests` (none does).
- A listed condition is compared as text: what its names stand for (a constant that a spec
  defines) and a comment inside a condition or a reason are the review's, and a comment there
  makes the check refuse more, never less.
- Without node or without TypeScript in `webui/node_modules` (`make setup`), the checks of skips
  skip, as the other node checks of `test_standin.py` do (ADR-0215). On a workstation
  `make check` needs both for its frontend part anyway; the Unit Tests job of the CI workflow,
  off for now (ADR-0230), installs no packages of the web UI, so they would skip there.
- The listed conditions stay until `testing.no-skips-that-hide-api-errors` changes them: it takes
  up those that can pass over an API that answers an error, and its step 2 turns the skip on
  `notes.length === 0` into a failure.
