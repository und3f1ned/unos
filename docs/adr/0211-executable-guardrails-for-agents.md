---
status: accepted
date: 2026-11-07
area: process
decided-by: agent
---

# ADR-0211: A committed settings file and a PreToolUse guard block risky agent commands

## Context

Agents run shell commands on the owner's workstation and, through SSH, on the dev device, whose system disk cannot be replaced (ADR-0214). Rules written only in AGENTS.md guide but block nothing, and the workspace's personal settings allowed whole command families. Besides the Bash tool, the owner's sessions have other tools that run commands or read files: the desktop app's terminal tool, which runs a line in the owner's login shell, and the Desktop Commander plugin. Knowledge kept in an agent's private auto memory is invisible to other sessions and to review (the book's Executable Guardrails pattern and its Private Memory anti-pattern).

## Decision

- The committed `.claude/settings.json` turns auto memory off (`autoMemoryEnabled: false`). It denies reading every `dev.env` and the deploy's device backups (ADR-0212): `Read(./dev.env)` and `Read(./**/dev.env)`, and by absolute path, which also covers a linked worktree's main checkout, `Read(//**/dev.env)` and `Read(//**/tmp/dev/backup/**)`. It denies every tool of the Desktop Commander server (`mcp__plugin_desktop-commander_desktop-commander`), whose commands and file reads would pass the other rules. It runs the PreToolUse hook `.claude/hooks/guard.py` on every command of the Bash tool and of the desktop app's terminal tool (`mcp__terminal__run_in_terminal`, whose `tool_input.command` is checked like a Bash command), and the Stop hook of ADR-0216. Sessions start in the root of a checkout, the repository or a feature worktree, so that it applies (ADR-0202, ADR-0210).
- The guard reads the hook's JSON on standard input, splits the command line into simple commands, follows wrappers (sudo, env, timeout, xargs, `bash -c`, `sh -c`, eval and others) and treats the remote command of ssh, `scripts/dev/ssh.sh` and `dev.sh` as a command on the device. It blocks a command (exit code 2, one line on standard error that names the rule and what to do instead) by these rules:
  - push-main: a push to main or master, with `--all` too, or without a refspec when git's configuration sends it there (the refspecs of `remote.<name>.push`, `push.default` matching, or the branch that `@{push}` names, such as main for a branch made from origin/main without `--no-track` under `push.default` upstream; git is asked in the command's directory, with the command's `git -c` settings), and `gh pr merge` (ADR-0208);
  - device-push: a git push on the dev device (ADR-0053);
  - force-push: `--force`, `--force-with-lease`, a `+refspec`, `--mirror`;
  - delete-branch: `--delete`, a `:branch` refspec, `--prune`;
  - skip-hooks: `--no-verify`, `git commit -n`, another `core.hooksPath` (ADR-0209);
  - e2e-target: Playwright with a `BASE_URL` or `HTTP_URL` that is not local, and Playwright on the device (ADR-0215);
  - disk-wipe, anywhere: mkfs, `wipefs -a`, dd or a redirection to a disk, blkdiscard, sgdisk, sfdisk, fdisk, parted, shred on a disk, `cryptsetup luksFormat`, `btrfs device remove` or `delete`, and the pool create and delete calls of `scripts/dev/api.py`;
  - device-docker, on the device: docker build, pull, run or create, `docker compose` build, pull, run, create or up, prunes, and app installs through `scripts/dev/api.py`: the deploy script's job when the owner asks for a deploy (ADR-0223), or owner actions;
  - device-packages, on the device: apt and apt-get install, upgrade, remove and the like, `dpkg -i`, pip install, npm install or ci;
  - power: reboot, poweroff, shutdown, halt and `/proc/sysrq-trigger`, on the workstation and on the device;
  - secret-argv: a secret on a command line: `sshpass -p`, `curl -u user:password`, `--password=...`, `user:password@` in a URL, a literal assigned to a secret's name, a literal Authorization header, `zip -P` (ADR-0212). A secret's name has a part that ends in PASSWORD, PASSWD, PASSPHRASE, TOKEN, SECRET, PASS (not BYPASS, ASKPASS and the like), AUTHKEY or APIKEY, or KEY after API, PRIVATE, ACCESS or AUTH, unless its last part says something about the secret (`_FILE`, `_LENGTH`, `_ID` and the like). This one rule, `_is_secret_name`, is also the secret scan's (ADR-0216);
  - dev-env: showing, copying or replacing `dev.env`, and showing, copying or extracting the device backups in `tmp/dev/backup` (ADR-0223); recursive searches, copies and tar or zip archives of a tree that holds either, also of a linked worktree's main checkout, which the guard finds through git's common directory as `scripts/dev/env.sh` does (ADR-0210).
- The docstring of `.claude/hooks/guard.py` is the complete rule set. `supervisor/tests/test_agent_guard.py` runs the guard as Claude Code does, on a table of blocked and allowed commands per rule, with the everyday variants an agent could type by accident: other spellings and argument orders, sudo, env, `bash -c` and `sh -c` wrappers, relative and absolute paths. Its places are temporary repositories, among them a branch that tracks origin/main under `push.default` upstream and a main checkout with `dev.env` and a device backup next to its linked worktree.
- Every denial adds one line to the git-ignored `tmp/guard/denials.log`: the UTC time, the rule and the tool, never the command, which may hold a secret. The write is best effort and never blocks; an internal error of the guard never blocks either (exit 1 with a warning).
- Ordinary work stays allowed, for example `git status`, a push of the working branch, `scripts/dev/ssh.sh 'docker ps'`, `scripts/dev/deploy.sh build-supervisor`, `make check` and `npx playwright test` against a local target.
- Agents do not work around a denial; they ask the owner. They never bypass the workstation's network filter.

## Consequences

- The guard reads the command line, not what runs at run time. Commands built from variables or files are checked only as far as the line shows them: a variable set earlier on the same line is followed, one from the environment or a file is not. The contents of the scripts and programs a command runs are not read: the scripts of `scripts/dev/` and `scripts/standin/` and the Makefile are trusted as they are and enforce their own limits, and a new script that does what a rule blocks must not be run to get around it. Arguments that arrive on standard input, from a file or from a command's output are unknown. Push settings in the environment (`GIT_CONFIG_*`) are not followed. A path to a secret is recognized by its name (`dev.env`, `tmp/dev/backup`): a copy elsewhere, or a path built in a program's code, is not.
- Pattern checks on command strings are a seatbelt against accidents, not a security boundary, and the tools of other MCP servers are not checked; CI runs the repository's checks again (ADR-0216).
- A false block costs a question to the owner; the monthly agent-setup pass reads `tmp/guard/denials.log` and refines the condition that caused it (`docs/maintenance.md`, ADR-0218).
