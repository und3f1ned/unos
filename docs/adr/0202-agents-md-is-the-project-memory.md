---
status: accepted
date: 2026-11-07
area: process
decided-by: agent
---

# ADR-0202: AGENTS.md is the project memory, and CLAUDE.md only imports it

## Context

The repository's CLAUDE.md had grown into a mix of rules, a directory listing, an incomplete API list and the device's credentials, and a second, longer CLAUDE.md in the workspace folder around the repository repeated it in an older, partly contradicting form. Every line of a memory file costs context in every session, and prose bans block nothing (the book's Project Memory pattern and its Bloated Memory anti-pattern). Claude Code reads CLAUDE.md, which can import another file with a line `@AGENTS.md`.

## Decision

- `AGENTS.md` at the root is the project memory: what UNOS is, the commands with what a pass proves, the working loop, the decision rules, the hard rules with what enforces each, and where things live (ADR-0201). Nested files refine an area and are read before changing it: `supervisor/AGENTS.md`, `webui/AGENTS.md` and `deploy/AGENTS.md` (the last also covers every action on the dev device).
- `CLAUDE.md` contains only the line `@AGENTS.md`, so that Claude Code loads the same text as other agents. `supervisor/CLAUDE.md`, `webui/CLAUDE.md` and `deploy/CLAUDE.md` hold the same line, which imports the nested AGENTS.md beside them: Claude Code loads a subdirectory's CLAUDE.md when it reads files there, but no AGENTS.md by itself; other agents read the nested files because the root file tells them to.
- The CLAUDE.md in the workspace folder outside the repository becomes a short pointer to the repository, and sessions start in the root of a checkout, the repository or one of its feature worktrees (ADR-0210), where the checkout's settings and hooks apply (ADR-0211).
- None of these files holds a secret (ADR-0212).
- A rule that can be checked becomes a check (a test, a hook or CI), and AGENTS.md only names it with its ADR; the reasons live in ADRs, not in AGENTS.md.
- Corrections of how to work become edits of an AGENTS.md file, reviewed like code; agents keep no private project memory (auto memory is off, ADR-0211).

## Consequences

- One file to keep current, but it must stay lean: the monthly pass asks of every line which mistake it prevents (`docs/maintenance.md`, ADR-0218).
- Directory listings, API lists and environment variables are left to the code, which agents read when they need them.
- The device's credentials, once published in CLAUDE.md, remain in git history; rotating them is an owner action (ADR-0024, ADR-0212).

## Considered options

- Keep one large CLAUDE.md: it bloats, mixes reasons with rules and carried secrets.
- Only AGENTS.md without CLAUDE.md, as the book's own repository does: one file fewer, but whether Claude Code loads it then depends on its native support; the one-line import makes it explicit.
