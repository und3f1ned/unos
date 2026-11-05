from enum import Enum

from pydantic import BaseModel, computed_field

# Catalog host paths that start with this placeholder live on the storage pool
# chosen at install time; it is replaced with the pool's mount point (for
# example /mnt/pools/data). Any other host path is a fixed host resource
# (docker.sock, /dev/..., /proc, ...) that is mounted as-is and never created.
POOL_PLACEHOLDER = "{pool}"


def is_pool_path(host: str) -> bool:
    """Return True if a catalog host path is relative to the selected pool."""
    return host == POOL_PLACEHOLDER or host.startswith(POOL_PLACEHOLDER + "/")


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
    # The source is a single file (a database, a config file), not a
    # directory. On the pool a missing file is created empty, so Docker does
    # not create a directory in its place.
    is_file: bool = False
    # Owner given to the source when the supervisor creates it on the pool.
    # Set it for images that run as a fixed non-root user: they cannot write
    # to the root-owned folders the supervisor creates, and crash-loop. An
    # existing source and the parent folders are never changed.
    uid: int | None = None
    gid: int | None = None

    @computed_field  # type: ignore[misc]
    @property
    def on_pool(self) -> bool:
        """True if the source lives on the selected storage pool."""
        return is_pool_path(self.host)


class EnvVar(BaseModel):
    name: str
    value: str = ""
    description: str = ""
    required: bool = False
    # Password, token or key: the UI masks it, and well-known placeholder
    # values ("changeme", "admin", ...) are rejected at install time.
    secret: bool = False


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


class AppVolume(BaseModel):
    """A bind mount with the host path that is actually used."""
    host: str
    container: str
    readonly: bool = False


class InstalledApp(BaseModel):
    id: str
    template_id: str
    name: str
    container_id: str
    status: str
    web_port: int | None = None
    created_at: str
    # Pool chosen at install time. None for apps without data on a pool and
    # for apps installed by versions that did not let you choose a pool.
    pool: str | None = None
    # The container's bind mounts as Docker reports them (real host paths).
    volumes: list[AppVolume] = []
    # Host paths of data folders that are not on a mounted storage pool: the
    # app writes them to the system disk (apps installed by older versions
    # under /mnt/pools/default), or its pool is not mounted. UNOS does not
    # start such an app.
    data_outside_pools: list[str] = []


class AppPool(BaseModel):
    """A mounted storage pool that can hold app data."""
    name: str
    path: str
    free_bytes: int
    total_bytes: int


class AppInstallRequest(BaseModel):
    template_id: str
    name: str | None = None
    # Pool for the app's data; None selects the first mounted pool by name.
    pool: str | None = None
    port_overrides: dict[int, int] = {}
    # Container path -> host path; host paths must be inside a mounted pool.
    volume_overrides: dict[str, str] = {}
    env_overrides: dict[str, str] = {}


class AppInstallResponse(BaseModel):
    id: str
    name: str
    container_id: str
    status: str
    web_url: str | None = None
    # Pool holding the app's data (None if the app stores nothing on a pool).
    pool: str | None = None
    volumes: list[AppVolume] = []
