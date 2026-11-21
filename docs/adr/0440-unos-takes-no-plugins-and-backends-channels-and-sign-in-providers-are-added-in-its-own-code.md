---
status: accepted
date: 2026-11-21
area: architecture
decided-by: agent
---

# ADR-0440: UNOS takes no plugins, and storage backends, notification channels and sign-in providers are added in its own code

## Context

The feature-list entry `architecture.plugins` asked that extensions add storage backends (ZFS,
mdadm), notification channels and sign-in providers without changes to UNOS, and noted that it
needs a decision on whether UNOS takes plugins at all; ADR-0008 had kept a plugin system among the
roadmap's architecture ideas. The supervisor runs privileged, with `pid: host` and the Docker
socket (ADR-0002): code it loads runs as root on the host, and a storage backend or a sign-in
provider needs exactly that reach. Asked to choose, the owner answered "decide yourself"
(2026-11-21).

## Decision

- UNOS has no plugin mechanism: neither the supervisor nor the web UI loads code that is not part
  of UNOS, and no API exists for extensions.
- A storage backend, a notification channel or a sign-in provider is a feature-list entry of its
  own and lands in UNOS's code, reviewed and tested like any other change (ADR-0017, ADR-0186).
- Third-party software runs as an app of the catalog, in containers of its own with pinned images
  (ADR-0410), outside the supervisor.
- The entry `architecture.plugins` is removed from `feature-list.json`.

## Consequences

- A new backend, channel or provider waits for a UNOS change; nobody keeps a plugin API stable,
  which fits UNOS's lack of backward compatibility (ADR-0207).
- Everything that runs as root in the supervisor is code that review has seen.

## Considered options

- Python packages the supervisor discovers and imports: the shortest way to add a backend, but
  each would run as root with the Docker socket and the host's processes, unreviewed.
- Extensions as containers that call an API of the supervisor: isolated, but a storage backend
  or a sign-in provider still needs the host or the sign-in path, so the API would hand them the
  same power through a second interface that must stay stable.
