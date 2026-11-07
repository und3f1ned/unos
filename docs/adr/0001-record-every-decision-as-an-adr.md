---
status: accepted
date: 2026-11-07
area: process
decided-by: owner
supersedes: [ADR-0060]
---

# ADR-0001: Every decision is recorded as a short ADR in docs/adr

## Context

Until 2026-11-07 the decisions behind UNOS were spread over session notes that disappear with the session, the owner's private audit notes, pull request bodies and the CHANGELOG (ADR-0060). Agents and the owner had no single durable record and had to know which note was current. On 2026-11-07 the owner asked: "Record all decisions in ADRs so there is no confusion, and convert the repository to follow the patterns of agentic development" (the book Agentic Coding Design Patterns, github.com/mokevnin/agentic-coding-design-patterns). That book advises an ADR for a choice that is hard to reverse and hard to understand without context, and the domain-modeling skill of the owner's mattpocock-skills plugin only for one that is, besides, the result of a real trade-off.

## Decision

- Every decision gets an ADR, not only the hard-to-reverse ones: architecture, security, product rules, ways of working, the use of the dev device, and decisions that are deferred (status `proposed`). This deliberately overrides the book's and the skill's advice, because the owner asked for all of them; the short format keeps that cheap.
- One file per decision: `docs/adr/NNNN-slug.md`, four-digit numbers in sequence from 0001, a slug of lowercase words and hyphens.
- Front matter: `status` (`proposed`, `accepted` or `superseded`), `date` (the day of the decision), `area` (process, architecture, security, auth, storage, shares, apps, backup, files, system, webui, deploy, device, testing or ci), `decided-by` (`owner` when the owner decided; `agent` when an agent did, including the security decisions the owner delegated in ADR-0013), an optional `supersedes` list, and `superseded-by` exactly when the status is `superseded`.
- The title line is `# ADR-NNNN: <title>` with the file's number. The sections are Context, Decision and Consequences, plus Considered options when the alternatives are worth remembering. A typical ADR is a few dozen lines.
- A changed decision gets a new ADR that supersedes the old one. The old file keeps its text; it only changes its status, gains `superseded-by` and one line that names its successor. Supersession is mutual.
- Only two kinds of ADR are refined in place, and only until their first commit: an ADR written in the same uncommitted change as its decision (this ADR and ADR-0200 to ADR-0223 of this conversion), and a proposed ADR that is being accepted with new evidence (ADR-0162). A decision that is already in force is superseded, also while its ADR is not committed yet (ADR-0051 by ADR-0223).
- The ways of working that Phase 0 and this conversion replaced are recorded as superseded ADRs that point to their successors: all development on the device (ADR-0003, by ADR-0016 in Phase 0), commits pushed to main from the device (ADR-0004, by ADR-0208), sshpass with the password on the command line (ADR-0005, by ADR-0212), decisions kept outside the repository (ADR-0060, by this ADR) and the device treated as a production NAS (ADR-0182, by ADR-0220).
- ADRs are self-contained and in English (ADR-0200): they cite repository files and tests, quote the owner in English translation, and contain no secrets, no session or scratch paths and no exploit details of weaknesses that are still open (ADR-0213).
- `docs/adr/README.md` lists every ADR by area with its status, `docs/adr/template.md` is the template, the adr skill (`.agents/skills/adr/SKILL.md`) is the procedure, and `supervisor/tests/test_adrs.py` enforces the names, the numbering, the front matter, the title lines and sections, mutual supersession, the index, that ADRs cite no scratch paths or scratch documents and hold no Cyrillic text, and that every ADR id cited in the repository exists.

## Consequences

- The record starts with about 220 ADRs, most of them short records of decisions taken before 2026-11-07, each dated by the day it was decided.
- The reason for a rule is found in one place, and code comments cite ADR ids instead of sections of notes that a reader of the repository cannot open.
- Writing the ADR is part of finishing any change that decides something; a choice that no ADR settles goes to the owner first.
- Every new ADR also changes the index, and a superseded rule stays readable in its own file.

## Considered options

- ADRs only for hard-to-reverse decisions, as the book's Domain Vocabulary pattern and the domain-modeling skill advise (the book counts an ADR for every trivial decision as an anti-pattern): not chosen, because the owner asked for every decision, and short records keep the cost low.
