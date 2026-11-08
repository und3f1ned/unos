---
status: accepted
date: 2026-11-07
area: apps
decided-by: agent
---

# ADR-0255: The container drawer follows the log over the logs socket, and the Images tab pulls by name and removes only what no container uses

## Context

The container drawer read a container's log over HTTP, `GET /api/v1/containers/{id}/logs`, when its Logs tab opened and every 3 seconds with Auto-refresh, although `/ws/v1/logs/{container_id}` streams it (ADR-0006, ADR-0031). The web UI had the functions for the image API, but no page called them.

## Decision

- The Logs tab of `webui/src/lib/components/containers/ContainerDetailDrawer.svelte` opens `/ws/v1/logs/{id}` (`streamContainerLogs` in `webui/src/lib/api/containers.ts`) while the tab of an open drawer shows it, and closes it when the tab, the drawer or the container changes. The token goes in the first message (ADR-0031); 4401 ends the session, and 4403 shows that only administrators read container logs.
- The socket sends the last 200 lines (`LOG_STREAM_TAIL_LINES` of `supervisor/src/websocket/events.py`, 50 before; the drawer showed 200 by default) and then each new one, each line starting with the time Docker received it, as in `GET /api/v1/containers/{id}/logs`. When the log ends (the container stopped), or after `{"type": "error"}` with the reason the log cannot be read, the server closes the socket with 1000; an endpoint that only returned would make uvicorn drop the connection without a close frame, which a browser reports as 1006. The drawer's choice of 100 to 1000 lines, Refresh and Auto-refresh are gone; `GET /api/v1/containers/{id}/logs` stays for API clients.
- The drawer keeps the newest 500,000 characters, cut at a line break, and follows new lines while it is scrolled to the end. A close with 1000 shows that the stream ended, any other close that the connection was lost; both offer Reconnect, which starts again with the last lines. It does not reconnect by itself: the stream of a stopped container would end again at once.
- The Containers page has the tabs Containers and Images. Images (`webui/src/lib/components/containers/ImageList.svelte`) lists what `GET /api/v1/containers/images` returns, without UNOS's own images: tags, ID, size and creation time. Pull takes a name as `docker pull` does (latest without a tag) and waits until Docker has the image.
- Remove asks first, names the tags that go with the image, and never forces. `remove_image` of `supervisor/src/containers/docker.py` refuses an image that a container uses, running or stopped, with 409 naming the containers, before it removes anything. Otherwise it removes each tag without force, and then what is left of the image by its ID: Docker's classic image store deletes an image with more than one tag by its ID only when forced ("image is referenced in multiple repositories"), even when no container uses it. Docker's own refusals reach the dialog with Docker's reason (409, ADR-0254).
- Tests: `webui/tests/containers.spec.ts` (route fakes of the containers API, `page.routeWebSocket` for the log socket), `supervisor/tests/test_ws_auth.py` (`TestTheLogSocketClosesWhenItsLogEnds` under a real uvicorn) and `supervisor/tests/test_containers.py` (`TestImageRemoval`, against a fake that refuses as the classic image store does).

## Consequences

- Live log lines come over one socket per open Logs tab; nothing polls the API for them.
- An image that other local images are built on loses its tags and stays: Docker refuses to delete it ("image has dependent child images"), and the dialog shows that reason. A container created between the check and the removal of the last tag keeps that tag the same way, with Docker's reason.
- Pull shows no progress: the page waits for the answer, which comes when the image is there.
