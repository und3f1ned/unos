from enum import Enum

from pydantic import BaseModel, computed_field

# Catalog host paths that start with this placeholder live on the storage pool
# chosen at install time; it is replaced with the pool's mount point (for
# example /mnt/pools/data). Any other host path is a fixed host resource
# (docker.sock, /dev/..., /proc, ...) that is mounted as-is and never created.
POOL_PLACEHOLDER = "{pool}"
# The folders of the apps' own data (configuration, databases): {pool}/apps/<app>/...
APPS_DATA_PATH = f"{POOL_PLACEHOLDER}/apps"


def is_pool_path(host: str) -> bool:
    """Return True if a catalog host path is relative to the selected pool."""
    return host == POOL_PLACEHOLDER or host.startswith(POOL_PLACEHOLDER + "/")


def is_app_data_path(host: str) -> bool:
    """Return True if a catalog host path holds the app's own data ({pool}/apps/...)."""
    return host.startswith(APPS_DATA_PATH + "/")


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

    @computed_field  # type: ignore[misc]
    @property
    def can_run_in_unos_smb(self) -> bool:
        """True if the app can be installed to run in the group unos-smb (ADR-0274).

        linuxserver.io images take the user and group of their processes
        from PUID and PGID and the umask of their services from UMASK. Other
        images ignore them, also where their template declares PGID
        (jellyfin's image runs as root, Plex's takes PLEX_GID), so they are
        never given a share's folder. An app whose every pool folder holds
        its own data ({pool}/apps), which is never a share's folder, would
        gain nothing from the group either.
        """
        return (
            self.source is AppSource.LINUXSERVER
            and {"PUID", "PGID"} <= {var.name for var in self.environment}
            and any(volume.on_pool and not is_app_data_path(volume.host) for volume in self.volumes)
        )


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
    # Pool chosen at install time. None for apps without data on a pool.
    pool: str | None = None
    # The container's bind mounts as Docker reports them (real host paths).
    volumes: list[AppVolume] = []
    # Host paths of data folders that are not on a mounted storage pool, because
    # their pool is not mounted. UNOS does not start such an app.
    data_outside_pools: list[str] = []
    # The app runs in the group unos-smb: its container's PGID is 70000
    # (ADR-0274). Only such an app is given a share's folder.
    runs_in_unos_smb: bool = False


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
    # Values of the template's variables; PUID and PGID are not among them
    # (ADR-0274).
    env_overrides: dict[str, str] = {}
    # Run the app in the group unos-smb (PUID 80000, PGID 70000, UMASK 007),
    # the only way to give it a share's folder; only for templates with
    # can_run_in_unos_smb (ADR-0274).
    run_in_unos_smb: bool = False


class AppInstallResponse(BaseModel):
    id: str
    name: str
    container_id: str
    status: str
    web_url: str | None = None
    # Pool holding the app's data (None if the app stores nothing on a pool).
    pool: str | None = None
    volumes: list[AppVolume] = []
