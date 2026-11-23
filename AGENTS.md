# AGENTS.md: UNOS project memory

UNOS (Universal NAS Open Software) turns an existing Debian or Ubuntu host into a NAS with a web
interface (ADR-0002), in two Docker containers: `unos-webui`, the SvelteKit app as static files
behind Caddy (HTTPS only), and `unos-supervisor`, FastAPI with SQLite, privileged, with
`pid: host` and the Docker socket. Storage, SMB, NFS exports, the network, power, the time zone,
the hostname and the firewall change the host through `supervisor/src/host.py` (ADR-0020) and the
logs read the host's journal through it, Docker apps go through the Docker socket; the system,
security, logs and NFS routers start no process of their own (ADR-0416). New host code uses
`host.py`. UNOS has one dev device and no other installations to keep working (ADR-0207).

Read the nested file before you change its area:
- `supervisor/AGENTS.md`: backend code and backend tests.
- `webui/AGENTS.md`: web UI code and the E2E specs.
- `deploy/AGENTS.md`: compose, Caddy and the install scripts, and every action on the dev device.

## Language

All text written into the repository is English: code, comments, docs, ADRs, skills, hook and
commit messages (ADR-0200); an ADR quotes the owner in translation. Answer the owner in Russian.
The one exception is the web UI's Russian texts and the tests that check them (ADR-0287).

## Start of a session

1. Start the session in the root of your checkout, the main one or a feature's worktree, so that
   its `.claude/settings.json` applies: auto memory off, secrets unreadable, the guard (ADR-0211).
2. Read `PROGRESS.md` (Now, Dev device), `git log --oneline -10` and `git status`.
3. One feature per worktree (ADR-0210): `git worktree add --no-track -b <id> ../unos-<id> <base>`,
   with `<base>` the branch that holds this AGENTS.md (`origin/main` once the conversion is merged,
   until then `origin/phase1-broken-flows`); the owner runs `make setup` in it. A worktree without
   `AGENTS.md` or `.claude/settings.json` is a stop: ask the owner. Do not start beside uncommitted
   work of another feature or person: ask the owner for a worktree. Only a workflow whose brief
   assigns the files shares a checkout.
4. Run `make smoke`, the baseline: the app starts and serves health, first-run setup and login.
   A red baseline goes to the owner before you change anything.
5. Take the feature from `feature-list.json`, or the handoff in `tmp/handoff/` the owner names.
   Done when you can state its id, its next step and what runs on the dev device.

## Commands (from the repository root; `make help` lists the targets)

| Command | A pass proves |
| --- | --- |
| `make check` | The gate (ADR-0216), which the CI workflow runs again on GitHub (ADR-0462): what its Unit Tests, Lint and Frontend jobs check. All backend tests, with the line coverage of `supervisor/src` at or above its floor (ADR-0442), mypy over `supervisor/src` with `mypy.ini` (ADR-0447), `ruff check` with the rules of `ruff.toml` (ADR-0425), `bash -n` and shellcheck with `.shellcheckrc` on the shell scripts (ADR-0448), JSON syntax, svelte-check, the checks of the web UI's texts (ADR-0287) and design system (ADR-0400), the production build and ESLint and Prettier's check of `webui/src` (ADR-0449); `make test`, `make lint` and `make frontend` run its parts. Linux-only tests skip on macOS. |
| `make check-fast` | Lint plus `make test-fast`: the convention tests of the ADRs, the ledger, the guard, the repository tooling, the dev scripts and the stand-in. The pre-commit hook runs it, and so does the Stop hook `.claude/hooks/stop-check.sh`, unless the working tree is the one in which `make check` or `make check-fast` last passed (ADR-0383): a failure sends you back to work. |
| `make smoke` | In a few seconds, through existing backend tests: the app starts and serves health, first-run setup and login. The baseline of a session. |
| `make e2e-local` | The Playwright specs pass against the local stand-in: the real supervisor and the built web UI on this machine. It runs once per batch, on the batch branch after the merges, and after a change to the shared components or the layout (ADR-0433); `scripts/standin/e2e.sh <spec> [args]` runs the specs of a feature's area. |
| `scripts/prune-merged-branches.py [--apply]` | At the end of a batch: the clean worktrees and the local branches of work merged into `origin/main` are gone (a dry run without `--apply`); the others, new ones without a commit included, are kept and named, and nothing changes on GitHub (ADR-0465). |
| `scripts/dev/linux-tests.sh [pytest args]` | The backend tests pass on Linux, in the dev device's RAM: the ones that skip on macOS. |
| `scripts/dev/check.sh` | The dev device is healthy: containers, Samba and network discovery (smbd, wsdd2, avahi-daemon; nmbd off), the API, the inbound IPv6 filter, the system disk's writes since boot, UNOS within its CPU budget and no thermal zone near its throttling point (ADR-0421). Read-only. |
| `scripts/dev/api.py METHOD PATH` | What the API on the dev device answers. Methods other than GET change the device. |
| `scripts/dev/deploy.sh status` | Which runtime files differ between this tree and the device. `status`, `sync --dry-run` and `backup` only read the device (ADR-0223); `backup` copies its secrets into the git-ignored `tmp/dev/backup` and keeps the newest 3 copies. |
| `scripts/dev/sd.sh measure <label> -- <command>` | How many MiB a command wrote to the device's system disk. |
| `scripts/dev/scenario.sh AREA` | The user scenario of an area (`files`, `volumes`, `smart`, `backups`, `smb`, `smbconf`, `shares`, `scrub`, `balance`, `offsite`, `restore`, `hardware`, `all`) works on the dev device, step by step with each step's SD MiB, and the check left the device as it found it (ADR-0304). It changes the device for its duration: on the owner's request. |
| `make setup`, `make audit` | Bootstrap and dependency audit. Both need the network: the owner runs them. |

Run the backend tests through make, never with pytest directly: `make test` and `make test-fast`
run them in parallel worker processes (pytest-xdist, `TEST_WORKERS`), each with temporary paths of
its own, so runs in several worktrees go at once, without a lock (ADR-0383).
Search with `git grep` or `rg`; the guard blocks `grep -r` over a folder that contains `dev.env`.

## Working loop

One feature per session, branch and pull request (ADR-0203).

1. Explore: the code, the ADRs of the area (`docs/adr/README.md` lists them by area) and the
   GLOSSARY.md terms you will use. Done when you can name the files to change and the ADRs that
   bind them.
2. Plan in a few lines. A choice that no ADR settles goes to the owner, and then into an ADR.
3. Write the test first and watch it fail on the current code (ADR-0186): backend tests in
   `supervisor/tests/`, the user flow in `webui/tests/`. From then on the test stands: weakening
   it to get green needs the owner's yes.
4. Make it pass. Done when `make check` exits 0 and, after a change to `webui/` or to an API the
   web UI calls, the E2E specs of its area pass: those of the pages and APIs it changes, plus
   every spec citing a test id, label or catalog key it changed (`scripts/standin/e2e.sh
   tests/<name>.spec.ts ...`). A change to `webui/src/lib/components/ui/`, `webui/src/app.css` or
   `webui/src/routes/+layout.svelte` runs the full `make e2e-local`, which otherwise runs once per
   batch, on the batch branch after the merges, before the deploy and before main (ADR-0433).
5. Review in a fresh context (ADR-0017): a reviewer without this session's history (a subagent,
   or a new session with mattpocock-skills:code-review) gets the diff, the ledger entry and its
   ADRs and tries to refute the change. Each finding is fixed, or recorded with the reason in
   the PROGRESS.md Log entry, before the feature passes.
6. A host-level change (GLOSSARY.md) is done only after the device check and a reboot check
   (ADR-0018). The owner starts the deploy with /deploy-device; then the device-checks skill
   runs the checks, and both report the SD MiB of every step (ADR-0214).
7. Record it in the same change: `passes` in `feature-list.json` turns true only after the scenario
   passed (the feature-pass skill); `PROGRESS.md` gets a dated Log entry and its Dev device state
   (ADR-0204); every decision gets an ADR (the adr skill, ADR-0001); new terms go into
   GLOSSARY.md; docs describe what the code does now (ADR-0061).
8. Commit and push when the owner asks (ADR-0208): Conventional Commits in English with a
   `Feature: <id>` trailer (ADR-0209), pushed to your branch. The owner opens the pull request.

Work left at the end of a session goes into a handoff (the handoff skill, `tmp/handoff/`). A
correction of how to work here becomes an edit of an AGENTS.md file, reviewed like code.

## Decision rules

- No backward compatibility (ADR-0207). UNOS has no older installations: remove old fields,
  endpoints, shims and upgrade notes, and update every caller (the web UI) in the same change. A
  legacy path you meet becomes a feature-list entry for its removal. Features for new
  installations, such as importing the smb.conf of a host installed by hand (ADR-0084), stay.
- The simplest implementation that fully meets the current requirements. Fully means the error
  paths, the role checks (ADR-0030), surviving a reboot (ADR-0018) and no needless writes to the
  system disk (ADR-0214).
- Fix the cause, not the symptom. Name the cause first, look at your own side before the owner's
  or the device's (ADR-0057), and prove the fix with a test that failed without it (ADR-0186).
- Propose a refactoring when it serves the task. The owner sets its scope, and it gets its own
  feature-list entry and pull request (ADR-0203).

## Hard rules

Each rule names its decision and what enforces it. The guard checks the commands of Bash and of
the desktop app's terminal tool, and this project denies the Desktop Commander tools (ADR-0211).
A denial by the guard or the permission system is an answer: ask the owner how to go on.

- The dev device runs from its eMMC, a soldered chip that wears with every write, and its SD card
  is wiped and unused (ADR-0214, ADR-0470), and Docker keeps its data on the pool (ADR-0486). Reach the device only through `scripts/dev/`
  (ADR-0016), scratch files in its `/dev/shm`. `deploy.sh`, `linux-tests.sh`, `ipv6-filter.sh`
  (install, remove) and `scenario.sh` hold the device lock (ADR-0210) and print `SD writes`, the
  system disk's writes; `sd.sh measure` measures any command. Builds, `up` and `rollback` go through `deploy.sh`, and
  the scenario checks, which make temporary things and remove them, through `scenario.sh`, both on
  the owner's request (ADR-0223, ADR-0304). Container runs, image pulls, prunes, package installs and reboots on the device are
  owner actions (ADR-0211). Enforced by the guard, which also blocks disk-wiping tools everywhere
  (`supervisor/tests/test_agent_guard.py`).
- Secrets live in `dev.env`, read only by the scripts (ADR-0212), and `tmp/dev/backup` holds the
  device's JWT secret, password hashes and database, read only by `deploy.sh rollback` (ADR-0223).
  Neither is ever shown or extracted, the main checkout's copy from a worktree included. Passwords
  stay off command lines and out of the repository, handoffs and logs. Credentials and the device's
  security settings are owner actions (ADR-0024). Enforced by the deny rules of
  `.claude/settings.json`, the guard, `.gitignore`, the mode checks and the secret scan of
  `make test-fast` (`supervisor/tests/test_dev_scripts.py`, `test_repo_tooling.py`).
- E2E specs run only against local targets: the stand-in (`make e2e-local`, the default) or the
  compose test stack, never the dev device (ADR-0215). Enforced by `webui/playwright.config.ts`
  through `webui/tests/target.ts`, and by the guard.
- You push only your own branch; main and master belong to the owner, and pushed history is never
  rewritten (ADR-0208). Enforced by the guard (pushes to main or master, force pushes,
  `--no-verify`, a green run recorded by hand, ADR-0383); commit messages by the commit-msg hook
  (ADR-0209).
- A command run in the background (the Bash tool's `run_in_background`) is one call of `timeout`
  with a deadline of at most 2 hours, such as `timeout 1800 make check > <log> 2>&1`, with loops
  and pipelines inside `timeout <seconds> bash -c '...'`: the session wakes only when it ends,
  and the Bash tool runs zsh (ADR-0335). Rewriting a refused command that way is the answer to
  this denial. A session does not end its turn waiting only on a background command while other
  work remains. Enforced by the guard's background-deadline rule
  (`supervisor/tests/test_agent_guard.py`); no tool checks the turn.
- Packages come from `make setup`, which the owner runs. The workstation's network filter blocks
  agents: report a blocked request to the owner as it is (ADR-0211).
- Security audit notes and exploit details stay in the owner's private audit folder (ADR-0213).
  ADRs, commits and issues state decisions and reasons. Enforced by review.

## Where things live (one fact, one home: ADR-0201)

| What | Where |
| --- | --- |
| How to work | the AGENTS.md files, CONTRIBUTING.md |
| Why: every decision | `docs/adr/` (index by area in `docs/adr/README.md`; new ones with the adr skill) |
| The project's words | GLOSSARY.md (the domain-modeling skill calls this file CONTEXT.md) |
| Where work stands, the dev device included | PROGRESS.md |
| What to build next | `feature-list.json` |
| A handoff between sessions | `tmp/handoff/` (git-ignored) |
| Invariants | `.claude/hooks/`, `scripts/git-hooks/`, `supervisor/tests/`, `.github/workflows/` |
| Procedures | `.agents/skills/<name>/SKILL.md`, linked from `.claude/skills/`: adr, handoff, feature-pass, deploy-device, device-checks, standin-e2e |
| Device settings and secrets | `dev.env` (git-ignored, mode 600; template `dev.env.example`) |
| Tests and CI | `docs/TESTING.md` |
| Users and operators | README.md, `docs/INSTALL.md`, `docs/USER_GUIDE.md`, CHANGELOG.md |
| Maintenance calendar | `docs/maintenance.md` |

Third-party skills (tdd, diagnosing-bugs, grilling, code-review, writing-for-agents and others)
come from the owner's user-scope plugin mattpocock-skills, not from the repository (ADR-0205).
