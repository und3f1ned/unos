import asyncio
import logging
from collections.abc import Callable
from typing import Annotated, Any

from docker.errors import APIError
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.models import User
from ..auth.utils import verify_token
from ..openapi import BODY, answer, errors
from .schemas import ContainerInfo, ContainerCreate, ContainerLogs, ImageInfo
from . import docker as docker_ops

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/containers", tags=["containers"])

# The API documentation (ADR-0417): the path parameter and the errors of _docker and of the routes'
# own except branches.
ContainerId = Annotated[str, Path(description="The container's id, a prefix of its id, or its name.")]
NO_DOCKER = {503: "Docker does not answer: its socket is missing or refuses the connection, or the connection broke."}
DOCKER_FAILED = {500: "Docker failed, with its error as the detail."}
NO_CONTAINER = {404: "Docker has no container of that id or name."}
APP_CHECKS = {
    409: "The container belongs to an app that must not start: its data folders are not usable, it belongs to the "
         "earlier version that an update kept, or another change of the app is under way.",
    500: "Docker failed, or a data folder of the app's container could not be checked or created.",
}


async def _docker(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a docker.py operation in a worker thread: docker-py blocks (ADR-0021).

    A Docker that does not answer answers 503 with the reason (ADR-0323). A
    conflict answers 409: an image that containers use, which remove_image
    refuses itself (ADR-0255), and the conflicts that Docker reports (a name that
    is taken, a running container removed without force) with Docker's reason as
    the detail (ADR-0254).
    """
    try:
        return await asyncio.to_thread(operation, *args, **kwargs)
    except docker_ops.NO_ANSWER as e:
        raise HTTPException(status_code=503, detail=docker_ops.no_answer_detail(e)) from e
    except docker_ops.ImageInUse as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except APIError as e:
        if e.status_code == 409:
            raise HTTPException(status_code=409, detail=e.explanation or str(e)) from e
        raise


@router.get(
    "", response_model=list[ContainerInfo], summary="List the containers",
    responses=errors(DOCKER_FAILED, NO_DOCKER),
)
async def list_containers(
    all: bool = Query(True, description="true: every container; false: only those that run."),
    _=Depends(verify_token),
):
    """The containers on the host, but UNOS's own (those whose names start with unos-)."""
    try:
        return await _docker(docker_ops.list_containers, all=all)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Could not list containers")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "", response_model=ContainerInfo, summary="Create and start a container",
    responses=errors(BODY, {
        409: "Docker refused it: a container of that name exists.",
        500: "Docker could not pull the image, create the container or start it (a container that Docker created "
             "and could not start stays), or the name is one of UNOS's own (unos-...).",
    }, NO_DOCKER),
)
async def create_container(config: ContainerCreate, request: Request, user: User = Depends(verify_token)):
    """Create a container and start it; Docker pulls its image first when it lacks it. The audit
    log gets container_created, with the image and never the environment.
    \f
    ADR-0402.
    """
    try:
        info = await _docker(docker_ops.create_container, config)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Could not create container")
        # Docker made the container and could not start it (a port in use): it stays, so the audit log
        # gets container_created as failed (ADR-0402).
        if await asyncio.to_thread(docker_ops.container_exists, config.name):
            await audit_change(
                AuditAction.CONTAINER_CREATED, request, user.username, resource_type="container",
                resource_id=config.name, success=False,
                details=f"Created the container {config.name} from {config.image}; then: {e}",
            )
        raise HTTPException(status_code=500, detail=str(e))
    await audit_change(
        AuditAction.CONTAINER_CREATED, request, user.username, resource_type="container", resource_id=info.name,
        details=f"Created the container {info.name} from {config.image}",
    )
    return info


# Image endpoints. They come before the /{container_id} routes: Starlette tries
# the routes in order, and GET /{container_id} would take "images" for a
# container name.
@router.get(
    "/images", response_model=list[ImageInfo], summary="List the images",
    responses=errors(DOCKER_FAILED, NO_DOCKER),
)
async def list_images(_=Depends(verify_token)):
    """The images on the host, but UNOS's own (whose names start with deploy-)."""
    try:
        return await _docker(docker_ops.list_images)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Could not list images")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/images/pull", response_model=ImageInfo, summary="Pull an image",
    responses=errors({500: "The pull failed: the registry has no such image, or Docker failed."}, NO_DOCKER),
)
async def pull_image(
    image: Annotated[str, Query(description="The image to pull, as name:tag or name@sha256:... (the tag latest when "
                                            "it names none).")],
    request: Request,
    user: User = Depends(verify_token),
):
    """Pull an image from its registry and answer it as Docker has it then. The audit log gets
    image_pulled when the pull brought a new image or moved the reference to another.
    \f
    ADR-0402.
    """
    try:
        info, changed = await _docker(docker_ops.pull_image_changed, image)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Could not pull image")
        raise HTTPException(status_code=500, detail=str(e))
    if changed:
        await audit_change(
            AuditAction.IMAGE_PULLED, request, user.username, resource_type="image", resource_id=image,
            details=f"Pulled the image {image}",
        )
    return info


@router.delete(
    "/images/{image_id}", summary="Remove an image",
    responses={
        **answer({"status": "removed"}, "Always removed: the image is gone with all its tags."),
        **errors({
            404: "Docker has no image of that id or name, or it is one of UNOS's own images.",
            409: "A container uses the image (without force), or Docker refused to remove it.",
        }, DOCKER_FAILED, NO_DOCKER),
    },
)
async def remove_image(
    image_id: Annotated[str, Path(description="The image's id, a prefix of it, or a name:tag of the image.")],
    request: Request,
    force: bool = Query(False, description="true: remove it with Docker's force, without UNOS's check of the "
                                           "containers that use it."),
    user: User = Depends(verify_token),
):
    """Remove an image with all its tags; refused while a container, running or stopped, uses it.
    The audit log gets image_removed.
    \f
    ADR-0255, ADR-0402.
    """
    try:
        name = await _docker(docker_ops.remove_image, image_id, force=force)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Could not remove image")
        raise HTTPException(status_code=500, detail=str(e))
    await audit_change(
        AuditAction.IMAGE_REMOVED, request, user.username, resource_type="image", resource_id=name,
        details=f"Removed the image {name}" + (" (forced)" if force else ""), severity=AuditSeverity.WARNING,
    )
    return {"status": "removed"}


@router.get(
    "/{container_id}", response_model=ContainerInfo, summary="Read a container",
    responses=errors(NO_CONTAINER, DOCKER_FAILED, NO_DOCKER),
)
async def get_container(container_id: ContainerId, _=Depends(verify_token)):
    """A container as Docker reports it, with its ports that the host publishes."""
    try:
        return await _docker(docker_ops.get_container, container_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Could not get container")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{container_id}/start", response_model=ContainerInfo, summary="Start a container",
    responses=errors(NO_CONTAINER, APP_CHECKS, NO_DOCKER),
)
async def start_container(container_id: ContainerId, request: Request, user: User = Depends(verify_token)):
    """Start a container; one that runs already stays as it is. A container of an app starts only
    when the app's data folders are usable, as on the App Store; it starts alone, not with the
    app's other containers. The audit log gets container_started when it did not run.
    \f
    ADR-0350.
    """
    try:
        # The app checks read the pools (statvfs can hang), in the same worker thread.
        info, started = await _docker(docker_ops.start_container, container_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Could not start container")
        raise HTTPException(status_code=500, detail=str(e))
    if started:
        await audit_change(
            AuditAction.CONTAINER_STARTED, request, user.username, resource_type="container",
            resource_id=info.name,
            details=f"Started the container {info.name}",
        )
    return info


@router.post(
    "/{container_id}/stop", response_model=ContainerInfo, summary="Stop a container",
    responses=errors({404: "Docker has no container of that id or name, or it is one of UNOS's own."},
                     DOCKER_FAILED, NO_DOCKER),
)
async def stop_container(container_id: ContainerId, request: Request, user: User = Depends(verify_token)):
    """Stop a container; one that does not run stays as it is. The audit log gets
    container_stopped when it ran.
    \f
    ADR-0350.
    """
    try:
        info, stopped = await _docker(docker_ops.stop_container, container_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Could not stop container")
        raise HTTPException(status_code=500, detail=str(e))
    if stopped:
        await audit_change(
            AuditAction.CONTAINER_STOPPED, request, user.username, resource_type="container",
            resource_id=info.name,
            details=f"Stopped the container {info.name}",
        )
    return info


@router.post(
    "/{container_id}/restart", response_model=ContainerInfo, summary="Restart a container",
    responses=errors({404: "Docker has no container of that id or name, or it is one of UNOS's own."},
                     APP_CHECKS, NO_DOCKER),
)
async def restart_container(container_id: ContainerId, request: Request, user: User = Depends(verify_token)):
    """Restart a container; a container of an app only when the app's data folders are usable.
    UNOS's own containers are refused, as for a stop or a removal. It starts the container again:
    the audit log gets container_started.
    \f
    ADR-0350.
    """
    try:
        info = await _docker(docker_ops.restart_container, container_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Could not restart container")
        raise HTTPException(status_code=500, detail=str(e))
    await audit_change(
        AuditAction.CONTAINER_STARTED, request, user.username, resource_type="container", resource_id=info.name,
        details=f"Restarted the container {info.name}",
    )
    return info


@router.delete(
    "/{container_id}", summary="Remove a container",
    responses={
        **answer({"status": "removed"}, "Always removed: Docker removed the container."),
        **errors({
            404: "Docker has no container of that id or name, or it is one of UNOS's own.",
            409: "Docker refused it (a running container without force), or another change of its app is under way.",
        }, DOCKER_FAILED, NO_DOCKER),
    },
)
async def remove_container(
    container_id: ContainerId,
    request: Request,
    force: bool = Query(False, description="true: remove it also while it runs (Docker's force)."),
    user: User = Depends(verify_token),
):
    """Remove a container; its volumes and the folders bound into it stay. The audit log gets
    container_removed.
    \f
    ADR-0350.
    """
    try:
        name = await _docker(docker_ops.remove_container, container_id, force=force)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Could not remove container")
        raise HTTPException(status_code=500, detail=str(e))
    await audit_change(
        AuditAction.CONTAINER_REMOVED, request, user.username, resource_type="container", resource_id=name,
        details=f"Removed the container {name}" + (" (forced)" if force else ""),
        severity=AuditSeverity.WARNING,
    )
    return {"status": "removed"}


@router.get(
    "/{container_id}/logs", response_model=ContainerLogs, summary="Read a container's log",
    responses=errors(NO_CONTAINER, DOCKER_FAILED, NO_DOCKER),
)
async def get_container_logs(
    container_id: ContainerId,
    tail: int = Query(100, description="How many of the log's last lines to answer."),
    _=Depends(verify_token),
):
    """The last lines of a container's log, each with Docker's time stamp; the web UI streams the
    log over /ws/v1/logs/{container_id} instead."""
    try:
        logs = await _docker(docker_ops.get_container_logs, container_id, tail=tail)
        return ContainerLogs(container_id=container_id, logs=logs)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Could not get container logs")
        raise HTTPException(status_code=500, detail=str(e))
