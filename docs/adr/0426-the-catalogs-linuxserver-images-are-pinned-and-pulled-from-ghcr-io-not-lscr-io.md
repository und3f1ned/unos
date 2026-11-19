---
status: accepted
date: 2026-11-19
area: apps
decided-by: agent
---

# ADR-0426: The catalog's LinuxServer images are pinned and pulled from ghcr.io, not lscr.io

## Context

ADR-0410 and ADR-0415 pinned 22 LinuxServer images as `lscr.io/linuxserver/<name>:<tag>@<digest>`. lscr.io is LinuxServer's own redirector: a gateway that counts pulls and sends Docker on to GHCR, which serves the same images by the same index digests. On the dev device's network every Docker pull from lscr.io failed (four times since 2026-11-18: a blob's download ended with "timeout awaiting response headers", after 55 to 590 MiB of partial layers), while on 2026-11-19 `docker pull ghcr.io/linuxserver/transmission@sha256:5baf08cd...`, the same image as the catalog's transmission pin, completed in 10 s. BookStack's install and transmission's update waited for a pull that completes. The owner delegated the choices, asking above all that it be safe.

## Decision

- **The catalog names GHCR.** Each LinuxServer pin of `supervisor/src/apps/catalog.py` (BookStack's and the 21 of `APP_IMAGES`) is `ghcr.io/linuxserver/<name>:<tag>@<digest>`, with the tag, the digest, the estimate and the `VOLUME`s it had. Each tag's index digest was checked on ghcr.io on 2026-11-19 with GHCR's anonymous token and a HEAD of the tag's manifest: all 22 were the pinned digests, so none changed, and `make catalog-tags` reported none of them. The install pulls that name by digest, creates the container from `ghcr.io/linuxserver/<name>@<digest>` and tags the image `ghcr.io/linuxserver/<name>:<tag>`; the update's registry reads (ADR-0408), the platforms of `supervisor/src/apps/image_platforms.json` (ADR-0403, renamed, `make catalog-platforms` passed) and the weekly tags audit use the same name.
- **No ADR is superseded.** This changes the image references that ADR-0410 and ADR-0415 list, not their decisions: the images, tags and digests stay, as ADR-0410 re-tagged the Valkey pin of ADR-0381 without superseding it.
- **lscr.io in the code.** No image of the catalog may name lscr.io: `supervisor/tests/test_linuxserver_ghcr.py` fails for one, and holds the 22 tags and digests that ghcr.io named; `make catalog-tags` compares them with ghcr.io again. The registry reader (`supervisor/src/apps/registry.py`) and the audit's `scripts/catalog-platforms.py` read only the catalog's references, so their lscr.io alias and its `linuxserver/` prefix are removed (ADR-0207); an lscr.io reference would be read at lscr.io itself.
- **Installed apps created from lscr.io** stay what they were: an app is its containers' `unos.app` label, never its image (ADR-0381), so it is listed, started, stopped and uninstalled as before. Its update compares digests only (ADR-0408): a container created from a tag (the dev device's transmission, from `lscr.io/linuxserver/transmission:latest`) is offered the GHCR pin, and the update creates it from GHCR's name; a container created from an lscr.io pin's `name@digest` runs the very image that the GHCR pin names and is offered nothing, since a new name alone is no reason to recreate an app. A rollback gives the earlier container its name back: it runs the image it ran, by Docker's id, under its lscr.io name, and nothing is pulled.
- **Custom containers and the Images tab** take the administrator's own references, from any registry, lscr.io included: UNOS neither refuses nor rewrites them (the user guide names GHCR as the same image).
- **The space check** (ADR-0381) looks for a pin by its GHCR name and digest, as Docker keeps it after `docker pull ghcr.io/linuxserver/<name>@<digest>`. An image that Docker has only under an lscr.io name counts its estimate again: the check errs high, as ADR-0410's estimate does, and Docker's pull then fetches only the manifests. The update's plan counts the layers by their chain ids, whatever name holds them.

## Consequences

- BookStack's install and transmission's update pull from the registry whose pull completed on the dev device's network; `apps.linuxserver-images-from-ghcr`'s device check runs both through UNOS's API.
- LinuxServer's own pull counts no longer see UNOS's pulls.
- The device checks recorded in the notes of `apps.pinned-catalog-images`, `apps.update-in-place` and `apps.catalog-companions` name the lscr.io references of their day; the digests they name are unchanged.
- A LinuxServer image that LinuxServer stops publishing on GHCR would need a re-pin elsewhere, as for any publisher; the tags audit reports a gone index.

## Considered options

- Keep lscr.io and retry or proxy Docker's pulls: the failures were Docker's downloads of lscr.io's blobs on this network, which UNOS cannot fix, and GHCR serves the same bytes.
- Recreate apps installed from an lscr.io pin under the GHCR name: the same image, a stop and a recreate for nothing.
