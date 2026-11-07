---
status: proposed
date: YYYY-MM-DD
area: AREA
decided-by: OWNER-OR-AGENT
supersedes: [ADR-NNNN]
---

<!--
How to fill in this template; the adr skill (.agents/skills/adr/SKILL.md) has the steps.
Copy it to docs/adr/NNNN-slug.md, replace every placeholder and delete this comment.

status         proposed: written down, not yet settled. accepted: in force.
               superseded: replaced by a later ADR. A new ADR starts as superseded only
               when it records a way of working that is already replaced.
date           The day the decision was made (YYYY-MM-DD), not the day it was written down.
area           One of: process, architecture, security, auth, storage, shares, apps,
               backup, files, system, webui, deploy, device, testing, ci. The area
               decides the ADR's table in docs/adr/README.md.
decided-by     owner when the owner decided; agent when an agent did, including the
               security decisions the owner delegated (ADR-0013).
supersedes     The ADRs this one replaces, as [ADR-0012, ADR-0013]; delete the line when
               there are none. Each of them then gets status: superseded and
               superseded-by: this ADR, and one line at the top of its Context.
superseded-by  Never written by hand into a new ADR: a later ADR that replaces this one
               adds it (one id, superseded-by: ADR-NNNN) together with status: superseded.
Title          # ADR-NNNN: the decision as one sentence in sentence case, without a final
               period. NNNN is the number of the file name.

Write in English; quote the owner in English, in double quotes (ADR-0200). Keep the ADR
self-contained: cite repo files, tests and other ADRs, never paths outside the
repository, session notes or chat. No secrets, and no step-by-step attack on a weakness
that is still open (ADR-0213). Typical length: 12 to 45 lines; an ADR that bundles a few
closely related rules may be longer.
-->

# ADR-NNNN: The decision as one sentence in sentence case

## Context

One to four sentences: the problem and the forces behind the decision. A superseded ADR
starts this section (or Consequences) with one line: "Superseded by ADR-NNNN: <what
replaced it>."

## Decision

What was decided, concretely: names, values and files (repo paths such as
`supervisor/src/shares/smbconf.py`), and the tests that check it.

## Consequences

What follows from it, including the costs and what it rules out.

## Considered options

- Another option: why it was not chosen. (Keep this section only when the alternatives are
  worth remembering; otherwise delete it.)
