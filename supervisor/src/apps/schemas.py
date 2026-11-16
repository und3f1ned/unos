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


class HardwareProfile(BaseModel):
    """Hardware of a SoC that a container of an app gets when it is installed on a host with that
    SoC (ADR-0382).

    Off that SoC the container is installed as on every host.
    """
    # Names the profile in the API; the App Store's hint is chosen by it.
    id: str
    # A name of the SoC in the host's device tree (/proc/device-tree/compatible).
    soc: str
    # Device nodes, or folders of them, passed into the container under the same paths, read
    # and write, when the host has them. The container stays unprivileged: its root opens
    # them whatever their owner and mode, and the device cgroup allows these nodes only.
    devices: list[str]
    # Those of the devices without which the container cannot use the hardware, or paths inside
    # a folder of them (a link in /dev/dri/by-path): the profile applies, and the catalog API
    # names it, only on a host that has every one of them.
    requires: list[str]
    # Paths that Docker masks in every unprivileged container and the app must read: the rest
    # of Docker's masks stay (hardware.DOCKER_MASKED_PATHS).
    unmasked_paths: list[str] = []
    # Run without Docker's AppArmor profile (docker-default), which denies reading what is
    # unmasked under /sys/firmware, where Docker applies one.
    apparmor_unconfined: bool = False
    # Where the profile applies, its container runs this image in place of its own: one built
    # for the hardware, pinned by tag and digest (ADR-0381). A profile shared by templates
    # names none; a template gives its copy one (model_copy). At most one profile of a
    # container names an image.
    image: str | None = None


class ContainerSpec(BaseModel):
    """What UNOS needs to create one container of an app (ADR-0381): the main container's are
    its template's own fields, a companion's its own."""
    image: str
    volumes: list[VolumeMapping] = []
    environment: list[EnvVar] = []
    # The hardware of the SoCs that the container gets (ADR-0382). The catalog API answers only
    # the profiles of the host's SoC, and the image of one of them that names one.
    hardware: list[HardwareProfile] = []


class Companion(ContainerSpec):
    """A companion container of an app, beside its main container (ADR-0381)."""
    # The container's name in the app, its DNS alias on the app's network: the container is
    # "<app id>-<name>".
    name: str


class AppTemplate(ContainerSpec):
    """An app of the catalog: its main container (image, volumes, environment and hardware of
    ContainerSpec, with the fields below) and its companion containers."""
    id: str
    name: str
    description: str
    icon: str
    category: AppCategory
    version: str
    website: str = ""
    source: AppSource = AppSource.COMMUNITY
    github: str = ""
    documentation: str = ""
    ports: list[PortMapping] = []
    restart_policy: str = "unless-stopped"
    privileged: bool = False
    network_mode: str | None = None
    capabilities: list[str] = []
    min_memory: str = "256m"  # Minimum recommended memory
    recommended_memory: str = "512m"
    # The app's other containers (ADR-0381).
    companions: list[Companion] = []

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
    # The host's device nodes passed into the container (ADR-0382), as Docker keeps them.
    devices: list[str] = []


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
    # The host's device nodes passed into the container (ADR-0382): none where no hardware
    # profile of the container applies, or where the host could not be read.
    devices: list[str] = []
