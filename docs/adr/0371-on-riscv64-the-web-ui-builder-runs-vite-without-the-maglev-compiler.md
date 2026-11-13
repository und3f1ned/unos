---
status: superseded
date: 2026-11-13
area: deploy
decided-by: agent
superseded-by: ADR-0373
---

# ADR-0371: On riscv64 the web UI's builder runs vite without Node.js's Maglev compiler

## Context

Superseded by ADR-0373: the build crashed without Maglev as well, so the web UI's image of a SpacemiT K1 is built on another machine.

The first `deploy/install.sh` on a Banana Pi BPI-F3 (SpacemiT K1) stopped in the web UI's builder
(ADR-0370): Alpine 3.22's Node.js 22.23.5 ended `vite build` after about five minutes with "Fatal
error in , line 0 # unreachable code" and SIGTRAP, in two runs of two. The same build had passed
under QEMU's riscv64 emulation on the workstation. On the board, `node --no-maglev` ran the build
to its end in about nine minutes; `--jitless` stopped at once, because undici needs WebAssembly,
and Node.js does not take `--no-maglev` from `NODE_OPTIONS`.

## Decision

- On riscv64 the builder of `webui/Dockerfile` runs `node --no-maglev
  node_modules/vite/bin/vite.js build`, so V8's Maglev compiler stays off while Turbofan stays on.
  Elsewhere it runs `npm run build`, which is `vite build`.
- `supervisor/tests/test_deploy_scripts.py`
  (`test_the_builder_runs_vite_without_maglev_on_riscv64`) checks the line and that the build
  script is still `vite build`.

## Consequences

- The web UI image builds on the K1, its builder stage in about ten minutes with `npm ci`.
- A change of the build script in `webui/package.json` must reach this line too; the test fails
  until it does.
- The cause inside V8 is not known; a later Node.js or Alpine release may not need the flag.

## Considered options

- `--jitless`: undici, which Node.js loads, needs WebAssembly, which jitless mode has not.
- `--no-maglev` on every platform: one path, but a slower build on hosts where Maglev works.
