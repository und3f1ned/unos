---
status: accepted
date: 2026-02-05
area: process
decided-by: owner
---

# ADR-0007: UNOS is licensed under the GNU GPL version 3

## Context

UNOS started under the MIT license. On 2026-02-05 the owner changed the license to the GNU General Public License version 3 (commit ee00138, "Change license from MIT to GPL v3").

## Decision

- UNOS is licensed under the GNU General Public License, version 3 or, at the user's option, any later version, as the notice in `LICENSE` states. README.md names the license GPL-3.0-or-later.

## Consequences

- `LICENSE` holds the copyright line, the notice that grants version 3 or any later version, and, since 2026-11-07, the verbatim text of the GNU GPL version 3 (29 June 2007). Until then it had only the copyright line and the notice; the November 2026 audit had listed the missing license text among the places where the docs promised more than the repository had.
