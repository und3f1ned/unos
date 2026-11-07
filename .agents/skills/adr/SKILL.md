---
name: adr
description: Record a decision as an ADR in docs/adr (the next number, the template, supersession written into both files, a row in the README index) and check it with make check-fast. Use whenever the owner or an agent makes or changes a decision, however small, and when a proposed ADR is accepted or refused.
---

# Record a decision as an ADR

Every decision is recorded as a short ADR in `docs/adr/NNNN-slug.md`, not only the ones
that are hard to reverse (ADR-0001). A decision is never rewritten when it changes: a new
ADR supersedes it, and the old file keeps its text. The only exception, until the ADR's
first commit: an ADR written in the same uncommitted change as its decision, or a
proposed ADR that is being accepted with new evidence, is refined in place. A decision
already in force is superseded, even when its ADR is not committed yet.
`docs/adr/template.md` explains each field; `supervisor/tests/test_adrs.py` checks the
format, the numbering, supersession and the index.

## Steps

1. **Look for an ADR that already covers the decision.** `grep -ril '<keyword>' docs/adr/`
   and the table of its area in `docs/adr/README.md`. Result: the ADR that covers it, or
   none. When one does, the first case that fits applies:
   - The decision is unchanged and only a typo or a moved path is wrong: correct it in
     place and go to step 8.
   - The ADR is not committed yet (`git log --oneline -1 -- docs/adr/NNNN-slug.md` prints
     nothing) and it was written in the same change as its decision, which is not in force
     yet: refine it in place, keep its README row in step (step 6) and go to step 8.
   - A proposed ADR is accepted, as written or, before its first commit, refined with the
     new evidence that settled it (ADR-0162): it gets `status: accepted`, the day of the
     decision as its date and the same change in its row (steps 6 and 8).
   - Otherwise the decision changes (also one already in force whose ADR is not committed
     yet), or a proposal is refused or, after its first commit, decided otherwise: write a
     new ADR (steps 2 to 8) that supersedes it.

2. **Take the next number.** `ls docs/adr | grep -E '^[0-9]{4}-' | tail -n 1` shows the
   highest number; add one. Result: a four-digit number that no file has yet (numbers run
   from 0001 without gaps).

3. **Create the file.** `cp docs/adr/template.md docs/adr/NNNN-slug.md`, with a slug of a
   few lowercase words joined by hyphens that names the decision. Result: the file exists
   and its name matches `NNNN-[a-z0-9-]+.md`.

4. **Fill it in.** The front matter: `status` (accepted when in force, proposed when not
   yet settled), `date` (the day it was decided), `area` (the list is in the template),
   `decided-by` (owner or agent; agent also for the security decisions the owner delegated,
   ADR-0013). The title `# ADR-NNNN: <the decision as one sentence>` in sentence case.
   Context in one to four sentences; Decision with the names, values and repo paths;
   Consequences with the costs and what it rules out; Considered options only when they are
   worth remembering. Delete the template's comment and any line that does not apply.
   Write English and quote the owner in English, in double quotes (ADR-0200). Cite repo
   files, tests and ADR ids only: no paths outside the repository, no session notes, no
   chat. No secrets, and no step-by-step attack on a weakness that is still open
   (ADR-0213). Result: the ADR reads on its own to someone who never saw the session.

5. **Supersede in both files** (only when the new ADR replaces one). The new ADR gets
   `supersedes: [ADR-OLD]`. The old ADR gets `status: superseded`, a line
   `superseded-by: ADR-NEW` in its front matter and, as the first line of its Context,
   `Superseded by ADR-NEW: <what replaced it>.`; the rest of its text stays as it was.
   Result: each of the two files names the other in its front matter.

6. **Add the row to the index.** In `docs/adr/README.md`, in the table of the ADR's area
   and in number order: `| [NNNN](NNNN-slug.md) | <title> | <status> | <date> |`, with the
   title as in the file and the status `accepted` or `proposed`. A superseded ADR's status
   cell becomes `superseded by [NNNN](NNNN-slug.md)`, naming its successor. Result: the
   README lists the new ADR once, and every changed status cell matches its file.

7. **Point to it where the rule is applied.** A code comment, a test, `AGENTS.md` or a
   `feature-list.json` entry that relies on the decision names it as `ADR-NNNN` (not a
   session note or a chat message). Result: `git grep --untracked -n 'ADR-NNNN'` (which
   skips git-ignored files such as dev.env) finds the places that rely on it; the check of
   step 8 fails for an id that does not exist.

8. **Check.** `make check-fast`. Result: exit status 0. Otherwise `test_adrs.py` names
   the file, the line and the rule; for a missing README row it prints the row to add.

## Gotchas

- Commits happen only when the owner asks (ADR-0208); an ADR is written right away, not
  when the commit comes.
- Two worktrees can take the same number. The branch that merges later renumbers its ADR
  (the file name, the title line, the README row and every `ADR-NNNN` that cites it);
  `make check-fast` fails on a duplicate number or a gap.
- An ADR is never deleted, and its status is only ever proposed, accepted or superseded. A
  refused proposal is superseded by an ADR that records the refusal and its reason.
- One decision, one ADR. A few rules that only make sense together may share one ADR; a
  later change of one of them supersedes the whole ADR with a new one that restates the
  rules that stay.
