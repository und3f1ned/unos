---
status: accepted
date: 2026-11-11
area: process
decided-by: owner
---

# ADR-0335: Every background command of an agent has a deadline

## Context

Claude Code wakes a session when a command that its Bash tool runs in the background
(`run_in_background`) ends, so a background command that never ends never wakes it. On
2026-11-10 a session waited for a time of day with a loop written for bash,
`until [ "$(date -u +%H%M%S)" \> "222340" ]; do sleep 15; done; ...`, run in the background,
and ended its turn. The Bash tool runs the workstation's zsh, whose `[` does not know `\>`: the
test failed at every pass, the loop never ended, and the session stalled for nine hours. The
owner said "this must never happen again".

## Decision

- A command that the Bash tool runs in the background is one call of GNU timeout that ends it
  within 2 hours (7200 s):
  `[cd DIR &&]... [NAME=VALUE ...] timeout [-k DUR] [-s SIG] [--preserve-status] [-v] DURATION COMMAND [ARGS] [REDIRECTIONS]`.
  DURATION is a number with an optional unit `s`, `m`, `h` or `d`, more than 0, and with the
  SIGKILL of `-k` (`--kill-after`) at most 7200 s; `-s` (`--signal`) names TERM, INT, HUP, QUIT
  or KILL, the signals that end a process, and `--foreground` is refused, since timeout then
  leaves the command's children running. `timeout`, `gtimeout` and their paths in
  `/opt/homebrew/bin` count. Nothing else stands at the top level: no `;`, `&&`, `||`, `|`, `&`
  or newline after the call (a `;` that ends the line is none), and no command substitution
  (`$(...)`, backticks) or process substitution (`<(...)`) outside single quotes, since the outer
  shell runs those before timeout starts. A quoted heredoc is the input of the command and may
  follow it.
- Loops and pipelines go inside `timeout <seconds> bash -c '...'`, which also runs them in bash
  rather than in zsh.
- The guard enforces it as its rule background-deadline (`.claude/hooks/guard.py`, ADR-0211):
  exit 2 with one line that names the rule and the shape to write instead, and a line in
  `tmp/guard/denials.log`. Commands in the foreground and the commands of the desktop app's
  terminal tool are not affected. `supervisor/tests/test_agent_guard.py` holds the cases
  (`BACKGROUND_CASES`), the loop of 2026-11-10 among them. For this rule the guard's lexer now
  reads three things as bash and zsh do, for every rule: the command substitutions inside
  `$((...))` run, `<<$'EOF'` is a quoted heredoc, and a carriage return is part of a word.
- Rewriting a refused command into that shape is the answer the denial asks for, not a way
  around the guard (ADR-0211).
- A session does not end its turn waiting only on a background command while other work
  remains: it goes on with that work, and the end of the background command wakes it. No tool
  checks this.

## Consequences

- Long jobs need an explicit deadline that fits them: the gate as
  `timeout 1800 make check > <log> 2>&1` (it takes about 7 minutes), and the E2E specs, deploys
  and scenario checks with deadlines of their own. A job killed at its deadline (timeout exits
  with status 124) proved nothing: it is rerun, with a longer deadline of at most 2 hours, or in
  parts. A job longer than 2 hours does not run in the background.
- The device-checks skill runs `scripts/dev/scenario.sh all` in the background as
  `timeout 3600 scripts/dev/scenario.sh all > <log> 2>&1`. At the deadline the check gets
  SIGTERM and cleans up (ADR-0304); a SIGKILL would leave what it made on the device.
- timeout ends the command with its signal: a command that catches or ignores it runs on unless
  `-k` also sends SIGKILL, and a process that leaves timeout's process group (setsid, a daemon)
  outlives it. The outer shell opens the redirections before timeout starts, so a redirection
  of a FIFO waits for its other end without a deadline. As every rule of the guard, this one
  reads the command line only.

## Considered options

- Refusing only loops in the background: a hung ssh or a command that waits for input stalls a
  session as well, and a deadline covers every case.
- Requiring `-k` in every background command, so that a command that ignores SIGTERM ends too:
  the deadline then always holds, but the SIGKILL would cut the clean-up of a check or a
  script that traps SIGTERM, and the usual commands end at SIGTERM.
