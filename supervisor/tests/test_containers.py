import pytest
from unittest.mock import patch, MagicMock
from src.containers import docker as docker_module
from src.containers.schemas import (
    ContainerInfo, ContainerCreate, ContainerState, 
    PortMapping, VolumeMount, ImageInfo
)


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

    def test_stop_container(self, mock_docker_client, mock_container):
        """Test stopping a container."""
        mock_docker_client.containers.get.return_value = mock_container
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            result = docker_module.stop_container("abc123")
        
        mock_container.stop.assert_called_once()

    def test_restart_container(self, mock_docker_client, mock_container):
        """Test restarting a container."""
        mock_docker_client.containers.get.return_value = mock_container
        
        with patch("src.containers.docker.get_client", return_value=mock_docker_client):
            result = docker_module.restart_container("abc123")
        
        mock_container.restart.assert_called_once()

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
