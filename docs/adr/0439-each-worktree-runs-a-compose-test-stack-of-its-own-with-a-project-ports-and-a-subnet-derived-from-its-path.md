---
status: accepted
date: 2026-11-21
area: testing
decided-by: agent
---

# ADR-0439: Each worktree runs a compose test stack of its own, with a project, ports and a subnet derived from its path

## Context

The compose test stack of `deploy/docker-compose.test.yml` had one project (`unos-test`), fixed
container names, the ports 3080 and 3443 and the subnet 172.31.253.0/24, so `scripts/test-cycle.sh`
ran once per machine (ADR-0210): a second worktree's run failed on the names and ports, and its
`down -v` at the start and the end removed the first one's containers and volumes. Parallel work in
worktrees (ADR-0210) needs one stack per worktree (`testing.compose-stack-per-worktree`).

## Decision

- **Settings.** The compose file takes its project (`name:`), its container names
  (`<project>-supervisor`, `<project>-webui`, `<project>-runner`, which the supervisor's
  `UNOS_WEBUI_CONTAINER` names too), its published ports and its /24 (subnet, gateway `.1`, the
  web UI `.2`, which `FORWARDED_ALLOW_IPS` names, the supervisor `.3`) from `UNOS_TEST_PROJECT`,
  `UNOS_TEST_HTTP_PORT`, `UNOS_TEST_HTTPS_PORT` and `UNOS_TEST_SUBNET_PREFIX` (the first three
  numbers of the /24). Their defaults are the old values, for a stack started by hand
  (`docs/TESTING.md`). Its volumes and network have no names of their own, so they belong to the
  project.
- **Project.** `scripts/test-cycle.sh` derives the project from the physical path of its checkout:
  `unos-test-` and the eight hex digits of the path's POSIX `cksum`. Two worktrees have two
  projects, and the same worktree always the same one.
- **Ports and subnet.** One of 100 slots: slot n publishes HTTP on 127.0.0.1:13080+n and HTTPS on
  127.0.0.1:13443+n and has the network 172.31.(100+n).0/24, away from the production stack's
  172.31.254.0/24 and the defaults. `choose_test_stack` takes the first slot, from the path's
  CRC modulo 100 onward, whose subnet no Docker network has: every stack that holds a slot has
  its subnet, so a second worktree whose path gives a held slot takes the next one. It chooses
  after the build, which can take minutes, right before `docker compose up` creates the network
  that holds the slot. When all 100 are held, it stops before `up`.
- **Only its own project.** Every compose call of the script names the project with `-p`, which
  comes before `COMPOSE_PROJECT_NAME` of the caller's environment and the file's `name:`, so its
  `down -v --remove-orphans` (at the start, at the end and in `--cleanup`) removes this worktree's
  containers, network and volumes only.
- Tested in `supervisor/tests/test_deploy_scripts.py` (`TestTestStack`, `TestTestStackPerWorktree`,
  `TestTestCycle`), which read the compose file and run the script's functions against a fake
  Docker. This ends the "runs once per machine" of the compose test stack in ADR-0210; its other
  rules stand.

## Consequences

- Each worktree builds images of its own project (`<project>-supervisor`, `<project>-webui`), which
  `down -v` leaves; a removed worktree's images stay until someone removes them.
- The script prints the project and the addresses of its stack, which lives while the script
  runs; `docker compose -p <project>` reaches it meanwhile, and the manual commands of
  `docs/TESTING.md` without the settings start the stack of the defaults.
- A program other than a test stack that holds a slot's port makes `docker compose up` fail with
  Docker's error; so does a network outside the test stacks that overlaps the slot's /24 without
  being that /24. Two worktrees whose paths give the same free slot and whose builds end within
  the moment between one's choice and its `up` creating the network can both take it, and the
  second `up` fails the same way.

## Considered options

- Ports chosen by Docker (`127.0.0.1::443`): no collisions, but the supervisor trusts
  `X-Forwarded-For` only from the web UI's fixed address (ADR-0048), which needs a fixed subnet
  anyway, and the URLs would be known only after `up`.
- The slot from the worktree's position in `git worktree list`: unique at one moment, but it
  shifts when an earlier worktree is removed while a later one's stack runs.
