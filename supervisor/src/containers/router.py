from fastapi import APIRouter, Depends, HTTPException, Query
from ..auth.utils import verify_token
from .schemas import ContainerInfo, ContainerCreate, ContainerLogs, ImageInfo
from . import docker as docker_ops

router = APIRouter(prefix="/containers", tags=["containers"])


@router.get("", response_model=list[ContainerInfo])
async def list_containers(all: bool = Query(True), _=Depends(verify_token)):
    """List all containers."""
    try:
        return docker_ops.list_containers(all=all)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=ContainerInfo)
async def create_container(config: ContainerCreate, _=Depends(verify_token)):
    """Create a new container."""
    try:
        return docker_ops.create_container(config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{container_id}", response_model=ContainerInfo)
async def get_container(container_id: str, _=Depends(verify_token)):
    """Get container details."""
    try:
        return docker_ops.get_container(container_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{container_id}/start", response_model=ContainerInfo)
async def start_container(container_id: str, _=Depends(verify_token)):
    """Start a container."""
    try:
        return docker_ops.start_container(container_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{container_id}/stop", response_model=ContainerInfo)
async def stop_container(container_id: str, _=Depends(verify_token)):
    """Stop a container."""
    try:
        return docker_ops.stop_container(container_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{container_id}/restart", response_model=ContainerInfo)
async def restart_container(container_id: str, _=Depends(verify_token)):
    """Restart a container."""
    try:
        return docker_ops.restart_container(container_id)
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
        docker_ops.remove_container(container_id, force=force)
        return {"status": "removed"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{container_id}/logs", response_model=ContainerLogs)
async def get_container_logs(
    container_id: str, tail: int = Query(100), _=Depends(verify_token)
):
    """Get container logs."""
    try:
        logs = docker_ops.get_container_logs(container_id, tail=tail)
        return ContainerLogs(container_id=container_id, logs=logs)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Image endpoints
@router.get("/images", response_model=list[ImageInfo])
async def list_images(_=Depends(verify_token)):
    """List all images."""
    try:
        return docker_ops.list_images()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/images/pull", response_model=ImageInfo)
async def pull_image(image: str, _=Depends(verify_token)):
    """Pull an image from registry."""
    try:
        return docker_ops.pull_image(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/images/{image_id}")
async def remove_image(
    image_id: str, force: bool = Query(False), _=Depends(verify_token)
):
    """Remove an image."""
    try:
        docker_ops.remove_image(image_id, force=force)
        return {"status": "removed"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
