---
name: handoff
description: Write a handoff document for a named next goal to tmp/handoff/, so that a fresh session continues from it. Use when the owner asks for a handoff, or when a session has to stop before its goal is reached.
---

# Handoff to the next session

A handoff carries one move from this session to the next (ADR-0204): the next session reads it
first and follows its links. It lives in `tmp/handoff/`, which git ignores, because it goes stale
within days. What has to last goes to its own home first (ADR-0201): the state of the work to
PROGRESS.md, feature states to `feature-list.json`, decisions to `docs/adr/`.

## Steps

1. **Name the next goal.** One sentence that says what the next session does and how it knows it
   is done, such as "Make `security.append-only-audit-log` pass" or "Find why the restore tests
   fail on Linux". Take it from the owner's words, and ask when there are none. Result: the goal
   sentence, and a slug of two to five lowercase words from it.

2. **Put the lasting facts in their homes.** Update PROGRESS.md (Now; Dev device when the device
   changed; a dated Log entry for what was done), change `passes` only as the feature-pass skill
   allows, and record each decision of this session with the adr skill. Result:
   `git status --short` lists those files, or none of them needed a change.

3. **Collect the state.** Run `git status --short`, `git log --oneline -5` and `git diff --stat`;
   after device work also `scripts/dev/deploy.sh status`. Result: the branch, the last commit, the
   uncommitted files and what runs on the device, taken from this session's output.

4. **Write the document** `tmp/handoff/YYYY-MM-DD-<slug>.md` (today's date; add `-2` when the
   name is taken), with every section filled in or saying "none":

   ```markdown
   # Handoff: <goal>

   ## Goal
   <the goal sentence, and the feature-list.json id if there is one>

   ## State
   <worktree and branch, last commit, uncommitted files, what runs on the dev device>

   ## Decisions
   - <decision>: ADR-NNNN (docs/adr/NNNN-<slug>.md)

   ## Files touched
   - <path>: <what changed and why, in one line>

   ## Commands that verify
   - `<command>`: <the result that shows it works>

   ## Open questions
   - <question>, for <the owner or the next session>

   ## What not to do
   - <an approach that was tried and dropped, and why>; <what to do instead>
   - <an action that is the owner's, such as a deploy, a reboot or a push>

   ## Suggested skills
   - <feature-pass, standin-e2e, device-checks or another, and why>
   ```

   Link the lasting documents (ADRs, PROGRESS.md, commits, issues) instead of copying them. A
   decision without an ADR does not belong here: record it first (step 2). Result: the file
   exists, and every section is filled in or says "none".

5. **Keep secrets and foreign paths out.** Name a variable of `dev.env` instead of its value, and
   write no password, token, key or address of the device, and no path outside the repository.
   Then check the file through the redaction filter of `scripts/dev/api.py`, so that the check
   itself never prints a secret:

   ```bash
   (scripts/dev/api.py --redact || echo 'CHECK FAILED') < tmp/handoff/<file> \
     | grep -nE '<redacted>|CHECK FAILED|PRIVATE KEY|/private/tmp|/Users/|/home/'
   ```

   Before grep sees a line, the filter replaces with `<redacted>` the values of dev.env's
   secrets wherever they occur, JWTs, and the value of every `NAME=value` or `name: value` whose
   name ends in pass, password, passwd, passphrase, secret, token, API key or private key, in any
   case.
   Result: no output. A printed line shows `<redacted>` where a secret was, a private key's first
   line, or a foreign path: rewrite that line and check again. `CHECK FAILED` means the filter
   could not read dev.env and says why (`make setup` creates it).

6. **Tell the owner how to start the next session.** End your last message with the line
   `Read tmp/handoff/<file> and continue from its Goal.` Result: the line, with the file's path,
   is in your message.

## The next session

It starts in the root of the same checkout or worktree, whose `tmp/handoff/` holds the file,
reads the handoff, then PROGRESS.md and `git log --oneline -10`, and runs the baseline
`make smoke`, as AGENTS.md says. Where the handoff and the repository disagree, the repository
wins: check the state before acting on the handoff. Once the goal is reached, delete the handoff.
