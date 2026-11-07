## What and why

<!-- One feature per pull request (ADR-0203). Name the feature-list.json entry and the ADRs this
change follows or adds. No secrets and no exploit details (ADR-0213). -->

Feature: <feature-list id>
ADRs:

## How it was verified

<!-- The verification levels are in docs/TESTING.md. -->

- [ ] `make check` passes
- [ ] `make e2e-local` passes (after a change to `webui/` or to an API the web UI calls)
- [ ] A host-level change (as `GLOSSARY.md` defines it): checked on the dev device and after a
      reboot, with the SD writes of each step reported (ADR-0018, ADR-0214)
- [ ] Reviewed in a fresh context (ADR-0017): each finding fixed, or recorded with the reason
- [ ] `feature-list.json`: `passes` turned true only after the scenario passed (ADR-0203)
- [ ] `PROGRESS.md` updated; every new decision has an ADR; new terms are in `GLOSSARY.md`
- [ ] The commit messages follow the commit convention (ADR-0209; CI's commits job checks them)

## Notes for the reviewer

<!-- Risks, follow-ups, what was left out on purpose. -->
