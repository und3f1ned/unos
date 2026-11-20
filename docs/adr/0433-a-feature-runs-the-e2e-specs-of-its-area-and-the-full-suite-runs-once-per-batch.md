---
status: accepted
date: 2026-11-20
area: testing
decided-by: owner
---

# ADR-0433: A feature runs the E2E specs of its area, and the full suite runs once per batch

## Context

Every change to `webui/` or to an API the web UI calls ran the whole Playwright suite
(`make e2e-local`, about 7 minutes) before it was done, so a batch of front-end features ran it
once per feature and again after each merge. Asked why every front-end task runs the full E2E
suite, the owner agreed to run per feature only the specs that the change can break: "Yes, like
that".

## Decision

- A feature that changes `webui/` or an API the web UI calls runs the E2E specs of its area
  before it is done: the specs of the pages and the APIs it changes, plus every spec that cites
  a test id, a label or a catalog key (`webui/src/lib/i18n/`) that it changed
  (`git grep` in `webui/tests/`). They run through `scripts/standin/e2e.sh tests/<name>.spec.ts
  ...` (ADR-0215) and must pass with nothing failed or flaky.
- A change to the shared components or the layout (`webui/src/lib/components/ui/`,
  `webui/src/app.css`, `webui/src/routes/+layout.svelte`) reaches every page, so it still runs
  the full suite, `make e2e-local`.
- The full suite runs once per batch: on the batch branch, after its feature branches are
  merged, before the deploy and before the batch reaches main. A failure there is fixed on the
  batch branch before either.
- `AGENTS.md` (the working loop, step 4), `.agents/skills/feature-pass/SKILL.md` (step 6),
  `.agents/skills/standin-e2e/SKILL.md` (step 3), `webui/AGENTS.md` and `docs/TESTING.md`
  (level 2) state the rule.

## Consequences

- A front-end feature is done minutes sooner, and a batch runs the full suite once instead of
  once per feature and per merge.
- A break in a spec outside the feature's area (a shared selector or a flow that crosses pages)
  is found only by the batch run, after the merges; it is then fixed on the batch branch, where
  more changes stand between it and its cause.
- Choosing the specs is the author's judgement; a doubt about a spec runs it.
