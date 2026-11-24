---
status: accepted
date: 2026-11-24
area: apps
decided-by: agent
supersedes: [ADR-0480]
---

# ADR-0490: Mosquitto listens on its published ports only for clients that sign in with the password of its install

## Context

ADR-0480 gave Mosquitto a `mosquitto.conf` of comments only, so Mosquitto 2.1 ran in its
local-only mode: port 9001 was published with nothing listening (left open by
`apps.install-on-a-pool`), and port 1883 reached nothing either, so the catalog's Zigbee2MQTT,
whose broker is the Mosquitto app at `host.docker.internal:1883` (ADR-0415), could not connect.
The image's own file opens 1883 to anonymous clients. Mosquitto reads only a password file of hashes, which
`mosquitto_passwd` makes, and opens it after it drops root to its user `mosquitto` (uid 1883).

## Decision

- A template's file volume (`VolumeMapping.is_file`) may have `content`
  (`supervisor/src/apps/schemas.py`): the text that the install, and the check before a start
  (`prepare_app_start`), write into the file when it is missing on the app's pool
  (`_create_file_with_content` in `supervisor/src/apps/router.py`: a temporary file beside it,
  mode 0644, the volume's owner, synced and linked to its name). An existing file is kept.
  `content` is refused on a folder, a generated secret or a fixed host path. A configuration file
  that an image ships in a folder and needs at its start is bound as that file, read-only, with
  such a content, never under a bind of the folder (Mosquitto's `mosquitto.conf`, Prometheus's
  `prometheus.yml`). The content is the image's own, unless that opens the app to the network
  without authentication: Prometheus gets the image's example, which scrapes Prometheus itself.
  `TestShippedConfiguration` of `supervisor/tests/test_apps.py` holds a table of the catalog's
  images that ship such a file, checked by hand against their Dockerfiles (a new image that ships
  one needs its row): every app binds it as a file with a content and no folder over it. These
  rules are ADR-0480's, unchanged.
- Mosquitto's content (`MOSQUITTO_CONF` in `supervisor/src/apps/catalog.py`): `listener 1883`,
  `listener 9001` with `protocol websockets`, `allow_anonymous false` and `password_file
  /run/mosquitto/passwords`; persistence and a log file stay comments. The template publishes
  1883 and 9001, both listened on.
- The install asks for `MOSQUITTO_PASSWORD` (required, secret: placeholders refused). The user
  name `MOSQUITTO_USERNAME` is `mqtt`, set by UNOS (`editable=False`): `mosquitto_passwd` refuses
  a name with `:`, the install checks no variable's form, and a start that fails restarts in a
  loop.
- The container's command (`MOSQUITTO_COMMAND`, run by the image's entrypoint) hands the password
  twice on the input of `mosquitto_passwd -c /run/mosquitto/passwords "$MOSQUITTO_USERNAME"`
  through `printf`, a builtin of the image's shell, so it is in no command line; with `umask 077`
  and `chown mosquitto:mosquitto` the file is 0600 and Mosquitto's own. `/run/mosquitto` is a
  tmpfs of 1 MB: the file is made again at every start, in RAM. Then the command unsets the
  password and runs the pinned image's CMD, recorded in its pin (`PinnedImage.cmd`).
- A secret variable of any app is one line: the install refuses a value with a line break,
  which would give `mosquitto_passwd` two different lines and fail every start.
- Zigbee2MQTT gets `ZIGBEE2MQTT_CONFIG_MQTT_USER` (`mqtt`) and `ZIGBEE2MQTT_CONFIG_MQTT_PASSWORD`
  (secret, empty: Zigbee2MQTT ignores an empty variable), into which the administrator types the
  password of Mosquitto's install.
- Tests: `TestShippedConfiguration` and `TestMosquittoSignIn` of `supervisor/tests/test_apps.py`,
  the latter running the command with recording stand-ins of `mosquitto_passwd`, `chown` and
  Mosquitto, and refusing a password with a line break.

## Consequences

- A fresh install of Mosquitto takes MQTT clients of the LAN, of other apps and of Zigbee2MQTT
  through the host gateway, each with `mqtt` and the install's password, and no anonymous one.
- MQTT and its password cross the network unencrypted, as on most home brokers; TLS is the
  administrator's addition to the file, as more users are: a password file in the data folder,
  named in `password_file` in place of UNOS's, which then must hold `mqtt` too for Zigbee2MQTT.
- The command runs the CMD recorded in the pin by hand, as Linkwarden's does: a re-pin whose
  image changes its CMD must change the record, or the start runs the old one.
- Zigbee2MQTT installed without Mosquitto's password is refused by Mosquitto, as it reached no
  broker before; the user guide says to give it the password.
- The password is a variable of the container, in Docker's configuration like the other
  passwords that an install asks for (Grafana's, PhotoPrism's), not a generated file: the
  administrator must know it to give it to the clients. It changes with an uninstall and an
  install.
- The file of ADR-0480's install is kept by a reinstall: an app installed with it stays
  local-only until its `mosquitto.conf` is removed (ADR-0207: no conversion).

## Considered options

- Drop 9001 and keep the local-only mode: the app would still serve no client, Zigbee2MQTT
  included, until the administrator edited its file.
- A password that UNOS generates as a file (ADR-0381): never shown, so no client could use it.
- `mosquitto_passwd -U` on a plain `user:password` file: it cuts the password at its first `:`;
  `-b` puts the password on a command line.
