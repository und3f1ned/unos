import asyncio
import importlib
import os
import re
import threading
import pytest
import requests
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from docker.errors import APIError, NotFound
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from src.auth.utils import verify_token
from src.config import get_settings
from src.containers import docker as docker_module
from src.containers.schemas import (
    ContainerInfo, ContainerCreate, ContainerState, 
    PortMapping, VolumeMount, ImageInfo
)
from src.main import app

# The package re-exports its APIRouter as ``router``; import the module.
apps_router = importlib.import_module("src.apps.router")


class TestContainerSchemas:
    """Tests for container schemas."""

    def test_container_state_enum(self):
        """Test ContainerState enum values."""
        assert ContainerState.RUNNING == "running"
        assert ContainerState.EXITED == "exited"
        assert ContainerState.PAUSED == "paused"
        assert ContainerState.CREATED == "created"

    def test_port_mapping_schema(self):
        """Test PortMapping schema."""
        port = PortMapping(host_port=8080, container_port=80)
        assert port.host_port == 8080
        assert port.container_port == 80
        assert port.protocol == "tcp"

    def test_port_mapping_no_host_port(self):
        """Test PortMapping without host port."""
        port = PortMapping(container_port=80)
        assert port.host_port is None
        assert port.container_port == 80

    def test_port_mapping_udp(self):
        """Test PortMapping with UDP protocol."""
        port = PortMapping(container_port=53, protocol="udp")
        assert port.protocol == "udp"

    def test_volume_mount_schema(self):
        """Test VolumeMount schema."""
        vol = VolumeMount(host_path="/data", container_path="/app/data")
        assert vol.host_path == "/data"
        assert vol.container_path == "/app/data"
        assert vol.mode == "rw"

    def test_volume_mount_readonly(self):
        """Test VolumeMount with readonly mode."""
        vol = VolumeMount(host_path="/config", container_path="/etc/config", mode="ro")
        assert vol.mode == "ro"

    def test_container_info_schema(self):
        """Test ContainerInfo schema."""
        info = ContainerInfo(
            id="abc123",
            name="my_container",
            image="nginx:latest",
            status="Up 2 hours",
            state=ContainerState.RUNNING,
            created="2024-01-01T00:00:00Z",
            ports=[PortMapping(host_port=80, container_port=80)],
            labels={"app": "web"}
        )
        assert info.id == "abc123"
        assert info.name == "my_container"
        assert info.state == ContainerState.RUNNING
        assert len(info.ports) == 1

    def test_container_create_schema(self):
        """Test ContainerCreate schema."""
        create = ContainerCreate(
            name="my_app",
            image="nginx:latest",
            ports=[PortMapping(host_port=8080, container_port=80)],
            volumes=[VolumeMount(host_path="/data", container_path="/usr/share/nginx/html")],
            environment={"ENV": "production"},
            labels={"managed-by": "unos"}
        )
        assert create.name == "my_app"
        assert create.image == "nginx:latest"
        assert len(create.ports) == 1
        assert len(create.volumes) == 1
        assert create.environment == {"ENV": "production"}

    def test_container_create_defaults(self):
        """Test ContainerCreate default values."""
        create = ContainerCreate(name="simple", image="alpine")
        assert create.ports == []
        assert create.volumes == []
        assert create.environment == {}
        assert create.restart_policy == "unless-stopped"
        assert create.labels == {}

    def test_image_info_schema(self):
        """Test ImageInfo schema."""
        image = ImageInfo(
            id="sha256:abc123",
            tags=["nginx:latest", "nginx:1.25"],
            size=187000000,
            created="2024-01-01T00:00:00Z"
        )
        assert image.id == "sha256:abc123"
        assert len(image.tags) == 2
        assert image.size == 187000000


class TestDockerModule:
    """Tests for Docker operations."""

    @pytest.fixture
    def mock_docker_client(self):
        """Mock Docker client."""
        mock_client = MagicMock()
        return mock_client

    @pytest.fixture
    def mock_container(self):
        """Create a mock container."""
        container = MagicMock()
        container.short_id = "abc123"
        container.name = "test_container"
        container.status = "running"
        container.image.tags = ["nginx:latest"]
        container.image.short_id = "sha256:def456"
        container.attrs = {
            "Created": "2024-01-01T00:00:00Z",
            "NetworkSettings": {
                "Ports": {
                    "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]
                }
            }
        }
        container.labels = {"app": "test"}
        return container

    def test_list_containers(self, mock_docker_client, mock_container):
        """Test listing containers."""
        mock_docker_client.containers.list.return_value = [mock_container]
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            result = docker_module.list_containers()
        
        assert len(result) == 1
        assert result[0].id == "abc123"
        assert result[0].name == "test_container"
        assert result[0].state == ContainerState.RUNNING

    def test_list_containers_empty(self, mock_docker_client):
        """Test listing containers when none exist."""
        mock_docker_client.containers.list.return_value = []
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            result = docker_module.list_containers()
        
        assert result == []

    def test_get_container_success(self, mock_docker_client, mock_container):
        """Test getting a specific container."""
        mock_docker_client.containers.get.return_value = mock_container
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            result = docker_module.get_container("abc123")
        
        assert result.id == "abc123"
        assert result.name == "test_container"

    def test_get_container_not_found(self, mock_docker_client):
        """Test getting non-existent container."""
        from docker.errors import NotFound
        mock_docker_client.containers.get.side_effect = NotFound("not found")
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            with pytest.raises(ValueError, match="not found"):
                docker_module.get_container("nonexistent")

    def test_start_container(self, mock_docker_client, mock_container):
        """Test starting a container."""
        mock_docker_client.containers.get.return_value = mock_container
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            result = docker_module.start_container("abc123")

        mock_container.start.assert_called_once()
        mock_container.reload.assert_called_once()
        assert (result.id, result.name) == ("abc123", "test_container")

    def test_stop_container(self, mock_docker_client, mock_container):
        """Test stopping a container."""
        mock_docker_client.containers.get.return_value = mock_container
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            result = docker_module.stop_container("abc123")

        mock_container.stop.assert_called_once()
        assert (result.id, result.name) == ("abc123", "test_container")

    def test_restart_container(self, mock_docker_client, mock_container):
        """Test restarting a container."""
        mock_docker_client.containers.get.return_value = mock_container
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            result = docker_module.restart_container("abc123")

        mock_container.restart.assert_called_once()
        assert (result.id, result.name) == ("abc123", "test_container")

    def test_remove_container(self, mock_docker_client, mock_container):
        """Test removing a container."""
        mock_docker_client.containers.get.return_value = mock_container
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            docker_module.remove_container("abc123")
        
        mock_container.remove.assert_called_once_with(force=False)

    def test_remove_container_force(self, mock_docker_client, mock_container):
        """Test force removing a container."""
        mock_docker_client.containers.get.return_value = mock_container
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            docker_module.remove_container("abc123", force=True)
        
        mock_container.remove.assert_called_once_with(force=True)

    def test_get_container_logs(self, mock_docker_client, mock_container):
        """Test getting container logs."""
        mock_container.logs.return_value = b"2024-01-01 Log entry 1\n2024-01-01 Log entry 2"
        mock_docker_client.containers.get.return_value = mock_container
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            logs = docker_module.get_container_logs("abc123", tail=50)
        
        assert "Log entry 1" in logs
        mock_container.logs.assert_called_once_with(tail=50, timestamps=True)


class TestImageOperations:
    """Tests for Docker image operations."""

    @pytest.fixture
    def mock_docker_client(self):
        """Mock Docker client."""
        return MagicMock()

    @pytest.fixture
    def mock_image(self):
        """Create a mock image."""
        image = MagicMock()
        image.short_id = "sha256:abc123"
        image.tags = ["nginx:latest"]
        image.attrs = {
            "Size": 187000000,
            "Created": "2024-01-01T00:00:00Z"
        }
        return image

    def test_list_images(self, mock_docker_client, mock_image):
        """Test listing images."""
        mock_docker_client.images.list.return_value = [mock_image]
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            result = docker_module.list_images()
        
        assert len(result) == 1
        assert result[0].id == "sha256:abc123"
        assert result[0].tags == ["nginx:latest"]

    def test_list_images_empty(self, mock_docker_client):
        """Test listing images when none exist."""
        mock_docker_client.images.list.return_value = []
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            result = docker_module.list_images()
        
        assert result == []

    def test_pull_image(self, mock_docker_client, mock_image):
        """Test pulling an image."""
        mock_docker_client.images.pull.return_value = mock_image
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            result = docker_module.pull_image("nginx:latest")
        
        assert result.id == "sha256:abc123"
        mock_docker_client.images.pull.assert_called_once_with("nginx:latest")

    def test_remove_image(self, mock_docker_client):
        """Test removing an image."""
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            docker_module.remove_image("sha256:abc123")
        
        mock_docker_client.images.remove.assert_called_once_with("sha256:abc123", force=False)

    def test_remove_image_not_found(self, mock_docker_client):
        """Test removing non-existent image."""
        from docker.errors import NotFound
        mock_docker_client.images.remove.side_effect = NotFound("not found")
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            with pytest.raises(ValueError, match="not found"):
                docker_module.remove_image("nonexistent")


class TestPortParsing:
    """Tests for port mapping parsing."""

    def test_parse_tcp_port(self):
        """Test parsing TCP port mapping."""
        port = PortMapping(host_port=8080, container_port=80, protocol="tcp")
        assert port.protocol == "tcp"

    def test_parse_udp_port(self):
        """Test parsing UDP port mapping."""
        port = PortMapping(host_port=53, container_port=53, protocol="udp")
        assert port.protocol == "udp"

    def test_parse_exposed_only_port(self):
        """Test parsing exposed-only port (no host binding)."""
        port = PortMapping(container_port=3000)
        assert port.host_port is None
        assert port.container_port == 3000


class TestContainerStateMapping:
    """Tests for container state mapping."""

    def test_all_states_mapped(self):
        """Test that all Docker states are mapped."""
        states = ["running", "exited", "paused", "restarting", "created", "dead"]
        for state in states:
            assert ContainerState(state) is not None

    def test_running_state(self):
        """Test running state."""
        assert ContainerState.RUNNING.value == "running"

    def test_exited_state(self):
        """Test exited state."""
        assert ContainerState.EXITED.value == "exited"


# ---------------------------------------------------------------------------
# UNOS apps started from the Containers page get the Apps page's checks
# ---------------------------------------------------------------------------


@pytest.fixture
def api():
    previous = app.dependency_overrides.get(verify_token)
    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(
        id=1, username="admin", is_admin=True, is_active=True
    )
    try:
        yield TestClient(app)
    finally:
        if previous is None:
            app.dependency_overrides.pop(verify_token, None)
        else:
            app.dependency_overrides[verify_token] = previous


@pytest.fixture
def pool(tmp_path, monkeypatch) -> Path:
    """A pools base with the mounted pool "alpha" and a directory outside of it."""
    alpha = tmp_path / "pools" / "alpha"
    alpha.mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    monkeypatch.setattr(get_settings(), "pools_base_path", str(alpha.parent))
    monkeypatch.setattr(os.path, "ismount", lambda path: os.fspath(path) == str(alpha))
    return alpha


def _bind(source, target: str, *, rw: bool = True) -> dict:
    return {"Type": "bind", "Source": str(source), "Destination": target, "Mode": "", "RW": rw}


def _container(name: str, labels: dict, mounts: list[dict]) -> MagicMock:
    container = MagicMock(name=name)
    container.short_id = f"{name}-id"
    container.name = name
    container.status = "exited"
    container.image.tags = [f"example/{name}:latest"]
    container.labels = labels
    container.attrs = {"Created": "2026-01-02T03:04:05Z", "NetworkSettings": {"Ports": {}}, "Mounts": mounts}
    return container


def _jellyfin(pool: Path) -> MagicMock:
    return _container(
        "jellyfin",
        {"unos.app": "jellyfin", "unos.app.pool": pool.name},
        [
            _bind(pool / "apps/jellyfin/config", "/config"),
            _bind(pool / "apps/jellyfin/cache", "/cache"),
            _bind(pool / "media", "/media", rw=False),
        ],
    )


@pytest.fixture
def docker_client(monkeypatch):
    """A mocked Docker client that knows the containers passed to ``add``."""
    by_name: dict[str, MagicMock] = {}
    client = MagicMock(name="docker")

    def get(name):
        try:
            return by_name[name]
        except KeyError:
            raise NotFound(f"No such container: {name}") from None

    client.containers.get.side_effect = get
    monkeypatch.setattr(docker_module, "get_client", lambda: client)
    client.add = lambda container: by_name.setdefault(container.name, container)
    return client


class TestUnosAppStartChecks:
    @pytest.mark.parametrize("operation", ["start_container", "restart_container"])
    def test_checks_run_right_before_the_start(self, docker_client, monkeypatch, operation):
        container = docker_client.add(_jellyfin(Path("/mnt/pools/alpha")))
        checked = []

        def prepare_app_start(target, pools=None):
            assert not container.start.called and not container.restart.called
            checked.append(target)

        monkeypatch.setattr(apps_router, "prepare_app_start", prepare_app_start)

        getattr(docker_module, operation)("jellyfin")

        assert checked == [container]
        getattr(container, operation.split("_")[0]).assert_called_once_with()

    @pytest.mark.parametrize("operation", ["start_container", "restart_container"])
    def test_refused_app_is_not_started(self, docker_client, monkeypatch, operation):
        container = docker_client.add(_jellyfin(Path("/mnt/pools/alpha")))

        def refuse(target, pools=None):
            raise HTTPException(409, "not on a mounted storage pool")

        monkeypatch.setattr(apps_router, "prepare_app_start", refuse)

        with pytest.raises(HTTPException) as caught:
            getattr(docker_module, operation)("jellyfin")

        assert caught.value.status_code == 409
        container.start.assert_not_called()
        container.restart.assert_not_called()

    def test_other_containers_are_not_checked(self, docker_client, monkeypatch):
        container = docker_client.add(
            _container("nginx", {"unos.app.pool": "alpha"}, [_bind("/mnt/pools/alpha/www", "/www")])
        )
        prepare = MagicMock(side_effect=AssertionError("checked a container that is no UNOS app"))
        monkeypatch.setattr(apps_router, "prepare_app_start", prepare)

        docker_module.start_container("nginx")
        docker_module.restart_container("nginx")

        prepare.assert_not_called()
        container.start.assert_called_once_with()
        container.restart.assert_called_once_with()

    @pytest.mark.parametrize("action", ["start", "restart"])
    def test_planted_link_is_refused_with_409(self, api, pool, docker_client, action):
        # Docker follows links in bind sources when it starts the container.
        (pool / "apps/jellyfin/config").mkdir(parents=True)
        (pool / "apps/jellyfin/cache").mkdir(parents=True)
        (pool / "media").symlink_to(pool.parent.parent / "outside")
        container = docker_client.add(_jellyfin(pool))

        response = api.post(f"/api/v1/containers/jellyfin/{action}")

        assert response.status_code == 409
        assert "symbolic link" in response.json()["detail"]
        container.start.assert_not_called()
        container.restart.assert_not_called()

    def test_app_on_an_unmounted_pool_is_refused_with_409(self, api, pool, docker_client, monkeypatch):
        monkeypatch.setattr(os.path, "ismount", lambda path: False)
        container = docker_client.add(_jellyfin(pool))

        response = api.post("/api/v1/containers/jellyfin/start")

        assert response.status_code == 409
        assert "not on a mounted storage pool" in response.json()["detail"]
        container.start.assert_not_called()
        assert list(pool.iterdir()) == []

    @pytest.mark.parametrize("action", ["start", "restart"])
    def test_app_with_usable_folders_starts(self, api, pool, docker_client, action):
        (pool / "apps/jellyfin/config").mkdir(parents=True)
        container = docker_client.add(_jellyfin(pool))

        response = api.post(f"/api/v1/containers/jellyfin/{action}")

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "jellyfin"
        getattr(container, action).assert_called_once_with()
        # Missing folders on the app's own pool are created again.
        assert (pool / "apps/jellyfin/cache").is_dir()
        assert (pool / "media").is_dir()

    def test_unknown_container_is_still_404(self, api, docker_client):
        assert api.post("/api/v1/containers/nope/start").status_code == 404
        assert api.post("/api/v1/containers/nope/restart").status_code == 404


# ---------------------------------------------------------------------------
# Routing: static paths under /containers are not taken for a container id
# ---------------------------------------------------------------------------

containers_router = importlib.import_module("src.containers.router")


def _image(short_id: str, tags: list[str]) -> MagicMock:
    image = MagicMock(name=short_id)
    image.short_id = short_id
    image.tags = tags
    image.attrs = {"Size": 187000000, "Created": "2026-01-02T03:04:05Z"}
    return image


class TestRouting:
    def test_images_are_listed(self, api, docker_client):
        docker_client.images.list.return_value = [_image("sha256:abc123", ["nginx:latest"])]

        response = api.get("/api/v1/containers/images")

        assert response.status_code == 200, response.text
        assert response.json() == [
            {"id": "sha256:abc123", "tags": ["nginx:latest"], "size": 187000000, "created": "2026-01-02T03:04:05Z"}
        ]
        docker_client.containers.get.assert_not_called()

    def test_container_details_are_still_reachable(self, api, docker_client):
        docker_client.add(_container("jellyfin", {}, []))

        response = api.get("/api/v1/containers/jellyfin")

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "jellyfin"
        assert api.get("/api/v1/containers/nope").status_code == 404

    def test_image_pull_and_remove_reach_the_image_endpoints(self, api, docker_client):
        docker_client.images.pull.return_value = _image("sha256:def456", ["alpine:3.20"])
        docker_client.images.get.return_value = _image("sha256:def456", ["alpine:3.20"])

        pulled = api.post("/api/v1/containers/images/pull", params={"image": "alpine:3.20"})
        removed = api.delete("/api/v1/containers/images/sha256:def456")

        assert pulled.status_code == 200, pulled.text
        assert pulled.json()["id"] == "sha256:def456"
        docker_client.images.pull.assert_called_once_with("alpine:3.20")
        assert removed.status_code == 200, removed.text
        assert removed.json() == {"status": "removed"}
        docker_client.images.remove.assert_called_once_with("sha256:def456", force=False)
        docker_client.containers.get.assert_not_called()

    def test_no_route_is_shadowed_by_an_earlier_one(self):
        """Starlette tries the routes in order: each must be the first that its own path reaches."""
        routes = [route for route in containers_router.router.routes if isinstance(route, APIRoute)]
        assert len(routes) > 1
        for route in routes:
            path = re.sub(r"\{[^}]+\}", "sample-id", route.path)
            first = next(r for r in routes if r.methods & route.methods and r.path_regex.match(path))
            assert first is route, f"{sorted(route.methods)} {route.path} is reached by {first.path}"


# ---------------------------------------------------------------------------
# Docker calls run in worker threads (ADR-0021, ADR-0247)
# ---------------------------------------------------------------------------


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


# Every route of the containers API with a request that reaches Docker:
# (method, route path, request path, JSON body).
DOCKER_REQUESTS = [
    ("GET", "/containers", "/api/v1/containers", None),
    ("POST", "/containers", "/api/v1/containers", {"name": "web", "image": "nginx:latest"}),
    ("GET", "/containers/images", "/api/v1/containers/images", None),
    ("POST", "/containers/images/pull", "/api/v1/containers/images/pull?image=alpine:3.20", None),
    ("DELETE", "/containers/images/{image_id}", "/api/v1/containers/images/sha256:def456", None),
    ("GET", "/containers/{container_id}", "/api/v1/containers/jellyfin", None),
    ("POST", "/containers/{container_id}/start", "/api/v1/containers/jellyfin/start", None),
    ("POST", "/containers/{container_id}/stop", "/api/v1/containers/jellyfin/stop", None),
    ("POST", "/containers/{container_id}/restart", "/api/v1/containers/jellyfin/restart", None),
    ("DELETE", "/containers/{container_id}", "/api/v1/containers/jellyfin?force=true", None),
    ("GET", "/containers/{container_id}/logs", "/api/v1/containers/jellyfin/logs?tail=10", None),
]


def _answering_docker(docker_client) -> MagicMock:
    """Let the mocked Docker client answer every request of DOCKER_REQUESTS; returns the container."""
    container = docker_client.add(_container("jellyfin", {}, []))
    container.logs.return_value = b"2026-01-02T03:04:05Z ready\n"
    docker_client.containers.list.return_value = [container]
    docker_client.containers.run.return_value = SimpleNamespace(short_id="jellyfin")
    docker_client.images.list.return_value = [_image("sha256:def456", ["alpine:3.20"])]
    docker_client.images.pull.return_value = _image("sha256:def456", ["alpine:3.20"])
    docker_client.images.get.return_value = _image("sha256:def456", ["alpine:3.20"])
    return container


@pytest.fixture
def client_threads(docker_client, monkeypatch) -> list[bool]:
    """For each Docker client docker.py makes (one per operation): was it on the event loop?"""
    made: list[bool] = []

    def get_client():
        made.append(_on_event_loop())
        return docker_client

    monkeypatch.setattr(docker_module, "get_client", get_client)
    return made


class TestDockerCallsOffTheEventLoop:
    def test_the_table_has_every_route_of_the_containers_api(self):
        routes = {
            (method, route.path)
            for route in containers_router.router.routes
            if isinstance(route, APIRoute)
            for method in route.methods
        }
        assert routes == {(method, path) for method, path, _, _ in DOCKER_REQUESTS}

    @pytest.mark.parametrize(
        "method,url,body", [(m, u, b) for m, _, u, b in DOCKER_REQUESTS], ids=[f"{m} {p}" for m, p, _, _ in DOCKER_REQUESTS]
    )
    def test_each_route_calls_docker_in_a_worker_thread(self, api, docker_client, client_threads, method, url, body):
        _answering_docker(docker_client)

        response = api.request(method, url, json=body)

        assert response.status_code == 200, response.text
        assert client_threads, "the route never asked Docker"
        assert not any(client_threads), "a Docker call ran on the event loop"

    def test_the_client_waits_at_most_its_timeout_for_each_docker_request(self, monkeypatch):
        import docker

        made = []
        monkeypatch.setattr(docker, "from_env", lambda **kwargs: made.append(kwargs) or "client")

        assert docker_module.get_client() == "client"
        assert made == [{"timeout": docker_module.DOCKER_TIMEOUT_SECONDS}]
        assert 0 < docker_module.DOCKER_TIMEOUT_SECONDS <= 60


@pytest.fixture
def live_api(api):
    """The supervisor under a real uvicorn on 127.0.0.1: one event loop, as in the image.

    Requests through ``live_api.request(method, path, timeout)`` return the status code.
    The authentication override of ``api`` applies: every request is an administrator's.
    """
    import socket
    import time
    import urllib.error
    import urllib.request

    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"

    def request(method: str, path: str, timeout: float) -> int:
        try:
            with opener.open(urllib.request.Request(base + path, method=method), timeout=timeout) as answer:
                return answer.status
        except urllib.error.HTTPError as error:
            error.close()
            return error.code

    try:
        deadline = time.monotonic() + 30
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.05)
        yield SimpleNamespace(request=request)
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


class TestRequestsAnsweredWhileDockerBlocks:
    """A Docker call that blocks holds a worker thread, never the whole API (ADR-0021)."""

    @pytest.mark.parametrize(
        "method,url,blocked",
        [
            ("POST", "/api/v1/containers/images/pull?image=alpine:3.20", "pull"),
            ("POST", "/api/v1/containers/jellyfin/stop", "stop"),
            ("DELETE", "/api/v1/containers/jellyfin?force=true", "remove"),
            ("GET", "/api/v1/containers", "list"),
        ],
    )
    def test_health_and_other_requests_are_answered_meanwhile(self, live_api, docker_client, method, url, blocked):
        container = _answering_docker(docker_client)
        entered, release = threading.Event(), threading.Event()

        def hold(real):
            def call(*args, **kwargs):
                entered.set()
                assert release.wait(20), "the blocked Docker call was never released"
                return real

            return call

        if blocked == "pull":
            docker_client.images.pull.side_effect = hold(docker_client.images.pull.return_value)
        elif blocked == "stop":
            container.stop.side_effect = hold(None)
        elif blocked == "remove":
            container.remove.side_effect = hold(None)
        else:
            docker_client.containers.list.side_effect = hold([container])

        answered: dict[str, int] = {}
        slow = threading.Thread(target=lambda: answered.setdefault("slow", live_api.request(method, url, 30)))
        slow.start()
        try:
            assert entered.wait(10), f"{method} {url} did not reach Docker"
            for path in ("/api/v1/health", "/api/v1/apps/catalog"):
                try:
                    answered[path] = live_api.request("GET", path, 5)
                except OSError as error:  # the timeout: the event loop is held
                    pytest.fail(f"GET {path} was not answered while {blocked} blocked in Docker: {error!r}")
            assert not release.is_set() and slow.is_alive()  # the Docker call was still in flight
        finally:
            release.set()
            slow.join(30)

        assert answered == {"/api/v1/health": 200, "/api/v1/apps/catalog": 200, "slow": 200}


# ---------------------------------------------------------------------------
# Docker's refusals keep their meaning (ADR-0247)
# ---------------------------------------------------------------------------


def _docker_refusal(status: int, explanation: str) -> APIError:
    """An error of docker-py for an answer of the Docker API with ``status``."""
    response = requests.Response()
    response.status_code = status
    response.reason = "Conflict"
    response.url = "http+docker://localhost/v1.47/images/sha256:def456"
    return APIError("Conflict", response=response, explanation=explanation)


IN_USE = (
    "conflict: unable to delete def456def456 (cannot be forced) - "
    "image is being used by running container 0d1f2e3c4b5a"
)


class TestImageRemoval:
    def test_an_image_that_a_container_uses_is_refused_with_dockers_reason(self, api, docker_client):
        docker_client.images.get.return_value = _image("sha256:def456", ["nginx:1.27"])
        docker_client.images.remove.side_effect = _docker_refusal(409, IN_USE)

        response = api.delete("/api/v1/containers/images/sha256:def456")

        assert (response.status_code, response.json()) == (409, {"detail": IN_USE})
        docker_client.images.remove.assert_called_once_with("sha256:def456", force=False)

    def test_an_image_that_no_container_uses_is_removed(self, api, docker_client):
        docker_client.images.get.return_value = _image("sha256:def456", ["nginx:1.27"])

        response = api.delete("/api/v1/containers/images/sha256:def456")

        assert (response.status_code, response.json()) == (200, {"status": "removed"})
        docker_client.images.remove.assert_called_once_with("sha256:def456", force=False)

    def test_other_docker_errors_are_still_server_errors(self, api, docker_client):
        docker_client.images.get.return_value = _image("sha256:def456", ["nginx:1.27"])
        docker_client.images.remove.side_effect = APIError("driver failed to remove the layers")

        response = api.delete("/api/v1/containers/images/sha256:def456")

        assert response.status_code == 500
        assert "driver failed to remove the layers" in response.json()["detail"]
