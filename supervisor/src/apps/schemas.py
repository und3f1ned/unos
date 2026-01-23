from pydantic import BaseModel
from enum import Enum


class AppCategory(str, Enum):
    MEDIA = "media"
    PRODUCTIVITY = "productivity"
    BACKUP = "backup"
    DOWNLOAD = "download"
    DEVELOPMENT = "development"
    MONITORING = "monitoring"
    NETWORK = "network"
    SECURITY = "security"
    HOME_AUTOMATION = "home_automation"
    COMMUNICATION = "communication"
    AI = "ai"
    PRIVACY = "privacy"
    GAMING = "gaming"
    DOCUMENTS = "documents"
    OTHER = "other"


class AppSource(str, Enum):
    """Verified source of the Docker image"""
    LINUXSERVER = "linuxserver.io"      # Trusted community images
    OFFICIAL = "official"                # Official Docker Hub images
    VERIFIED = "verified"                # Docker Verified Publisher
    COMMUNITY = "community"              # Well-known open source projects


class PortMapping(BaseModel):
    host: int
    container: int
    protocol: str = "tcp"


class VolumeMapping(BaseModel):
    host: str
    container: str
    readonly: bool = False


class EnvVar(BaseModel):
    name: str
    value: str = ""
    description: str = ""
    required: bool = False


class AppTemplate(BaseModel):
    id: str
    name: str
    description: str
    icon: str
    category: AppCategory
    image: str
    version: str
    website: str = ""
    source: AppSource = AppSource.COMMUNITY
    github: str = ""
    documentation: str = ""
    ports: list[PortMapping] = []
    volumes: list[VolumeMapping] = []
    environment: list[EnvVar] = []
    restart_policy: str = "unless-stopped"
    privileged: bool = False
    network_mode: str | None = None
    capabilities: list[str] = []
    min_memory: str = "256m"  # Minimum recommended memory
    recommended_memory: str = "512m"


class InstalledApp(BaseModel):
    id: str
    template_id: str
    name: str
    container_id: str
    status: str
    web_port: int | None = None
    created_at: str


class AppInstallRequest(BaseModel):
    template_id: str
    name: str | None = None
    port_overrides: dict[int, int] = {}
    volume_overrides: dict[str, str] = {}
    env_overrides: dict[str, str] = {}


class AppInstallResponse(BaseModel):
    id: str
    name: str
    container_id: str
    status: str
    web_url: str | None = None
