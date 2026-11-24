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


# Made-up digests and ids of the examples of the API documentation (ADR-0417); the image names
# are the catalog's.
_BOOKSTACK = "ghcr.io/linuxserver/bookstack"
_BOOKSTACK_DIGEST = "sha256:b24437fdf9955d4dc60c69f6cfff2947e19398dafb292c57e00af453796531f9"
_BOOKSTACK_NEWER_DIGEST = "sha256:d198e813699c5df38de4e7cf215ba4183ac6385b6ff36bb1fa20db41e598194d"
_MARIADB_DIGEST = "sha256:2b6d9296f58f8913554dc0e9db53e4c4317472b97f53ea75d03018b4f49fe637"
_BOOKSTACK_FOLDER = "/mnt/pools/data/apps/bookstack"


class PortMapping(BaseModel):
    """A port of an app's main container that the host publishes."""

    host: int = Field(description="The host's port that publishes it, unless the install request moves it.")
    container: int = Field(description="The container's port.")
    protocol: str = Field(default="tcp", description="Its protocol: tcp (the default) or udp.")


class VolumeMapping(BaseModel):
    """A folder or file of the host that the catalog binds into a container of an app."""

    host: str = Field(
        description="The host path: one that starts with {pool} is on the pool that the install chooses, with the "
                    "pool's mount point in place of {pool}; any other is a fixed path of the host, bound as it is."
    )
    container: str = Field(description="Where the container sees it.")
    readonly: bool = Field(default=False, description="Whether the container may only read it.")
    # On the pool a missing file is created empty, so Docker does not create a directory in its place.
    is_file: bool = Field(
        default=False,
        description="Whether it is one file (a database, a configuration file), not a folder; a missing one "
                           "on the pool is created empty, or with its content."
    )
    # The configuration that an image ships in a folder and needs at its start (Mosquitto's
    # mosquitto.conf, Prometheus's prometheus.yml): a bind of an empty folder there would hide it.
    content: str | None = Field(
        default=None,
        description="For a file (is_file): the text that a missing one on the pool is created with, such as the "
                    "configuration that the image ships at that path; an existing one is kept. null: created empty."
    )
    # Set it for images that run as a fixed non-root user: they cannot write to the root-owned folders
    # the supervisor creates, and crash-loop.
    uid: int | None = Field(
        default=None,
        description="The user id that the install gives the folder or file when it creates it on the pool; "
                          "null: root. An existing one, and the folders on the way, keep their owners."
    )
    gid: int | None = Field(
        default=None, description="The group id that the install gives it, as uid; null: root's group."
    )
    # Bound read-only and named by a *_FILE variable, LinuxServer's FILE__ one or the container's
    # command, so the password is in no Docker configuration (ADR-0381, ADR-0415).
    generated_secret: bool = Field(
        default=False,
        description="Whether it is a generated secret: a password file that the install writes when it is "
                           "missing (secret_digits hex digits, mode 0600), never at a start. Its host path cannot be "
                           "moved."
    )
    # Half as many random bytes; 32 for a key that the app takes as its 32 bytes (BookStack's
    # APP_KEY, ADR-0415).
    secret_digits: int = Field(
        default=64, ge=32, le=256, multiple_of=2,
        description="The hex digits of a generated secret: 64 by default, an even number from 32 to 256.",
    )
    # The data would not take a new password.
    secret_for: str | None = Field(
        default=None,
        description="For a generated secret: the template host path of the data folder that was set up with "
                          "it. An install that finds that folder not empty and the password file missing is refused. "
                          "null: none."
    )
    # A newer version may migrate it so that the earlier one cannot read it (ADR-0408).
    copy_before_update: bool = Field(
        default=False,
        description="Whether an app update makes a database copy of this folder beside it on the pool before "
                           "the new version starts, which an app rollback puts back."
    )

    @model_validator(mode="after")
    def _content_only_for_a_file_on_the_pool(self) -> "VolumeMapping":
        if self.content is not None and (not self.is_file or self.generated_secret or not is_pool_path(self.host)):
            raise ValueError(f"{self.container}: content is only for a file on the pool that is no generated secret")
        return self

    @computed_field  # type: ignore[prop-decorator]  # mypy takes no decorator above @property
    @property
    def on_pool(self) -> bool:
        """True if the source lives on the selected storage pool."""
        return is_pool_path(self.host)


class EnvVar(BaseModel):
    """A variable of a container's environment."""

    name: str = Field(description="The variable's name.")
    value: str = Field(default="", description="Its value unless the install request gives another.")
    description: str = Field(
        default="", description="What it is for, for the App Store; empty when the catalog says nothing."
    )
    required: bool = Field(default=False, description="Whether the install needs a value that is not empty.")
    secret: bool = Field(
        default=False,
        description="Whether it is a password, token or key: the App Store masks it, and an install refuses "
                           "well-known placeholders such as changeme."
    )
    # An override of it answers 400 (ADR-0381).
    editable: bool = Field(
        default=True,
        description="False for what UNOS sets (a companion's name, the path of a password file): the install "
                          "request cannot change it. PUID and PGID cannot be changed either, whatever it says: "
                          "the install option to run in the group unos-smb sets them."
    )


class HardwareProfile(BaseModel):
    """A hardware profile: the hardware of a SoC that a container of an app gets when it is
    installed on a host with that SoC. Off that SoC the container is installed as on every host."""
    # ADR-0382.
    # The App Store's hint is chosen by it.
    id: str = Field(description="The profile's name, such as rk3588-video.")
    soc: str = Field(description="The SoC, as the host's device tree names it (/proc/device-tree/compatible).")
    # The container stays unprivileged: its root opens them whatever their owner and mode, and the
    # device cgroup allows these nodes only.
    devices: list[str] = Field(
        description="Device nodes, or folders of them, passed into the container under the same paths, to read and "
                    "write, when the host has them."
    )
    # Such as a link in /dev/dri/by-path.
    requires: list[str] = Field(
        description="The devices, or paths inside a folder of them, that the host must have for the profile to apply."
    )
    # The rest of Docker's masks stay (hardware.DOCKER_MASKED_PATHS).
    unmasked_paths: list[str] = Field(
        default=[],
        description="Paths that Docker masks in every unprivileged container and that the container must read."
    )
    # Docker's docker-default denies reading what is unmasked under /sys/firmware.
    apparmor_unconfined: bool = Field(
        default=False, description="Whether the container runs without Docker's AppArmor profile."
    )
    # Pinned by tag and digest (ADR-0381). A profile shared by templates names none; a template
    # gives its copy one (model_copy). At most one profile of a container names an image.
    image: str | None = Field(
        default=None,
        description="The pinned image, built for the hardware, that the container runs in place of its own "
                          "where the profile applies; null: its own."
    )


class ContainerSpec(BaseModel):
    """What UNOS needs to create one container of an app (ADR-0381): the main container's are
    its template's own fields, a companion's its own."""
    image: str = Field(
        description="The pinned image that the container runs, name:tag@sha256:digest; in the catalog API's answer, "
                    "the one that it runs on this host."
    )
    volumes: list[VolumeMapping] = Field(
        default=[], description="The host's folders and files bound into the container."
    )
    environment: list[EnvVar] = Field(default=[], description="The variables of the container's environment.")
    # ADR-0382.
    hardware: list[HardwareProfile] = Field(
        default=[],
        description="Its hardware profiles. The catalog API answers only those that apply on this host, and in "
                        "image the image that the container runs here."
    )
    tmpfs: dict[str, str] = Field(
        default={}, description="Paths that the container writes and that are kept in RAM, not on the system disk: the "
                        "container path to Docker's tmpfs options, such as size=64m."
    )
    shm_size: str | None = Field(
        default=None, description="The size of the container's /dev/shm, such as 128m; null: 64 MB."
    )
    # Docker's StopTimeout, also at the host's shutdown.
    stop_timeout: int | None = Field(
        default=None,
        description="The seconds that Docker gives the container to stop before it kills it, and that a stop "
                          "of the app waits; null: 10."
    )
    # Linkwarden's reads its password files into the variables that it takes (ADR-0415).
    command: list[str] | None = Field(
        default=None, description="The container's command in place of its image's; null: the image's own."
    )
    # Docker's exec every stacks.POLL_SECONDS; nothing is written to the system disk while the app
    # runs (ADR-0415).
    ready_command: list[str] | None = Field(
        default=None,
        description="The ready command of a companion container whose image has no health check: UNOS runs it "
                          "in the container every 2 seconds while the next container waits for it, and the companion "
                          "is ready once it exits 0; null: none."
    )
    # Docker's NONE: every probe of a health check writes the container's state to the system disk
    # (ADR-0415).
    image_healthcheck: bool = Field(
        default=True, description="False: the container runs without its image's health check, and its health is null."
    )

    @computed_field(  # type: ignore[prop-decorator]  # mypy takes no decorator above @property
        description="The image platforms of its image, such as linux/arm64, as UNOS recorded them from the "
                    "registry; none for an image that nothing is recorded for."
    )
    @property
    def platforms(self) -> list[str]:
        """The platforms its image publishes, recorded from the registries (ADR-0403); none for
        an image that nothing is recorded for."""
        return image_platforms(self.image)


# The name of a companion: its DNS alias on the app's network and the end of its container's name.
COMPANION_NAME = r"^[a-z][a-z0-9-]*$"


class Companion(ContainerSpec):
    """A companion container of an app, beside its main container: it publishes no port, is never
    privileged, gets no capability and no override, and runs on the app's network."""
    # ADR-0381.
    name: str = Field(
        pattern=COMPANION_NAME,
        description="The companion's name in the app, its DNS name on the app's network; its container is named "
                    "<app id>-<name>.",
    )


class AppTemplate(ContainerSpec):
    """An app of the catalog: its main container (the image, volumes, environment and hardware
    profiles, with the fields below) and its companion containers."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "image": "jellyfin/jellyfin:12.4.20261026-211555@sha256:"
                 "bd5b9e25937cc592ebd34f13e7f35f278a14717469adb04f10f355ba05bf177c",
        "volumes": [
            {"host": "{pool}/apps/jellyfin/config", "container": "/config", "readonly": False, "is_file": False,
             "content": None, "uid": None, "gid": None, "generated_secret": False, "secret_digits": 64, "secret_for": None,
             "copy_before_update": False, "on_pool": True},
            {"host": "{pool}/media", "container": "/media", "readonly": True, "is_file": False, "content": None,
             "uid": None, "gid": None, "generated_secret": False, "secret_digits": 64, "secret_for": None,
             "copy_before_update": False, "on_pool": True},
        ],
        "environment": [
            {"name": "PUID", "value": "1000", "description": "", "required": False, "secret": False,
             "editable": True},
            {"name": "PGID", "value": "1000", "description": "", "required": False, "secret": False,
             "editable": True},
            {"name": "TZ", "value": "UTC", "description": "", "required": False, "secret": False, "editable": True},
        ],
        "hardware": [{
            "id": "rk3588-video", "soc": "rockchip,rk3588",
            "devices": ["/dev/dri", "/dev/dma_heap", "/dev/mpp_service", "/dev/rga", "/dev/mali0"],
            "requires": ["/dev/mpp_service"], "unmasked_paths": ["/sys/firmware"], "apparmor_unconfined": True,
            "image": None,
        }],
        "tmpfs": {}, "shm_size": None, "stop_timeout": None, "command": None, "ready_command": None,
        "image_healthcheck": True,
        "id": "jellyfin",
        "name": "Jellyfin",
        "description": "Free software media system - stream movies, shows, music with no subscriptions or tracking",
        "icon": "\U0001f3b5",
        "category": "media",
        "version": "12.4.20261026-211555",
        "website": "https://jellyfin.example.org",
        "source": "official",
        "github": "https://github.com/jellyfin/jellyfin",
        "documentation": "https://jellyfin.example.org/docs/",
        "ports": [{"host": 8096, "container": 8096, "protocol": "tcp"}],
        "restart_policy": "unless-stopped", "privileged": False, "network_mode": None, "capabilities": [],
        "min_memory": "1g", "recommended_memory": "4g",
        "companions": [], "depends_on": [], "host_gateway": False,
        "host_platform": "linux/arm64", "available": True, "images_bytes": 3005000000,
        "platforms": ["linux/amd64", "linux/arm64"], "can_run_in_unos_smb": False,
    }]})

    id: str = Field(description="The app's id in the catalog; its main container is named after it.")
    name: str = Field(description="The app's name.")
    description: str = Field(description="What the app does.")
    icon: str = Field(description="The emoji that the App Store shows for it.")
    category: AppCategory = Field(description="Its category in the App Store.")
    version: str = Field(description="The app's version in the catalog.")
    website: str = Field(default="", description="The project's website; empty when the catalog names none.")
    source: AppSource = Field(
        default=AppSource.COMMUNITY,
        description="Where its image comes from: linuxserver.io, official (Docker Hub's official images), verified "
                    "(a Docker Verified Publisher) or community (a well-known open source project).",
    )
    github: str = Field(default="", description="The project's source repository; empty when the catalog names none.")
    documentation: str = Field(
        default="", description="The project's documentation; empty when the catalog names none."
    )
    ports: list[PortMapping] = Field(default=[], description="The ports that the main container publishes on the host.")
    restart_policy: str = Field(
        default="unless-stopped", description="Docker's restart policy of the app's containers."
    )
    privileged: bool = Field(default=False, description="Whether the main container runs privileged.")
    network_mode: str | None = Field(
        default=None,
        description="Docker's network of the main container of an app without companions, such as host (the "
                          "NAS's network itself); null: Docker's default."
    )
    capabilities: list[str] = Field(
        default=[], description="Linux capabilities that the main container gets beyond Docker's."
    )
    min_memory: str = Field(default="256m", description="The least memory that the app needs, such as 512m or 1g.")
    recommended_memory: str = Field(default="512m", description="The memory recommended for the app, such as 2g.")
    # ADR-0381.
    companions: list[Companion] = Field(
        default=[],
        description="Its companion containers, created and started in this order before the main container and "
                        "stopped after it."
    )
    depends_on: list[str] = Field(
        default=[],
        description="The companions that must be ready (healthy, running without a health check, or answering "
                        "their ready command) before the next container starts, and so before the main container."
    )
    # Docker's host-gateway, as on Docker Desktop: Open WebUI's Ollama and Zigbee2MQTT's Mosquitto
    # (ADR-0415).
    host_gateway: bool = Field(
        default=False,
        description="Whether the main container reaches the NAS itself as host.docker.internal (the host "
                           "gateway), to reach a port that another app publishes there."
    )
    # Set by the catalog API for the host it runs on (ADR-0403).
    host_platform: str | None = Field(
        default=None,
        description="The host's platform, such as linux/arm64, as the catalog API names it; null when UNOS "
                          "cannot name it."
    )
    available: bool = Field(
        default=True,
        description="False when an image of the app publishes no build for the host's platform: such an app "
                          "cannot be installed here."
    )
    # The sum of the pins' estimates (PinnedImage.disk_bytes), set by the catalog API (ADR-0415).
    images_bytes: int | None = Field(
        default=None,
        description="About how many bytes the images that its install pulls on this host take where Docker keeps "
                          "its images, whether Docker has them or not, as the catalog API counts them."
    )

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

    @computed_field(  # type: ignore[prop-decorator]  # mypy takes no decorator above @property
        description="Whether the app can be installed as an app in unos-smb: only an app of one container from "
                    "linuxserver.io that takes PUID and PGID and has a folder on the pool besides its own data."
    )
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
    """A bind mount of an app's container, with the host path that it really uses."""
    host: str = Field(description="The host path that Docker binds.")
    container: str = Field(description="Where the container sees it.")
    readonly: bool = Field(default=False, description="Whether it is bound read-only.")
    companion: str | None = Field(
        default=None, description="The companion whose container has it; null: the main container."
    )


class ContainerResources(BaseModel):
    """A container's limits as Docker keeps them and its use as UNOS last read it."""
    # ADR-0390.
    # docker inspect's HostConfig.Memory, and CpuQuota / CpuPeriod.
    memory_limit: int | None = Field(
        default=None, description="Its memory limit, in bytes, as Docker keeps it; null: none."
    )
    cpu_limit: float | None = Field(default=None, description="Its CPU limit, in CPUs, as Docker keeps it; null: none.")
    # The cgroup's memory.current without inactive_file.
    memory_used: int | None = Field(
        default=None,
        description="The memory that it uses, in bytes, without the page cache that the kernel can drop; null "
                          "until UNOS read it, and while the container does not run."
    )
    cpu_used: float | None = Field(
        default=None,
        description="The CPUs that it used on average between UNOS's last two reads; null until UNOS read it "
                          "twice, and while the container does not run."
    )
    sampled_at: str | None = Field(
        default=None,
        description="When UNOS read its use, in ISO 8601 with the UTC offset (+00:00); null as memory_used."
    )


class ContainerLimitsRequest(BaseModel):
    """The container limits of one container of an app: null removes one. Both are required, so
    that a field left out never removes a limit."""
    # ADR-0390.
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [
        {"memory_limit": 1073741824, "cpu_limit": 1.5},
    ]})

    # From limits.MEMORY_FLOOR to below the host's memory.
    memory_limit: int | None = Field(
        description="The memory limit, in bytes: from 64 MiB to below the host's memory (GET /apps/limits); null "
                    "removes it."
    )
    # From limits.CPU_FLOOR to the host's CPUs.
    cpu_limit: float | None = Field(
        description="The CPU limit, in CPUs to the hundredth: from 0.1 to the host's CPUs; null removes it."
    )


class ContainerLimits(BaseModel):
    """The container limits of one container, as Docker keeps them after the change."""
    # ADR-0390.
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"container": "bookstack-database", "memory_limit": 1073741824, "cpu_limit": 1.5},
    ]})

    container: str = Field(description="The container's Docker name.")
    memory_limit: int | None = Field(default=None, description="Its memory limit, in bytes; null: none.")
    cpu_limit: float | None = Field(default=None, description="Its CPU limit, in CPUs; null: none.")


class LimitBounds(BaseModel):
    """What the container limits may be on this host."""
    # ADR-0390.
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"memory_min": 67108864, "memory_max": 16719384576, "cpu_min": 0.1, "cpu_max": 8.0},
    ]})

    memory_min: int = Field(description="The least memory limit, in bytes.")
    memory_max: int = Field(description="The host's memory, in bytes: a memory limit must be below it.")
    cpu_min: float = Field(description="The least CPU limit, in CPUs.")
    cpu_max: float = Field(description="The host's CPUs: the highest CPU limit.")


class CompanionContainer(BaseModel):
    """A companion container of an installed app, as Docker reports it."""
    # ADR-0381.
    name: str = Field(description="The companion's name in the app, its DNS name on the app's network.")
    container: str = Field(description="The container's Docker name, <app id>-<name>.")
    container_id: str = Field(description="Docker's short id of the container: 12 hex digits.")
    status: str = Field(description="Docker's status of the container, such as running or exited.")
    # Docker's State.Health.Status.
    health: str | None = Field(
        default=None,
        description="Docker's health of the container: starting, healthy or unhealthy; null without a health "
                          "check."
    )
    image: str = Field(description="The image the container was created from: name@sha256:... for a pinned one.")
    # ADR-0382.
    devices: list[str] = Field(default=[], description="The host's device nodes passed into the container.")
    # ADR-0390.
    resources: ContainerResources = Field(
        default=ContainerResources(), description="Its container limits and container use."
    )


def _resources(memory_used: int, cpu_used: float) -> dict:
    return {"memory_limit": None, "cpu_limit": None, "memory_used": memory_used, "cpu_used": cpu_used,
            "sampled_at": "2026-11-18T10:00:30.104512+00:00"}


class InstalledApp(BaseModel):
    """An installed app: the fields that name one container are its main container's; id and
    container_id name another of its containers only while the main one is missing."""
    # ADR-0381.

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "076a4a225c6b",
        "template_id": "bookstack",
        "name": "bookstack",
        "container_id": "076a4a225c6b",
        "status": "running",
        "web_port": 6875,
        "created_at": "2026-11-10T08:30:12.418273645Z",
        "pool": "data",
        "volumes": [
            {"host": f"{_BOOKSTACK_FOLDER}/config", "container": "/config", "readonly": False, "companion": None},
            {"host": f"{_BOOKSTACK_FOLDER}/app-key", "container": "/run/secrets/app-key", "readonly": True,
             "companion": None},
            {"host": f"{_BOOKSTACK_FOLDER}/database/data", "container": "/var/lib/mysql", "readonly": False,
             "companion": "database"},
        ],
        "data_outside_pools": [],
        "runs_in_unos_smb": False,
        "devices": [],
        "health": None,
        "companions": [{
            "name": "database", "container": "bookstack-database", "container_id": "d400bc09c2c9",
            "status": "running", "health": None, "image": f"mariadb@{_MARIADB_DIGEST}", "devices": [],
            "resources": _resources(157286400, 0.02),
        }],
        "missing": [],
        "resources": _resources(98566144, 0.01),
        "version": "v26.09.3-ls299",
        "update_to": "v26.10.1-ls301",
        "previous": None,
    }]})

    id: str = Field(
        description="Docker's short id of the main container (12 hex digits), or of the app's first other container "
                    "while the main one is missing."
    )
    template_id: str = Field(description="The app's id in the catalog.")
    name: str = Field(description="The main container's name, the app's id; the app's id while it is missing.")
    container_id: str = Field(description="The same as id.")
    status: str = Field(
        description="Docker's status of the main container, such as running or exited; missing when Docker has no "
                    "main container."
    )
    web_port: int | None = Field(
        default=None,
        description="The first host port that the main container publishes, which the App Store opens the app "
                          "at; null: none."
    )
    created_at: str = Field(description="When Docker created the container of id, as Docker writes it (UTC, with Z).")
    pool: str | None = Field(
        default=None,
        description="The pool chosen at the install for the app's data; null for an app without data on a pool."
    )
    volumes: list[AppVolume] = Field(
        default=[],
        description="The bind mounts of the main container, then of each companion, as Docker reports them (the "
                        "real host paths)."
    )
    data_outside_pools: list[str] = Field(
        default=[],
        description="Host paths of data folders that are on no mounted pool, because their pool is not mounted: "
                        "UNOS does not start such an app."
    )
    # ADR-0274.
    runs_in_unos_smb: bool = Field(
        default=False,
        description="Whether it is an app in unos-smb: its main container runs with the PGID 70000 of the "
                           "group unos-smb. Only such an app is given a share's folder."
    )
    # ADR-0382.
    devices: list[str] = Field(default=[], description="The host's device nodes passed into the main container.")
    # Docker's State.Health.Status.
    health: str | None = Field(
        default=None,
        description="Docker's health of the main container: starting, healthy or unhealthy; null without a "
                          "health check."
    )
    companions: list[CompanionContainer] = Field(
        default=[], description="Its companion containers, in the catalog's order."
    )
    missing: list[str] = Field(
        default=[],
        description="The names of the app's containers that the catalog has and Docker lacks: such an app does "
                        "not start, and an uninstall removes the rest."
    )
    # ADR-0390.
    resources: ContainerResources = Field(
        default=ContainerResources(), description="The main container's limits and use; all null while it is missing."
    )
    # The label unos.app.version (ADR-0408).
    version: str | None = Field(
        default=None,
        description="The catalog's version that the app's containers were created for; null for containers made "
                          "before UNOS recorded it."
    )
    # ADR-0408.
    update_to: str | None = Field(
        default=None,
        description="The catalog's version that an app update would move it to, when the catalog pins another "
                          "image for one of its containers than the one it runs; null: no update."
    )
    previous: "AppEarlierVersion | None" = Field(
        default=None,
        description="The earlier version that the app's last app update kept, until an app rollback or its "
                          "removal; null: none."
    )


class AppEarlierVersion(BaseModel):
    """The earlier version of an app that its last update kept: its containers, stopped and named
    <name>.previous, and the database copies made before the update."""
    # ADR-0408.
    version: str | None = Field(
        default=None,
        description="The catalog's version of its main container; null when it names none or has no main one."
    )
    containers: list[str] = Field(default=[], description="The names of its containers, each <name>.previous.")
    images: list[str] = Field(default=[], description="Their images, as Docker created them (name@sha256:...).")
    copies: list[str] = Field(
        default=[], description="The database copies (<folder>.previous) on mounted pools that are still there."
    )


InstalledApp.model_rebuild()


class ContainerUpdate(BaseModel):
    """One container of an app update."""
    # ADR-0408.
    name: str = Field(description="The container's Docker name.")
    companion: str | None = Field(
        default=None, description="The companion's name in the app; null: the main container."
    )
    image_from: str | None = Field(
        default=None, description="The image its container was created from; null for a container that the update adds."
    )
    image_to: str | None = Field(
        default=None,
        description="The catalog's pinned image that the update creates it from; null for a container that the "
                          "catalog no longer has, which stays with the earlier version."
    )
    changed: bool = Field(default=False, description="Whether it runs another image after the update.")
    built_from: UtcDatetime | None = Field(
        default=None,
        description="When the image that runs was built, in UTC; null when the catalog still pins it, for a "
                          "container that the update adds, or when the image names no time."
    )
    built_to: UtcDatetime | None = Field(
        default=None,
        description="When the catalog's image was built, in UTC, as its registry says; null as built_from."
    )


class DatabaseCopy(BaseModel):
    """A database copy: a data folder that an app update copies on its pool before the new version
    starts."""
    # ADR-0408.
    container: str = Field(description="The Docker name of the container whose data folder it is.")
    folder: str = Field(description="The data folder on the pool.")
    copy_folder: str = Field(description="Where the copy goes: the folder's path with .previous.")
    bytes: int = Field(description="What the folder takes on its disk, in bytes.")
    pool: str = Field(description="The pool of the folder.")
    pool_free_bytes: int = Field(description="The pool's free space now, in bytes.")


class AppUpdatePlan(BaseModel):
    """What an app update of an installed app would do."""
    # GET /apps/installed/{id}/update, ADR-0408.

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "template_id": "bookstack",
        "name": "BookStack",
        "version_from": "v26.09.3-ls299",
        "version_to": "v26.10.1-ls301",
        "platform": "linux/arm64",
        "containers": [
            {"name": "bookstack-database", "companion": "database", "image_from": f"mariadb@{_MARIADB_DIGEST}",
             "image_to": f"mariadb:11.8.9@{_MARIADB_DIGEST}", "changed": False, "built_from": None, "built_to": None},
            {"name": "bookstack", "companion": None, "image_from": f"{_BOOKSTACK}@{_BOOKSTACK_DIGEST}",
             "image_to": f"{_BOOKSTACK}:v26.10.1-ls301@{_BOOKSTACK_NEWER_DIGEST}", "changed": True,
             "built_from": "2026-09-28T04:12:55Z", "built_to": "2026-10-26T04:20:41Z"},
        ],
        "download_bytes": 95420416,
        "images_bytes": 241172480,
        "images_free_bytes": 1893792202752,
        "images_pool": "data",
        "copies": [{
            "container": "bookstack-database", "folder": f"{_BOOKSTACK_FOLDER}/database/data",
            "copy_folder": f"{_BOOKSTACK_FOLDER}/database/data.previous", "bytes": 241172480, "pool": "data",
            "pool_free_bytes": 1893792202752,
        }],
    }]})

    template_id: str = Field(description="The app's id in the catalog.")
    name: str = Field(description="The app's name.")
    version_from: str | None = Field(
        default=None,
        description="The catalog's version that the app was installed or updated to; null when its containers "
                          "name none."
    )
    version_to: str = Field(description="The catalog's version now, which the update installs.")
    platform: str = Field(description="The host's platform, whose images the update pulls.")
    containers: list[ContainerUpdate] = Field(
        description="Each container: the companions, the main container, then those that the catalog no longer has."
    )
    download_bytes: int = Field(description="The bytes of the compressed layers that Docker lacks, which the pulls "
                                            "download.")
    images_bytes: int = Field(
        description="About how many bytes the pulls write where Docker keeps its images: the downloads and the "
                    "unpacked layers."
    )
    images_free_bytes: int | None = Field(
        default=None,
        description="The free space now where Docker keeps its images, in bytes; null when the host lacks the folder."
    )
    images_pool: str | None = Field(
        default=None,
        description="The pool that holds Docker's images (containerd's root with the containerd image store, else "
                    "Docker's data root); null for the system disk (ADR-0490)."
    )
    copies: list[DatabaseCopy] = Field(default=[], description="The database copies that the update makes on the pool.")


class AppPool(BaseModel):
    """A mounted storage pool that can hold app data."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"name": "data", "path": "/mnt/pools/data", "free_bytes": 1893792202752, "total_bytes": 3999688294400},
    ]})

    name: str = Field(description="The pool's name.")
    path: str = Field(description="Where the pool is mounted.")
    free_bytes: int = Field(description="Its free space, in bytes.")
    total_bytes: int = Field(description="Its size, in bytes.")


class AppInstallRequest(BaseModel):
    """What to install: an app of the catalog, once, with the overrides of its main container."""
    # One instance per template: its containers and data folders are named after the template id
    # (ADR-0381); a field the request does not know, such as a name, answers 422.
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "template_id": "bookstack",
        "pool": "data",
        "port_overrides": {"80": 6876},
        "volume_overrides": {},
        "env_overrides": {"APP_URL": "http://192.0.2.10:6876", "TZ": "Europe/Berlin"},
        "run_in_unos_smb": False,
    }]})

    template_id: str = Field(description="The app's id in the catalog.")
    pool: str | None = Field(
        default=None, description="The pool for the app's data; null: the first mounted pool by name."
    )
    # The overrides change the main container only; companions take none (ADR-0381).
    port_overrides: dict[int, int] = Field(
        default={}, description="Host ports in place of the template's, by container port; of the main container only."
    )
    volume_overrides: dict[str, str] = Field(
        default={},
        description="Host paths in place of the template's, by container path, each inside a mounted pool; of "
                        "the main container only."
    )
    # ADR-0274.
    env_overrides: dict[str, str] = Field(
        default={},
        description="Values of the template's variables, by name; of the main container only. PUID, PGID and the "
                        "variables that UNOS sets cannot be given."
    )
    # ADR-0274.
    run_in_unos_smb: bool = Field(
        default=False,
        description="Install it as an app in unos-smb (PUID 80000, PGID 70000, UMASK 007), the only way to give "
                           "it a share's folder; only for an app whose can_run_in_unos_smb is true."
    )


class AppInstallResponse(BaseModel):
    """What an install job, an app update or an app rollback made of the app once it succeeded."""
    id: str = Field(description="Docker's short id of the main container: 12 hex digits.")
    name: str = Field(description="The main container's name, the app's id.")
    container_id: str = Field(description="The same as id.")
    status: str = Field(description="Docker's status of the main container right after its start, such as running.")
    web_url: str | None = Field(
        default=None,
        description="http://localhost:<host port> of the first port that the app publishes; null for an app "
                          "without ports, and after an update or a rollback."
    )
    pool: str | None = Field(
        default=None,
        description="The pool that holds the app's data; null when it keeps nothing on a pool, and after a "
                          "rollback."
    )
    volumes: list[AppVolume] = Field(
        default=[],
        description="The bind mounts of the main container, then of each companion; none after an update or a "
                        "rollback."
    )
    # ADR-0382.
    devices: list[str] = Field(
        default=[],
        description="The host's device nodes passed into the main container: none where no hardware profile "
                        "applies."
    )
    # Docker's State.Health.Status.
    health: str | None = Field(
        default=None,
        description="Docker's health of the main container right after its start (starting for an image with a "
                          "health check); null without a health check."
    )
    companions: list[CompanionContainer] = Field(
        default=[], description="Its companion containers, in their start order."
    )


class ImagePull(BaseModel):
    """The pull of one image of an install job, as Docker's pull stream reports it."""
    # ADR-0384.
    image: str = Field(description="The pinned image as the job pulls it: name:tag@sha256:...")
    state: Literal["waiting", "present", "pulling", "pulled", "failed"] = Field(
        default="waiting", description="waiting: not pulled yet; present: Docker has it, so it is not pulled; pulling; "
                               "pulled; failed."
    )
    layers: int = Field(default=0, description="The layers that Docker named so far.")
    layers_done: int = Field(
        default=0, description="Those of them that Docker has: downloaded and extracted, or there before."
    )
    bytes_total: int = Field(
        default=0, description="The bytes of the layers whose download size Docker reported so far."
    )
    bytes_done: int = Field(default=0, description="The bytes of them downloaded.")


class ContainerStart(BaseModel):
    """One container of an install job."""
    # ADR-0384.
    name: str = Field(description="The container's Docker name.")
    companion: str | None = Field(
        default=None, description="The companion's name in the app; null: the main container."
    )
    state: Literal["waiting", "created", "starting", "running", "failed", "removed"] = Field(
        default="waiting",
        description="waiting: not created yet; created; starting: asked to start, and for a companion that "
                               "the main container depends on, until it is ready; running; failed: the one whose "
                               "create, start or readiness failed; removed: by the clean-up of a failed install."
    )


class AppInstallJob(BaseModel):
    """An install job: an install, an app update or an app rollback that runs in the background,
    its progress, then its end. Kept in memory only: the running job and the last one of each app
    since the supervisor started."""
    # ADR-0384, ADR-0408.

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "fa10f920f8d19a15a36e00ce43289550",
        "template_id": "bookstack",
        "name": "BookStack",
        "kind": "install",
        "state": "running",
        "step": "pulling",
        "started_at": "2026-11-18T10:02:11.482113Z",
        "finished_at": None,
        "images": [
            {"image": f"mariadb:11.8.9@{_MARIADB_DIGEST}", "state": "pulled", "layers": 9, "layers_done": 9,
             "bytes_total": 104857600, "bytes_done": 104857600},
            {"image": f"{_BOOKSTACK}:v26.09.3-ls299@{_BOOKSTACK_DIGEST}", "state": "pulling", "layers": 12,
             "layers_done": 5, "bytes_total": 128974848, "bytes_done": 64487424},
        ],
        "containers": [
            {"name": "bookstack-database", "companion": "database", "state": "waiting"},
            {"name": "bookstack", "companion": None, "state": "waiting"},
        ],
        "result": None,
        "error": None,
        "error_status": None,
    }]})

    id: str = Field(description="The job's id: 32 hex digits.")
    template_id: str = Field(description="The app's id in the catalog.")
    name: str = Field(description="The app's name in the catalog.")
    kind: Literal["install", "update", "rollback"] = Field(
        default="install", description="install, update (an app update) or rollback (an app rollback)."
    )
    state: Literal["running", "succeeded", "failed", "cancelled"] = Field(
        default="running", description="running, succeeded, failed, or cancelled: an administrator cancelled it."
    )
    step: Literal[
        "checking", "pulling", "stopping", "copying", "creating", "starting", "cleaning", "restoring"
    ] = Field(
        default="checking",
        description="The step it reached: checking (the checks before any pull), pulling, stopping (the app, for an "
                    "update or a rollback), copying (the database copy), creating (the pool's folders, the network "
                    "and the containers), starting, cleaning (the removal of what a failed or cancelled install made) "
                    "or restoring (the earlier containers, for a rollback or a failed update).",
    )
    started_at: UtcDatetime = Field(description="When it started, in UTC.")
    finished_at: UtcDatetime | None = Field(default=None, description="When it ended, in UTC; null while it runs.")
    images: list[ImagePull] = Field(
        default=[],
        description="Each image once, in the order of the containers that run it; listed once the checks passed."
    )
    containers: list[ContainerStart] = Field(
        default=[], description="Its containers in their start order: the companions, then the main container."
    )
    result: AppInstallResponse | None = Field(
        default=None, description="What it made once it succeeded; null otherwise."
    )
    error: str | None = Field(default=None, description="Why it failed, or what its cancel did; null otherwise.")
    error_status: int | None = Field(
        default=None,
        description="For a failure, the HTTP status that its request would have answered, such as 400, 409, 500 "
                          "or 503; null otherwise."
    )
