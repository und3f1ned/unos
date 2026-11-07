---
status: accepted
date: 2026-02-09
area: process
decided-by: owner
---

# ADR-0012: Code follows short written standards, and the UI uses only the shared components

## Context

By February 2026 the agents' instructions in CLAUDE.md had grown to 674 lines of rules and examples. On 2026-02-09 they were cut to 136 lines, keeping the rules and dropping the examples, which can be found in the source. The web UI's design system of shared components had existed since 2026-01-25.

## Decision

- Python (the supervisor): async/await for I/O, Pydantic models for request and response schemas, type hints, the JSON output of command-line tools (`lsblk -J`, `smartctl -j`), errors as `HTTPException`; anything that must change the host goes through `supervisor/src/host.py` (added in Phase 0, ADR-0020).
- TypeScript (the web UI): strict mode (`webui/tsconfig.json`), the API client in `$lib/api` wraps fetch and adds the token (ADR-0009), Svelte stores hold shared state, components live in `$lib/components`, and Svelte files use `<script lang="ts">`.
- The UI is built only from the components of `webui/src/lib/components/ui` (Button, Card, Badge, Input, Select, Modal, Alert, ProgressBar, EmptyState, StatusDot) instead of raw HTML elements, with the semantic color tokens (surface, content, status and icon tokens) instead of raw Tailwind colors, and with the page, card and nested-card classes of `webui/src/app.css`.
- The written rules stay short, and details are found in the code. They move from CLAUDE.md into the AGENTS.md files (ADR-0202): the Python rules into `supervisor/AGENTS.md`, the TypeScript and UI rules into `webui/AGENTS.md`; agents keep them.

## Consequences

- The rules are not followed everywhere yet: on 2026-11-07, 22 Svelte files outside `webui/src/lib/components/ui` still use a raw `<button>`, and the pages use many raw slate color classes (the audit's design-system bypasses, ADR-0023).
- Strict TypeScript is checked by svelte-check in `make check` (ADR-0216); the other standards are kept by review.

## Considered options

- Long instructions with full examples, as in the 674-line CLAUDE.md: dropped, because the examples repeated what the source already shows.
