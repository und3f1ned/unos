import asyncio
import logging
from collections.abc import Callable
from typing import Any

from docker.errors import APIError
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.models import User
from ..auth.utils import verify_token
from .schemas import ContainerInfo, ContainerCreate, ContainerLogs, ImageInfo
from . import docker as docker_ops

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/containers", tags=["containers"])


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


@router.get("", response_model=list[ContainerInfo])
async def list_containers(all: bool = Query(True), _=Depends(verify_token)):
    """List all containers."""
    try:
        return await _docker(docker_ops.list_containers, all=all)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Could not list containers")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=ContainerInfo)
async def create_container(config: ContainerCreate, request: Request, user: User = Depends(verify_token)):
    """Create a new container (Docker pulls a missing image first). The audit log gets
    container_created, with the image and never the environment (ADR-0402)."""
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
@router.get("/images", response_model=list[ImageInfo])
async def list_images(_=Depends(verify_token)):
    """List all images."""
    try:
        return await _docker(docker_ops.list_images)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Could not list images")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/images/pull", response_model=ImageInfo)
async def pull_image(image: str, request: Request, user: User = Depends(verify_token)):
    """Pull an image from registry. The audit log gets image_pulled when the pull brought a new image
    or moved the reference to another (ADR-0402)."""
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


@router.delete("/images/{image_id}")
async def remove_image(
    image_id: str, request: Request, force: bool = Query(False), user: User = Depends(verify_token)
):
    """Remove an image with all its tags (409 while a container uses it). The audit log gets
    image_removed (ADR-0402)."""
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


@router.get("/{container_id}", response_model=ContainerInfo)
async def get_container(container_id: str, _=Depends(verify_token)):
    """Get container details."""
    try:
        return await _docker(docker_ops.get_container, container_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Could not get container")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{container_id}/start", response_model=ContainerInfo)
async def start_container(container_id: str, request: Request, user: User = Depends(verify_token)):
    """Start a container (409 for a UNOS app whose data folders are not usable).

    The audit log gets container_started when it did not run (ADR-0350).
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


@router.post("/{container_id}/stop", response_model=ContainerInfo)
async def stop_container(container_id: str, request: Request, user: User = Depends(verify_token)):
    """Stop a container; the audit log gets container_stopped when it ran (ADR-0350)."""
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


@router.post("/{container_id}/restart", response_model=ContainerInfo)
async def restart_container(container_id: str, request: Request, user: User = Depends(verify_token)):
    """Restart a container (409 for a UNOS app whose data folders are not usable).

    A system container is refused as a stop or a removal of it is (404). It starts the
    container again: the audit log gets container_started (ADR-0350).
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


@router.delete("/{container_id}")
async def remove_container(
    container_id: str, request: Request, force: bool = Query(False), user: User = Depends(verify_token)
):
    """Remove a container; the audit log gets container_removed (ADR-0350)."""
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


@router.get("/{container_id}/logs", response_model=ContainerLogs)
async def get_container_logs(
    container_id: str, tail: int = Query(100), _=Depends(verify_token)
):
    """Get container logs (the web UI streams them over /ws/v1/logs/{container_id})."""
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
