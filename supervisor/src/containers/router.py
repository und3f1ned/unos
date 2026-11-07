import asyncio
from collections.abc import Callable
from typing import Any

from docker.errors import APIError
from fastapi import APIRouter, Depends, HTTPException, Query
from ..auth.utils import verify_token
from .schemas import ContainerInfo, ContainerCreate, ContainerLogs, ImageInfo
from . import docker as docker_ops

router = APIRouter(prefix="/containers", tags=["containers"])


async def _docker(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a docker.py operation in a worker thread: docker-py blocks (ADR-0021).

    A conflict that Docker reports (409: an image that a container uses, a
    name that is taken, a running container removed without force) keeps its
    status, with Docker's reason as the detail (ADR-0247).
    """
    try:
        return await asyncio.to_thread(operation, *args, **kwargs)
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
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=ContainerInfo)
async def create_container(config: ContainerCreate, _=Depends(verify_token)):
    """Create a new container (Docker pulls a missing image first)."""
    try:
        return await _docker(docker_ops.create_container, config)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/images/pull", response_model=ImageInfo)
async def pull_image(image: str, _=Depends(verify_token)):
    """Pull an image from registry."""
    try:
        return await _docker(docker_ops.pull_image, image)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/images/{image_id}")
async def remove_image(
    image_id: str, force: bool = Query(False), _=Depends(verify_token)
):
    """Remove an image (409 with Docker's reason while a container uses it)."""
    try:
        await _docker(docker_ops.remove_image, image_id, force=force)
        return {"status": "removed"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{container_id}/start", response_model=ContainerInfo)
async def start_container(container_id: str, _=Depends(verify_token)):
    """Start a container (409 for a UNOS app whose data folders are not usable)."""
    try:
        # The app checks read the pools (statvfs can hang), in the same worker thread.
        return await _docker(docker_ops.start_container, container_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{container_id}/stop", response_model=ContainerInfo)
async def stop_container(container_id: str, _=Depends(verify_token)):
    """Stop a container."""
    try:
        return await _docker(docker_ops.stop_container, container_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{container_id}/restart", response_model=ContainerInfo)
async def restart_container(container_id: str, _=Depends(verify_token)):
    """Restart a container (409 for a UNOS app whose data folders are not usable)."""
    try:
        return await _docker(docker_ops.restart_container, container_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{container_id}")
async def remove_container(
    container_id: str, force: bool = Query(False), _=Depends(verify_token)
):
    """Remove a container."""
    try:
        await _docker(docker_ops.remove_container, container_id, force=force)
        return {"status": "removed"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))
