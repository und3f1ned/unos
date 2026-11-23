---
status: accepted
date: 2026-11-23
area: apps
decided-by: agent
---

# ADR-0476: A configuration file that a catalog image ships is bound as that file, written when missing, and Mosquitto starts local-only

## Context

The catalog bound an empty folder of the pool over `/mosquitto/config`, which hid the
`mosquitto.conf` that the eclipse-mosquitto image ships there, so on the BPI-W3 on 2026-11-23
Mosquitto failed with "Unable to open config file" and restarted in a loop, about 1 MiB in 10 s
to the system disk (ADR-0214). Prometheus had the same bind over `/etc/prometheus`. The image's
own `mosquitto.conf` of 2.1 opens a listener on port 1883 to anonymous clients, which the
catalog publishes on the host.

## Decision

- A template's file volume (`VolumeMapping.is_file`) may have `content`
  (`supervisor/src/apps/schemas.py`): the text that the install, and the check before a start
  (`prepare_app_start`), write into the file when it is missing on the app's pool
  (`_create_file_with_content` in `supervisor/src/apps/router.py`: a temporary file beside it,
  mode 0644, the volume's owner, synced and linked to its name, as `app_secrets.create` writes a
  password file). An existing file is kept. `content` is refused on a folder, a generated secret
  or a fixed host path.
- A configuration file that an image ships in a folder and needs at its start is bound as that
  file, read-only, with such a content, never under a bind of the folder. Mosquitto binds
  `/mosquitto/config/mosquitto.conf` and Prometheus `/etc/prometheus/prometheus.yml`
  (`supervisor/src/apps/catalog.py`).
- The content is the image's own, unless that opens the app to the network without
  authentication. Prometheus gets the image's example, which scrapes Prometheus itself.
  Mosquitto gets none of the image's settings, only comments: it starts in its local-only mode,
  listening inside its container, until the administrator adds a listener and its
  authentication, as `docs/USER_GUIDE.md` already said.
- Tests: `TestShippedConfiguration` of `supervisor/tests/test_apps.py`, with a table of the
  catalog's images that ship such a file, checked against their Dockerfiles: every app of the
  catalog binds it as a file with a content and no folder over it.

## Consequences

- A fresh install of Mosquitto or Prometheus starts; their files are on the pool, once, and a
  start rewrites nothing that exists.
- Mosquitto takes no client from another container or host until its file is edited:
  Zigbee2MQTT, whose default broker it is, waits for that, as its variable's description says.
- The container sees only that file of the folder: Mosquitto's password file goes into its
  data folder, as the file's comments say.
- An app installed before with the folder bind gets the file bind at its next update, at the
  template's path; files beside it in the folder are no longer seen. UNOS has no such
  installation to keep (ADR-0207): the BPI-W3's Mosquitto, which never started, is installed
  again.
- The table of the test is kept by hand: a new image that ships such a file needs its row.

## Considered options

- The image's own `mosquitto.conf`: an MQTT broker open to anonymous clients on the LAN.
- Binding only Mosquitto's data and log, so that the image's file is used: the same anonymous
  broker, and the administrator could not edit its configuration on the pool.
- Files that the install writes into a bound folder: the folder would stay whole, but the
  install would write into a folder that the app owns, and a missing file would need its own
  checks beside those of the bind's source.
