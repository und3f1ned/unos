from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from ..times import UtcDatetime
from .platforms import image_platforms

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
    # A password file that the install creates when it is missing (secret_digits hexadecimal
    # digits, mode 0600), never at a start and never in place of one that is there; bound read-only
    # and named by a *_FILE variable, LinuxServer's FILE__ one or the container's command, so the
    # password is in no Docker configuration (ADR-0381, ADR-0414). Its host path is fixed: an
    # override cannot move it.
    generated_secret: bool = False
    # The hexadecimal digits of a generated secret, half as many random bytes: 64 by default; 32
    # for a key that the app takes as its 32 bytes (BookStack's APP_KEY, ADR-0414).
    secret_digits: int = Field(default=64, ge=32, le=256, multiple_of=2)
    # The template host path of the data folder that was set up with the password: an install
    # that finds that folder not empty and the password file missing is refused (409), never
    # given a new password, which the data would not take.
    secret_for: str | None = None
    # A folder of the app's own data that a newer version may migrate so that the earlier one
    # cannot read it (a database): an update copies it beside itself on the pool before the new
    # version starts, and a rollback puts the copy back (ADR-0408).
    copy_before_update: bool = False

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
    # False for wiring that UNOS sets (the name of a companion, the path of a password file): an
    # override of it answers 400 (ADR-0381).
    editable: bool = True


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
    # Paths that the container writes at run time and that are kept in RAM, not on the system
    # disk: the container path to Docker's tmpfs options ("size=64m").
    tmpfs: dict[str, str] = {}
    # The size of the container's /dev/shm ("128m"); None: Docker's 64 MB.
    shm_size: str | None = None
    # Seconds Docker gives the container to stop before it kills it (Docker's StopTimeout, also
    # at the host's shutdown), and that a stop of the app waits; None: 10.
    stop_timeout: int | None = None
    # The container's command in place of its image's CMD (Linkwarden's reads its password files
    # into the variables that it takes, ADR-0414); None: the image's own.
    command: list[str] | None = None
    # A companion's readiness when its image has no health check: a command that UNOS runs in the
    # container (Docker's exec) every POLL_SECONDS while the next container waits for it, ready
    # once it exits 0, such as a database's check that it takes connections (ADR-0414). Docker runs
    # nothing between the waits, so nothing is written to the system disk while the app runs.
    ready_command: list[str] | None = None
    # False: the container runs without the health check of its image (Docker's NONE), whose every
    # probe writes the container's state to the system disk while the app runs; its health is then
    # none (ADR-0414).
    image_healthcheck: bool = True

    @computed_field  # type: ignore[misc]
    @property
    def platforms(self) -> list[str]:
        """The platforms its image publishes, recorded from the registries (ADR-0403); none for
        an image that nothing is recorded for."""
        return image_platforms(self.image)


# The name of a companion: its DNS alias on the app's network and the end of its container's name.
COMPANION_NAME = r"^[a-z][a-z0-9-]*$"


class Companion(ContainerSpec):
    """A companion container of an app, beside its main container (ADR-0381): it publishes no
    port, is never privileged, gets no capability and no override, and runs on the app's network."""
    # The container's name in the app, its DNS alias on the app's network: the container is
    # "<app id>-<name>".
    name: str = Field(pattern=COMPANION_NAME)


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
    # The app's other containers (ADR-0381): created and started in this order before the main
    # container, and stopped after it.
    companions: list[Companion] = []
    # The names of the companions that must be ready (healthy, or running without a health check,
    # or answering their ready_command) before the next container starts, and so before the main
    # container.
    depends_on: list[str] = []
    # The main container resolves host.docker.internal to the host (Docker's host-gateway), as on
    # Docker Desktop, to reach an app that publishes a port on the host: Open WebUI's Ollama and
    # Zigbee2MQTT's Mosquitto (ADR-0414).
    host_gateway: bool = False
    # The host's platform and whether every container's image publishes it (ADR-0403): set by the
    # catalog API for the host it runs on. An app that is not available is not installable here.
    host_platform: str | None = None
    available: bool = True
    # About what the images that the install pulls on this host take on the system disk, the sum of
    # their pins' estimates (PinnedImage.disk_bytes) whether Docker has them or not: set by the
    # catalog API, like host_platform (ADR-0414).
    system_disk_bytes: int | None = None

    @model_validator(mode="after")
    def _companions_are_named_once(self) -> "AppTemplate":
        names = [companion.name for companion in self.companions]
        if len(set(names)) != len(names):
            raise ValueError(f"{self.id}: a companion name is given twice: {names}")
        unknown = [name for name in self.depends_on if name not in names]
        if unknown:
            raise ValueError(f"{self.id}: depends_on names no companion: {unknown}")
        idle = [c.name for c in self.companions if c.ready_command is not None and c.name not in self.depends_on]
        if self.ready_command is not None or idle:
            raise ValueError(f"{self.id}: a ready command is waited for only on a companion of depends_on: {idle}")
        if self.host_gateway and self.network_mode is not None:
            raise ValueError(f"{self.id}: an app in the network {self.network_mode} has no host gateway of its own")
        return self

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
        gain nothing from the group either. Nor can an app with companions
        (ADR-0381): the group would reach one of its containers only.
        """
        return (
            not self.companions
            and self.source is AppSource.LINUXSERVER
            and {"PUID", "PGID"} <= {var.name for var in self.environment}
            and any(volume.on_pool and not is_app_data_path(volume.host) for volume in self.volumes)
        )


class AppVolume(BaseModel):
    """A bind mount with the host path that is actually used."""
    host: str
    container: str
    readonly: bool = False
    # The companion whose container has the mount; None: the main container's.
    companion: str | None = None


class ContainerResources(BaseModel):
    """A container's limits as Docker keeps them and its use as UNOS last read it (ADR-0390)."""
    # The memory limit in bytes and the CPU limit in CPUs, as docker inspect shows them
    # (HostConfig.Memory, CpuQuota / CpuPeriod); None: no limit.
    memory_limit: int | None = None
    cpu_limit: float | None = None
    # The memory that the container uses (its cgroup's memory.current without the page cache that
    # the kernel can drop, inactive_file), in bytes, and the CPUs it used on average between the
    # last two reads; None until read, and for a container that does not run.
    memory_used: int | None = None
    cpu_used: float | None = None
    # When the use was read, in ISO 8601 with the UTC offset.
    sampled_at: str | None = None


class ContainerLimitsRequest(BaseModel):
    """The limits of one container of an app (ADR-0390): null removes one. Both are required, so
    that a field left out never removes a limit."""
    model_config = ConfigDict(extra="forbid")

    # Bytes; from limits.MEMORY_FLOOR to below the host's memory.
    memory_limit: int | None
    # CPUs, in hundredths; from limits.CPU_FLOOR to the host's CPUs.
    cpu_limit: float | None


class ContainerLimits(BaseModel):
    """The limits of one container, as Docker keeps them after a change (ADR-0390)."""
    container: str
    memory_limit: int | None = None
    cpu_limit: float | None = None


class LimitBounds(BaseModel):
    """What a container's limits may be on this host (ADR-0390)."""
    memory_min: int
    # The host's memory: a memory limit must be below it.
    memory_max: int
    cpu_min: float
    # The host's CPUs.
    cpu_max: float


class CompanionContainer(BaseModel):
    """A companion container of an installed app, as Docker reports it (ADR-0381)."""
    # The companion's name in the app (its DNS alias), and the container's name and short id.
    name: str
    container: str
    container_id: str
    status: str
    # Docker's State.Health.Status (starting, healthy, unhealthy); None without a health check.
    health: str | None = None
    # The image the container was created from (name@sha256:... for a pinned one).
    image: str
    # The host's device nodes passed into the container (ADR-0382).
    devices: list[str] = []
    # Its limits and its use (ADR-0390).
    resources: ContainerResources = ContainerResources()


class InstalledApp(BaseModel):
    """An installed app: the fields that name one container are its main container's
    (ADR-0381); ``id`` and ``container_id`` name a companion's only while the main one is missing."""
    id: str
    template_id: str
    name: str
    container_id: str
    # The main container's status, or "missing" when Docker has no main container.
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
    # The main container's Docker State.Health.Status; None without a health check.
    health: str | None = None
    # The app's companion containers, in the catalog's order.
    companions: list[CompanionContainer] = []
    # The names of the app's containers that the catalog has and Docker lacks: such an app does
    # not start, and an uninstall removes the rest.
    missing: list[str] = []
    # The main container's limits and use (ADR-0390); empty while it is missing.
    resources: ContainerResources = ContainerResources()
    # The catalog's version that the main container was created for (its label unos.app.version);
    # None for a container made before the label (ADR-0408).
    version: str | None = None
    # The catalog's version that the app can be updated to: the catalog pins another image for one
    # of its containers than the one it runs (ADR-0408); None: no update.
    update_to: str | None = None
    # The earlier version that the app's last update kept, until a rollback or its removal.
    previous: "AppEarlierVersion | None" = None


class AppEarlierVersion(BaseModel):
    """The earlier version of an app that its last update kept (ADR-0408): its containers, stopped
    and named ``<name>.previous``, and the copies of its database made before the update."""
    version: str | None = None
    containers: list[str] = []
    # The images of those containers, as Docker created them (name@sha256:...).
    images: list[str] = []
    # The copies of the app's database folders that are there.
    copies: list[str] = []


InstalledApp.model_rebuild()


class ContainerUpdate(BaseModel):
    """One container of an app's update (ADR-0408)."""
    name: str
    companion: str | None = None
    # The image its container was created from; None for a container that the update adds.
    image_from: str | None = None
    # The catalog's pinned image that the update creates it from; None for a container that the
    # catalog no longer has, which the update leaves with the earlier version.
    image_to: str | None = None
    # Whether it runs another image afterwards.
    changed: bool = False
    # When the image that runs and the catalog's were built, for an image that changes.
    built_from: UtcDatetime | None = None
    built_to: UtcDatetime | None = None


class DatabaseCopy(BaseModel):
    """A data folder that an update copies on its pool before the new version starts (ADR-0408)."""
    container: str
    folder: str
    copy_folder: str
    # What the folder takes on its disk, and what its pool has free.
    bytes: int
    pool: str
    pool_free_bytes: int


class AppUpdatePlan(BaseModel):
    """What an update of an installed app would do (GET /apps/installed/{id}/update, ADR-0408)."""
    template_id: str
    name: str
    version_from: str | None = None
    version_to: str
    # The host's platform, whose images it pulls.
    platform: str
    containers: list[ContainerUpdate]
    # The compressed layers that Docker lacks, which the pulls download, and what they take on
    # the system disk once pulled (the blobs and the unpacked layers), with its free space now.
    download_bytes: int
    system_disk_bytes: int
    system_disk_free_bytes: int | None = None
    copies: list[DatabaseCopy] = []


class AppPool(BaseModel):
    """A mounted storage pool that can hold app data."""
    name: str
    path: str
    free_bytes: int
    total_bytes: int


class AppInstallRequest(BaseModel):
    # One instance per template: its containers and data folders are named after the template id
    # (ADR-0381); a field the request does not know, such as a name, answers 422.
    model_config = ConfigDict(extra="forbid")

    template_id: str
    # Pool for the app's data; None selects the first mounted pool by name.
    pool: str | None = None
    # The overrides change the main container only; companions take none (ADR-0381).
    # Container port -> host port.
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
    # The main container's Docker State.Health.Status right after its start ("starting" for an
    # image with a health check); None without a health check.
    health: str | None = None
    # The app's companion containers, in the order they were started.
    companions: list[CompanionContainer] = []


class ImagePull(BaseModel):
    """The pull of one image of an install job, as Docker's pull stream reports it (ADR-0384)."""
    # The reference as the install pulls it: name:tag@sha256:... for a pinned image.
    image: str
    # waiting: not pulled yet; present: a pinned image that Docker has, not pulled; pulling;
    # pulled; failed.
    state: Literal["waiting", "present", "pulling", "pulled", "failed"] = "waiting"
    # The layers that Docker named so far, and those that it has (downloaded and extracted, or
    # already there).
    layers: int = 0
    layers_done: int = 0
    # The bytes of the layers whose download size Docker reported so far, and the bytes of them
    # downloaded.
    bytes_total: int = 0
    bytes_done: int = 0


class ContainerStart(BaseModel):
    """One container of an install job (ADR-0384)."""
    # Docker's name, and the companion's name in the app (None: the main container).
    name: str
    companion: str | None = None
    # waiting: not created yet; created; starting: asked to start, and for a companion that the
    # main container depends on, until it is ready; running; failed: the one whose create, start
    # or readiness failed; removed: by the clean-up of a failed install.
    state: Literal["waiting", "created", "starting", "running", "failed", "removed"] = "waiting"


class AppInstallJob(BaseModel):
    """An install of an app that runs in the background (ADR-0384): its progress, then its end.

    Kept in memory only: the running job and the last one of each app since the supervisor
    started. ``step`` is the step reached: checking (the checks before any pull), pulling,
    creating (the pool's folders, the network and the containers), starting, and cleaning (the
    removal of what a failed or cancelled install made). ``state`` cancelled: an administrator
    cancelled it (DELETE on the running job).
    """
    id: str
    template_id: str
    # The app's name in the catalog.
    name: str
    # An install, an update to the catalog's newer images or a rollback to the earlier version
    # that an update kept (ADR-0408).
    kind: Literal["install", "update", "rollback"] = "install"
    state: Literal["running", "succeeded", "failed", "cancelled"] = "running"
    # An update also stops the app, copies its database and, after a failure, restores it; a
    # rollback stops the newer containers and restores the earlier ones (ADR-0408).
    step: Literal[
        "checking", "pulling", "stopping", "copying", "creating", "starting", "cleaning", "restoring"
    ] = "checking"
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    # Each image once, in the order of the containers that run it; listed once the checks passed.
    images: list[ImagePull] = []
    # In their start order: the companions, then the main container.
    containers: list[ContainerStart] = []
    # What the install made, once it succeeded.
    result: AppInstallResponse | None = None
    # Why it failed or what its cancel did, and for a failure the HTTP status that its request
    # would have answered (400, 404, 409, 500, 503).
    error: str | None = None
    error_status: int | None = None
