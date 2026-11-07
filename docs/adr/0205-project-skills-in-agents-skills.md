---
status: accepted
date: 2026-11-07
area: process
decided-by: agent
---

# ADR-0205: Project skills live in .agents/skills, linked from .claude/skills

## Context

Recurring procedures (recording a decision, handing over a session, passing a feature, deploying and checking the dev device, running the E2E specs on the stand-in) lived in session notes and scratch scripts, or were retyped in prompts. A procedure in AGENTS.md costs context in every session; a skill loads only when it is used (the book's Skills pattern). Claude Code reads project skills from `.claude/skills/`; the book's own repository keeps its skills in `.agents/skills/`.

## Decision

- Project skills live in `.agents/skills/<name>/SKILL.md`, with front matter `name:` and `description:` (what the skill does and when to use it, one or two sentences) and steps that each end in a checkable result.
- Each has a relative symlink `.claude/skills/<name>` -> `../../.agents/skills/<name>`, so that Claude Code finds the same file.
- The skills are adr, handoff, feature-pass, deploy-device, device-checks and standin-e2e. The device and stand-in skills drive the versioned scripts of `scripts/dev/` and `scripts/standin/`.
- A skill whose steps change the dev device is not loaded by the model on its own: deploy-device sets `disable-model-invocation: true`, and its description says to use it only when the owner has asked for a deploy (ADR-0223; `test_only_the_owner_starts_a_deploy` in `supervisor/tests/test_dev_scripts.py`).
- Third-party skills (tdd, diagnosing-bugs, grilling, domain-modeling, code-review, writing-for-agents and others) come from the owner's user-scope plugin mattpocock-skills 1.2.3 and are not vendored into the repository.
- `supervisor/tests/test_repo_tooling.py` (`test_every_skill_has_its_front_matter_and_a_relative_claude_link`) checks every directory of `.agents/skills`: the front matter's `name` equals the directory, a description exists, `.claude/skills/<name>` is the relative link `../../.agents/skills/<name>`, and `.claude/skills` holds nothing else. `supervisor/tests/test_dev_scripts.py` also checks that every script the device skills name exists.

## Consequences

- A procedure is changed in one place, its skill, and AGENTS.md only names the skill.
- An agent without the owner's plugin lacks the third-party skills; the project skills do not depend on them.
- The monthly maintenance checks every skill's steps against the scripts it calls (`docs/maintenance.md`, ADR-0218).

## Considered options

- Vendoring the third-party skills into the repository: needs a network install, and one installation in the owner's user scope is enough.
