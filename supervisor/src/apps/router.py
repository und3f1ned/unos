from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime
import os

from ..auth.utils import verify_token
from ..containers.docker import get_client, get_container
from .schemas import (
    AppTemplate, AppCategory, AppInstallRequest, 
    AppInstallResponse, InstalledApp
)
from .catalog import APP_CATALOG, get_app_by_id, get_apps_by_category, search_apps

router = APIRouter(prefix="/apps", tags=["apps"])

UNOS_APP_LABEL = "unos.app"


@router.get("/catalog", response_model=list[AppTemplate])
async def list_apps(
    category: AppCategory | None = None,
    query: str | None = None,
    _: dict = Depends(verify_token)
):
    """List available apps from catalog."""
    if query:
        return search_apps(query)
    if category:
        return get_apps_by_category(category)
    return APP_CATALOG


@router.get("/catalog/{app_id}", response_model=AppTemplate)
async def get_app(app_id: str, _: dict = Depends(verify_token)):
    """Get app template by ID."""
    app = get_app_by_id(app_id)
    if not app:
        raise HTTPException(404, f"App {app_id} not found")
    return app


@router.get("/installed", response_model=list[InstalledApp])
async def list_installed(_: dict = Depends(verify_token)):
    """List installed apps."""
    client = get_client()
    containers = client.containers.list(all=True, filters={"label": UNOS_APP_LABEL})
    
    result = []
    for c in containers:
        template_id = c.labels.get(UNOS_APP_LABEL, "")
        template = get_app_by_id(template_id)
        
        web_port = None
        ports = c.attrs.get("NetworkSettings", {}).get("Ports") or {}
        for port_str, bindings in ports.items():
            if bindings and bindings[0].get("HostPort"):
                web_port = int(bindings[0]["HostPort"])
                break
        
        result.append(InstalledApp(
            id=c.short_id,
            template_id=template_id,
            name=c.name,
            container_id=c.short_id,
            status=c.status,
            web_port=web_port,
            created_at=c.attrs.get("Created", ""),
        ))
    
    return result


@router.post("/install", response_model=AppInstallResponse)
async def install_app(req: AppInstallRequest, _: dict = Depends(verify_token)):
    """Install an app from catalog."""
    template = get_app_by_id(req.template_id)
    if not template:
        raise HTTPException(404, f"App {req.template_id} not found")
    
    client = get_client()
    name = req.name or template.id
    
    # Check if already exists
    try:
        existing = client.containers.get(name)
        raise HTTPException(400, f"Container {name} already exists")
    except Exception:
        pass
    
    # Build port bindings
    port_bindings = {}
    for p in template.ports:
        host_port = req.port_overrides.get(p.container, p.host)
        key = f"{p.container}/{p.protocol}"
        port_bindings[key] = host_port
    
    # Build volumes
    volumes = []
    for v in template.volumes:
        host_path = req.volume_overrides.get(v.container, v.host)
        os.makedirs(host_path, exist_ok=True)
        mode = "ro" if v.readonly else "rw"
        volumes.append(f"{host_path}:{v.container}:{mode}")
    
    # Build environment
    environment = {}
    for e in template.environment:
        environment[e.name] = req.env_overrides.get(e.name, e.value)
    
    # Pull image
    try:
        client.images.pull(template.image)
    except Exception as e:
        raise HTTPException(500, f"Failed to pull image: {e}")
    
    # Create container
    try:
        container = client.containers.run(
            template.image,
            name=name,
            detach=True,
            ports=port_bindings if port_bindings else None,
            volumes=volumes if volumes else None,
            environment=environment if environment else None,
            restart_policy={"Name": template.restart_policy},
            privileged=template.privileged,
            cap_add=template.capabilities if template.capabilities else None,
            network_mode=template.network_mode,
            labels={
                UNOS_APP_LABEL: template.id,
                "unos.app.name": template.name,
                "unos.app.icon": template.icon,
            },
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to create container: {e}")
    
    # Get web URL
    web_url = None
    if template.ports:
        first_port = req.port_overrides.get(template.ports[0].container, template.ports[0].host)
        web_url = f"http://localhost:{first_port}"
    
    return AppInstallResponse(
        id=container.short_id,
        name=name,
        container_id=container.short_id,
        status=container.status,
        web_url=web_url,
    )


@router.delete("/installed/{container_id}")
async def uninstall_app(container_id: str, _: dict = Depends(verify_token)):
    """Uninstall an app."""
    client = get_client()
    try:
        container = client.containers.get(container_id)
        if UNOS_APP_LABEL not in container.labels:
            raise HTTPException(400, "Not a UNOS managed app")
        container.stop()
        container.remove()
        return {"status": "removed", "id": container_id}
    except Exception as e:
        raise HTTPException(500, f"Failed to remove: {e}")


@router.post("/installed/{container_id}/start")
async def start_app(container_id: str, _: dict = Depends(verify_token)):
    """Start an installed app."""
    client = get_client()
    try:
        container = client.containers.get(container_id)
        container.start()
        container.reload()
        return {"status": container.status, "id": container_id}
    except Exception as e:
        raise HTTPException(500, f"Failed to start: {e}")


@router.post("/installed/{container_id}/stop")
async def stop_app(container_id: str, _: dict = Depends(verify_token)):
    """Stop an installed app."""
    client = get_client()
    try:
        container = client.containers.get(container_id)
        container.stop()
        container.reload()
        return {"status": container.status, "id": container_id}
    except Exception as e:
        raise HTTPException(500, f"Failed to stop: {e}")


@router.post("/installed/{container_id}/restart")
async def restart_app(container_id: str, _: dict = Depends(verify_token)):
    """Restart an installed app."""
    client = get_client()
    try:
        container = client.containers.get(container_id)
        container.restart()
        container.reload()
        return {"status": container.status, "id": container_id}
    except Exception as e:
        raise HTTPException(500, f"Failed to restart: {e}")
