"""
UNOS App Catalog - Curated, trusted applications for self-hosting.

All applications are from verified sources:
- LinuxServer.io: Community-maintained, well-documented images
- Official: Official Docker Hub images from the project maintainers
- Verified: Docker Verified Publisher images
- Community: Well-known open-source projects with active maintenance

Sources:
- https://github.com/awesome-selfhosted/awesome-selfhosted
- https://github.com/linuxserver/docker-documentation
- https://apps.truenas.com/catalog/

Volume host paths:
- Paths under POOL ("{pool}/...") are on the storage pool the administrator
  picks at install time; the placeholder becomes the pool's mount point.
  Missing directories are created there (only while the pool is mounted).
- Any other absolute path is a fixed host resource (docker.sock, /dev, /proc,
  /etc/localtime, ...). It is mounted as-is, never created, and cannot be
  changed through install overrides.
- Mark single-file sources on the pool with is_file=True.
- Never put a pool path inside another app's (or the same app's) writable
  pool path, e.g. {pool}/downloads/watch next to {pool}/downloads. Docker
  resolves bind sources again on every start and follows symbolic links, so
  a process in a container that can write {pool}/downloads could replace
  "watch" with a link to "/" and get the host's root directory mounted into
  the other container at its next start. App-specific folders belong under
  {pool}/apps/<app>. The only exception is a read-write bind of the pool
  root itself for an image that runs as a non-root user (filebrowser): the
  pool root and the folders the supervisor creates are owned by root.
- Images that run as a fixed non-root user (USER in the Dockerfile, no
  root init that fixes permissions) need uid/gid on their writable pool
  folders; otherwise they cannot write to the root-owned folders the
  supervisor creates.
- linuxserver.io templates declare PUID and PGID (1000), which UNOS alone
  sets. Such an app with a pool folder outside {pool}/apps can be installed
  to run in the group unos-smb, which sets PUID, PGID and UMASK
  (AppTemplate.can_run_in_unos_smb, ADR-0274). Only those apps are given a
  share's folder.

Mark passwords, tokens and keys with secret=True and never ship a default
value for them; required secrets must be set by the administrator.

An app may have companion containers beside its main container, which the
template's own fields describe (ADR-0381): AppTemplate.companions, each a
Companion named ^[a-z][a-z0-9-]*$. The main container is named <id>, a
companion <id>-<name>, and all of them join the network unos-app-<id>, on
which each companion answers to its name: the main container reaches it by
that name (DB_HOSTNAME=database). The companions are created and started in
their order before the main container, which first waits for those named in
depends_on to be ready (healthy, or running if their image has no health
check). A companion publishes no port, is never privileged, gets no
capability and takes no override of the install; its pool folders are under
{pool}/apps/<id>/.

A password that containers of an app share is a file that UNOS generates on
the pool, never a variable: a VolumeMapping with generated_secret=True
(is_file, readonly, under {pool}/apps/<id>/, the same mapping in each
container that reads it), named there by a *_FILE variable, with secret_for
naming the data folder that was set up with it. Wiring that UNOS sets, such
as a companion's name or a password file's path, is EnvVar(editable=False).
Such an app asks the administrator for no secret.

The images of an app with companions are pinned: a PinnedImage
(name:tag@sha256 of the multi-platform index, as ADR-0305 pins the
Dockerfiles) with its estimated size on the system disk, listed in
PINNED_IMAGES. The templates whose images still float on a tag are listed in
UNPINNED_TEMPLATES, which may only shrink.

Nothing an app writes may land on the system disk, where Docker's data root
is (ADR-0381): every VOLUME that its image declares lies at or under a bind on
the pool or a tmpfs (an install refuses a pinned image whose VOLUME is not),
and a path that a container writes at run time outside its data (/tmp, a run
folder, a configuration made at every start) is kept in RAM with
ContainerSpec.tmpfs.

Hardware of a SoC that a container uses (a video engine, a GPU, an NPU) is a
HardwareProfile in that container's hardware: the template's for its main
container, a companion's own for it. On a host whose device tree names that SoC
and that has the nodes the profile requires (each a device of the profile or a
path inside a device folder of it), the container gets the profile's device
nodes that the host has, and the profile's image in place of its own if the
profile names one; elsewhere the container is installed as on every host
(src/apps/hardware.py, ADR-0382). A profile shared by templates names no image:
a template gives its copy one with model_copy, and a container has at most one
profile with an image.
"""

from dataclasses import dataclass

from .schemas import (
    APPS_DATA_PATH,
    POOL_PLACEHOLDER,
    AppCategory,
    AppSource,
    AppTemplate,
    Companion,
    EnvVar,
    HardwareProfile,
    PortMapping,
    VolumeMapping,
)

POOL = POOL_PLACEHOLDER

# The video hardware of the Rockchip RK3588 under Rockchip's kernel (the vendor kernels 5.10
# and 6.1), as Jellyfin's guide to RKMPP passes it into a container: the DRM nodes, the DMA
# heaps, the video codecs (MPP), the 2D engine (RGA) and the Mali GPU (OpenCL, for HDR tone
# mapping). Only Rockchip's kernel has MPP's /dev/mpp_service: without it (the mainline kernel)
# RKMPP cannot work, and the profile does not apply. Rockchip's MPP names the SoC from
# /proc/device-tree/compatible, a link into /sys/firmware, which Docker masks and its AppArmor
# profile denies (ADR-0382).
RK3588_VIDEO = HardwareProfile(
    id="rk3588-video",
    soc="rockchip,rk3588",
    devices=["/dev/dri", "/dev/dma_heap", "/dev/mpp_service", "/dev/rga", "/dev/mali0"],
    requires=["/dev/mpp_service"],
    unmasked_paths=["/sys/firmware"],
    apparmor_unconfined=True,
)

# The NPU of the Rockchip RK3588 under Rockchip's kernel: its RKNPU driver (Immich asks for 0.9.8
# or later) makes it a DRM device in /dev/dri, whose card and render numbers follow the probe order
# (renderD129 on the dev device); its render node's name in /dev/dri/by-path does not. The whole
# folder is passed, since the runtime's choice of node is not documented. Immich's -rknn image
# loads librknnrt and names the SoC from /proc/device-tree/compatible, a link into /sys/firmware,
# which Docker masks and its AppArmor profile denies (as for RK3588_VIDEO). The mainline kernel's
# NPU driver (rocket, /dev/accel) makes no such node, and the profile does not apply (ADR-0382).
RK3588_NPU = HardwareProfile(
    id="rk3588-npu",
    soc="rockchip,rk3588",
    devices=["/dev/dri"],
    requires=["/dev/dri/by-path/platform-fdab0000.npu-render"],
    unmasked_paths=["/sys/firmware"],
    apparmor_unconfined=True,
)


@dataclass(frozen=True)
class PinnedImage:
    """An image of the catalog pinned by tag and by the digest of its multi-platform index
    (ADR-0381, as ADR-0305 pins the Dockerfiles): pulled and run by digest whatever the tag names
    later."""
    name: str
    tag: str
    digest: str
    # About what it takes on the system disk once pulled, from the registry: its compressed layers
    # plus their unpacked size (the containerd image store keeps both). The install checks the
    # free space with it before a pull.
    disk_bytes: int

    @property
    def ref(self) -> str:
        return f"{self.name}:{self.tag}@{self.digest}"


# Immich's server and machine learning are one release: they move together (a test ties them).
# The digests are the indexes that Immich's release workflow pushed for v3.4.1, its latest release
# on 2026-11-16; Postgres and Valkey are pinned as Immich's own docker-compose.yml of v3.4.1 pins them.
IMMICH_VERSION = "v3.4.1"
IMMICH_SERVER = PinnedImage(
    "ghcr.io/immich-app/immich-server", IMMICH_VERSION,
    "sha256:7c489add298fc2f92cda4c777de6462aa206877e04087b815f62c7664af0b53b", 1_482_000_000,
)
# The machine learning for the CPU (amd64 and arm64). Its size is an estimate, about the -rknn
# image's: their compressed arm64 layers are 238 MB and 242 MB, and its unpacked size was not
# measured. The two images are built apart and share only 2 of their 11 and 12 arm64 layers, so
# both on one disk take about twice the size of one.
IMMICH_ML = PinnedImage(
    "ghcr.io/immich-app/immich-machine-learning", IMMICH_VERSION,
    "sha256:8adebf414256f6e4f837bfbb66fa94f14f4da7260e3a1d65cbba49e4e35d8764", 950_000_000,
)
# The machine learning for Rockchip's NPU (arm64 only), with librknnrt.
IMMICH_ML_RKNN = PinnedImage(
    "ghcr.io/immich-app/immich-machine-learning", f"{IMMICH_VERSION}-rknn",
    "sha256:b7baeb12cab0093803f09f0299b7c863bed46f36349c532096f40bfbf4f10c84", 947_000_000,
)
IMMICH_POSTGRES = PinnedImage(
    "ghcr.io/immich-app/postgres", "14-vectorchord0.4.3-pgvectors0.2.0",
    "sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23", 984_000_000,
)
VALKEY = PinnedImage(
    "docker.io/valkey/valkey", "9",
    "sha256:418652cfb58ef879d4978c33553735d7147016032d5aefaa14c828e611eb9dfd", 184_000_000,
)
# Every pinned image of the catalog, by reference.
PINNED_IMAGES: dict[str, PinnedImage] = {
    image.ref: image for image in (IMMICH_SERVER, IMMICH_ML, IMMICH_ML_RKNN, IMMICH_POSTGRES, VALKEY)
}

# Immich's database and the password it was set up with (ADR-0381).
IMMICH_DB_DATA = f"{APPS_DATA_PATH}/immich/postgres/data"
IMMICH_DB_PASSWORD = VolumeMapping(
    host=f"{APPS_DATA_PATH}/immich/postgres/password",
    container="/run/secrets/db-password",
    readonly=True,
    is_file=True,
    generated_secret=True,
    secret_for=IMMICH_DB_DATA,
)

APP_CATALOG: list[AppTemplate] = [
    # ═══════════════════════════════════════════════════════════════════════════
    # MEDIA - Streaming, Photos, Music
    # ═══════════════════════════════════════════════════════════════════════════
    
    AppTemplate(
        id="plex",
        name="Plex Media Server",
        description="Organize and stream your personal media collection with beautiful interfaces across all devices",
        icon="🎬",
        category=AppCategory.MEDIA,
        source=AppSource.OFFICIAL,
        image="plexinc/pms-docker:latest",
        version="latest",
        website="https://plex.tv",
        github="https://github.com/plexinc/pms-docker",
        documentation="https://support.plex.tv/articles/",
        min_memory="1g",
        recommended_memory="4g",
        ports=[PortMapping(host=32400, container=32400)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/plex/config", container="/config"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/plex/transcode", container="/transcode"),
            VolumeMapping(host=f"{POOL}/media", container="/data", readonly=True),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC", description="Timezone (e.g., America/New_York)"),
            EnvVar(name="PLEX_CLAIM", value="", description="Claim token from plex.tv/claim", secret=True),
        ],
    ),
    
    AppTemplate(
        id="jellyfin",
        name="Jellyfin",
        description="Free software media system - stream movies, shows, music with no subscriptions or tracking",
        icon="🎵",
        category=AppCategory.MEDIA,
        source=AppSource.OFFICIAL,
        image="jellyfin/jellyfin:latest",
        version="latest",
        website="https://jellyfin.org",
        github="https://github.com/jellyfin/jellyfin",
        documentation="https://jellyfin.org/docs/",
        min_memory="1g",
        recommended_memory="4g",
        ports=[PortMapping(host=8096, container=8096)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/jellyfin/config", container="/config"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/jellyfin/cache", container="/cache"),
            VolumeMapping(host=f"{POOL}/media", container="/media", readonly=True),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
        # The image runs as root and carries Rockchip's MPP and RGA libraries in its ffmpeg.
        hardware=[RK3588_VIDEO],
    ),
    
    AppTemplate(
        id="emby",
        name="Emby Server",
        description="Personal media server with DVR, live TV, and parental controls",
        icon="📺",
        category=AppCategory.MEDIA,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/emby:latest",
        version="latest",
        website="https://emby.media",
        github="https://github.com/linuxserver/docker-emby",
        documentation="https://emby.media/support/articles/Home.html",
        min_memory="1g",
        recommended_memory="4g",
        ports=[PortMapping(host=8920, container=8096)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/emby/config", container="/config"),
            VolumeMapping(host=f"{POOL}/media", container="/media", readonly=True),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="navidrome",
        name="Navidrome",
        description="Modern music server with Subsonic/Airsonic API compatibility - stream your music anywhere",
        icon="🎶",
        category=AppCategory.MEDIA,
        source=AppSource.COMMUNITY,
        image="deluan/navidrome:latest",
        version="latest",
        website="https://navidrome.org",
        github="https://github.com/navidrome/navidrome",
        documentation="https://www.navidrome.org/docs/",
        min_memory="128m",
        recommended_memory="512m",
        ports=[PortMapping(host=4533, container=4533)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/navidrome/data", container="/data"),
            VolumeMapping(host=f"{POOL}/music", container="/music", readonly=True),
        ],
        environment=[
            EnvVar(name="ND_SCANSCHEDULE", value="1h"),
            EnvVar(name="ND_LOGLEVEL", value="info"),
            EnvVar(name="ND_BASEURL", value=""),
        ],
    ),
    
    AppTemplate(
        id="photoprism",
        name="PhotoPrism",
        description="AI-powered photo management - browse, organize and share your photo collection",
        icon="📷",
        category=AppCategory.MEDIA,
        source=AppSource.OFFICIAL,
        image="photoprism/photoprism:latest",
        version="latest",
        website="https://photoprism.app",
        github="https://github.com/photoprism/photoprism",
        documentation="https://docs.photoprism.app/",
        min_memory="2g",
        recommended_memory="4g",
        ports=[PortMapping(host=2342, container=2342)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/photoprism/storage", container="/photoprism/storage"),
            VolumeMapping(host=f"{POOL}/photos", container="/photoprism/originals", readonly=True),
        ],
        environment=[
            EnvVar(name="PHOTOPRISM_ADMIN_PASSWORD", value="", description="Initial admin password", required=True, secret=True),
            EnvVar(name="PHOTOPRISM_SITE_URL", value="http://localhost:2342/"),
            EnvVar(name="PHOTOPRISM_ORIGINALS_LIMIT", value="5000", description="File size limit in MB"),
            EnvVar(name="PHOTOPRISM_HTTP_COMPRESSION", value="gzip"),
        ],
    ),
    
    # Immich of four containers (ADR-0381): its server is the main container, its database, Valkey
    # and machine learning are companions on the network unos-app-immich, where each answers to its
    # name. The database's password is a file UNOS generates on the pool, beside the database; the
    # server and the database read it through *_FILE (the database's entrypoint reads it as root,
    # before it drops to the postgres user). Every VOLUME of the images is on the pool, and what the
    # containers write at run time is in RAM: the server's /tmp, the gunicorn heartbeat in the
    # machine learning's /tmp, Postgres's configuration (made from DB_STORAGE_TYPE at every start)
    # and its socket. The machine learning runs on the RK3588's NPU where RK3588_NPU applies
    # (ADR-0382), and the server gets the video hardware for RKMPP, which the user chooses in Immich.
    AppTemplate(
        id="immich",
        name="Immich",
        description="High-performance self-hosted photo and video backup solution with mobile apps",
        icon="🖼️",
        category=AppCategory.MEDIA,
        source=AppSource.COMMUNITY,
        image=IMMICH_SERVER.ref,
        version=IMMICH_VERSION,
        website="https://immich.app",
        github="https://github.com/immich-app/immich",
        documentation="https://docs.immich.app/",
        min_memory="6g",
        recommended_memory="8g",
        ports=[PortMapping(host=2283, container=2283)],
        volumes=[
            # The image's VOLUME: the originals, thumbnails, encoded videos and the daily dumps of
            # the database (backups/).
            VolumeMapping(host=f"{APPS_DATA_PATH}/immich/library", container="/data"),
            VolumeMapping(host="/etc/localtime", container="/etc/localtime", readonly=True),
            IMMICH_DB_PASSWORD,
        ],
        environment=[
            EnvVar(name="DB_HOSTNAME", value="database", editable=False),
            EnvVar(name="DB_USERNAME", value="postgres", editable=False),
            EnvVar(name="DB_DATABASE_NAME", value="immich", editable=False),
            EnvVar(name="DB_PASSWORD_FILE", value=IMMICH_DB_PASSWORD.container, editable=False),
            EnvVar(name="REDIS_HOSTNAME", value="redis", editable=False),
            EnvVar(name="IMMICH_MACHINE_LEARNING_URL", value="http://machine-learning:3003", editable=False),
        ],
        tmpfs={"/tmp": "size=512m"},
        hardware=[RK3588_VIDEO],
        depends_on=["database", "redis"],
        companions=[
            Companion(
                name="database",
                image=IMMICH_POSTGRES.ref,
                volumes=[
                    VolumeMapping(host=IMMICH_DB_DATA, container="/var/lib/postgresql/data"),
                    IMMICH_DB_PASSWORD,
                ],
                environment=[
                    EnvVar(name="POSTGRES_USER", value="postgres"),
                    EnvVar(name="POSTGRES_DB", value="immich"),
                    EnvVar(name="POSTGRES_PASSWORD_FILE", value=IMMICH_DB_PASSWORD.container),
                    EnvVar(name="POSTGRES_INITDB_ARGS", value="--data-checksums"),
                    # The database is on a pool's hard disk: Immich's settings for one.
                    EnvVar(name="DB_STORAGE_TYPE", value="HDD"),
                ],
                tmpfs={"/etc/postgresql": "size=1m,mode=1777", "/var/run/postgresql": "size=1m"},
                shm_size="128m",
                stop_timeout=60,
            ),
            Companion(
                name="redis",
                image=VALKEY.ref,
                # Its snapshots of the queued jobs; the image declares no VOLUME for its /data.
                volumes=[VolumeMapping(host=f"{APPS_DATA_PATH}/immich/redis", container="/data")],
            ),
            Companion(
                name="machine-learning",
                image=IMMICH_ML.ref,
                volumes=[VolumeMapping(host=f"{APPS_DATA_PATH}/immich/model-cache", container="/cache")],
                # At v2 the models have files for the RK3588 for every task, OCR included.
                environment=[EnvVar(name="MACHINE_LEARNING_MODEL_REVISION", value="v2")],
                tmpfs={"/tmp": "size=64m"},
                hardware=[RK3588_NPU.model_copy(update={"image": IMMICH_ML_RKNN.ref})],
            ),
        ],
    ),
    
    AppTemplate(
        id="audiobookshelf",
        name="Audiobookshelf",
        description="Self-hosted audiobook and podcast server with mobile apps and progress syncing",
        icon="📚",
        category=AppCategory.MEDIA,
        source=AppSource.COMMUNITY,
        image="ghcr.io/advplyr/audiobookshelf:latest",
        version="latest",
        website="https://audiobookshelf.org",
        github="https://github.com/advplyr/audiobookshelf",
        documentation="https://www.audiobookshelf.org/docs",
        min_memory="256m",
        recommended_memory="1g",
        ports=[PortMapping(host=13378, container=80)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/audiobookshelf/config", container="/config"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/audiobookshelf/metadata", container="/metadata"),
            VolumeMapping(host=f"{POOL}/audiobooks", container="/audiobooks", readonly=True),
            VolumeMapping(host=f"{POOL}/podcasts", container="/podcasts"),
        ],
    ),
    
    AppTemplate(
        id="calibre-web",
        name="Calibre-Web",
        description="Web app for browsing, reading and downloading eBooks from your Calibre library",
        icon="📖",
        category=AppCategory.MEDIA,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/calibre-web:latest",
        version="latest",
        website="https://github.com/janeczku/calibre-web",
        github="https://github.com/janeczku/calibre-web",
        documentation="https://github.com/janeczku/calibre-web/wiki",
        min_memory="256m",
        recommended_memory="512m",
        ports=[PortMapping(host=8083, container=8083)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/calibre-web/config", container="/config"),
            VolumeMapping(host=f"{POOL}/books", container="/books"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="kavita",
        name="Kavita",
        description="Fast, feature-rich, cross-platform reading server for comics, manga, and books",
        icon="📕",
        category=AppCategory.MEDIA,
        source=AppSource.COMMUNITY,
        image="jvmilazz0/kavita:latest",
        version="latest",
        website="https://kavitareader.com",
        github="https://github.com/Kareadita/Kavita",
        documentation="https://wiki.kavitareader.com/",
        min_memory="256m",
        recommended_memory="1g",
        ports=[PortMapping(host=5000, container=5000)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/kavita/config", container="/kavita/config"),
            VolumeMapping(host=f"{POOL}/manga", container="/manga", readonly=True),
            VolumeMapping(host=f"{POOL}/comics", container="/comics", readonly=True),
        ],
    ),
    
    # === MEDIA AUTOMATION (arr stack) ===
    
    AppTemplate(
        id="sonarr",
        name="Sonarr",
        description="Smart TV series management - automatic download, organization, and metadata",
        icon="📺",
        category=AppCategory.MEDIA,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/sonarr:latest",
        version="latest",
        website="https://sonarr.tv",
        github="https://github.com/Sonarr/Sonarr",
        documentation="https://wiki.servarr.com/sonarr",
        min_memory="256m",
        recommended_memory="1g",
        ports=[PortMapping(host=8989, container=8989)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/sonarr/config", container="/config"),
            VolumeMapping(host=f"{POOL}/media/tv", container="/tv"),
            VolumeMapping(host=f"{POOL}/downloads", container="/downloads"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="radarr",
        name="Radarr",
        description="Movie collection manager - automatic download and organization",
        icon="🎥",
        category=AppCategory.MEDIA,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/radarr:latest",
        version="latest",
        website="https://radarr.video",
        github="https://github.com/Radarr/Radarr",
        documentation="https://wiki.servarr.com/radarr",
        min_memory="256m",
        recommended_memory="1g",
        ports=[PortMapping(host=7878, container=7878)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/radarr/config", container="/config"),
            VolumeMapping(host=f"{POOL}/media/movies", container="/movies"),
            VolumeMapping(host=f"{POOL}/downloads", container="/downloads"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="lidarr",
        name="Lidarr",
        description="Music collection manager - automatic download and organization",
        icon="🎤",
        category=AppCategory.MEDIA,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/lidarr:latest",
        version="latest",
        website="https://lidarr.audio",
        github="https://github.com/Lidarr/Lidarr",
        documentation="https://wiki.servarr.com/lidarr",
        min_memory="256m",
        recommended_memory="1g",
        ports=[PortMapping(host=8686, container=8686)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/lidarr/config", container="/config"),
            VolumeMapping(host=f"{POOL}/music", container="/music"),
            VolumeMapping(host=f"{POOL}/downloads", container="/downloads"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="readarr",
        name="Readarr",
        description="Book and audiobook collection manager",
        icon="📗",
        category=AppCategory.MEDIA,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/readarr:develop",
        version="develop",
        website="https://readarr.com",
        github="https://github.com/Readarr/Readarr",
        documentation="https://wiki.servarr.com/readarr",
        min_memory="256m",
        recommended_memory="1g",
        ports=[PortMapping(host=8787, container=8787)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/readarr/config", container="/config"),
            VolumeMapping(host=f"{POOL}/books", container="/books"),
            VolumeMapping(host=f"{POOL}/downloads", container="/downloads"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="prowlarr",
        name="Prowlarr",
        description="Indexer manager for Sonarr, Radarr, Lidarr - centralized tracker management",
        icon="🔎",
        category=AppCategory.MEDIA,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/prowlarr:latest",
        version="latest",
        website="https://prowlarr.com",
        github="https://github.com/Prowlarr/Prowlarr",
        documentation="https://wiki.servarr.com/prowlarr",
        min_memory="128m",
        recommended_memory="512m",
        ports=[PortMapping(host=9696, container=9696)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/prowlarr/config", container="/config"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="bazarr",
        name="Bazarr",
        description="Automatic subtitle download companion for Sonarr and Radarr",
        icon="💬",
        category=AppCategory.MEDIA,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/bazarr:latest",
        version="latest",
        website="https://bazarr.media",
        github="https://github.com/morpheus65535/bazarr",
        documentation="https://wiki.bazarr.media/",
        min_memory="128m",
        recommended_memory="512m",
        ports=[PortMapping(host=6767, container=6767)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/bazarr/config", container="/config"),
            VolumeMapping(host=f"{POOL}/media/movies", container="/movies"),
            VolumeMapping(host=f"{POOL}/media/tv", container="/tv"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="overseerr",
        name="Overseerr",
        description="Request management and media discovery for Plex, Radarr, and Sonarr",
        icon="🎫",
        category=AppCategory.MEDIA,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/overseerr:latest",
        version="latest",
        website="https://overseerr.dev",
        github="https://github.com/sct/overseerr",
        documentation="https://docs.overseerr.dev/",
        min_memory="256m",
        recommended_memory="1g",
        ports=[PortMapping(host=5055, container=5055)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/overseerr/config", container="/config"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="tautulli",
        name="Tautulli",
        description="Monitoring and tracking tool for Plex Media Server with detailed statistics",
        icon="📈",
        category=AppCategory.MEDIA,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/tautulli:latest",
        version="latest",
        website="https://tautulli.com",
        github="https://github.com/Tautulli/Tautulli",
        documentation="https://github.com/Tautulli/Tautulli/wiki",
        min_memory="128m",
        recommended_memory="256m",
        ports=[PortMapping(host=8181, container=8181)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/tautulli/config", container="/config"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    # ═══════════════════════════════════════════════════════════════════════════
    # DOWNLOAD - BitTorrent, Usenet, Download Managers
    # ═══════════════════════════════════════════════════════════════════════════
    
    AppTemplate(
        id="transmission",
        name="Transmission",
        description="Lightweight, reliable BitTorrent client with web interface",
        icon="⬇️",
        category=AppCategory.DOWNLOAD,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/transmission:latest",
        version="latest",
        website="https://transmissionbt.com",
        github="https://github.com/transmission/transmission",
        documentation="https://github.com/transmission/transmission/wiki",
        min_memory="128m",
        recommended_memory="512m",
        ports=[
            PortMapping(host=9091, container=9091),
            PortMapping(host=51413, container=51413),
            PortMapping(host=51413, container=51413, protocol="udp"),
        ],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/transmission/config", container="/config"),
            VolumeMapping(host=f"{POOL}/downloads", container="/downloads"),
            # Not {pool}/downloads/watch: many apps can write to downloads and
            # could swap the folder for a link (see the module docstring).
            VolumeMapping(host=f"{APPS_DATA_PATH}/transmission/watch", container="/watch"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
            # Without both, the image turns RPC authentication off at every
            # start: anyone who reaches port 9091 controls the client,
            # including the script it runs when a torrent completes.
            EnvVar(name="USER", value="", description="Login for the web interface", required=True),
            EnvVar(name="PASS", value="", description="Password for the web interface", required=True, secret=True),
        ],
    ),

    AppTemplate(
        id="qbittorrent",
        name="qBittorrent",
        description="Feature-rich BitTorrent client with advanced options and web UI",
        icon="📥",
        category=AppCategory.DOWNLOAD,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/qbittorrent:latest",
        version="latest",
        website="https://qbittorrent.org",
        github="https://github.com/qbittorrent/qBittorrent",
        documentation="https://github.com/qbittorrent/qBittorrent/wiki",
        min_memory="256m",
        recommended_memory="1g",
        ports=[
            PortMapping(host=8080, container=8080),
            PortMapping(host=6881, container=6881),
            PortMapping(host=6881, container=6881, protocol="udp"),
        ],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/qbittorrent/config", container="/config"),
            VolumeMapping(host=f"{POOL}/downloads", container="/downloads"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
            EnvVar(name="WEBUI_PORT", value="8080"),
        ],
    ),
    
    AppTemplate(
        id="deluge",
        name="Deluge",
        description="Lightweight, cross-platform BitTorrent client with plugin support",
        icon="🌊",
        category=AppCategory.DOWNLOAD,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/deluge:latest",
        version="latest",
        website="https://deluge-torrent.org",
        github="https://github.com/deluge-torrent/deluge",
        documentation="https://dev.deluge-torrent.org/wiki",
        min_memory="256m",
        recommended_memory="512m",
        ports=[
            PortMapping(host=8112, container=8112),
            PortMapping(host=6882, container=6881),
            PortMapping(host=6882, container=6881, protocol="udp"),
        ],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/deluge/config", container="/config"),
            VolumeMapping(host=f"{POOL}/downloads", container="/downloads"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="sabnzbd",
        name="SABnzbd",
        description="Free and easy Usenet download manager for NZB files",
        icon="📰",
        category=AppCategory.DOWNLOAD,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/sabnzbd:latest",
        version="latest",
        website="https://sabnzbd.org",
        github="https://github.com/sabnzbd/sabnzbd",
        documentation="https://sabnzbd.org/wiki/",
        min_memory="256m",
        recommended_memory="1g",
        ports=[PortMapping(host=8085, container=8080)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/sabnzbd/config", container="/config"),
            VolumeMapping(host=f"{POOL}/downloads", container="/downloads"),
            # Not {pool}/downloads/incomplete: see the transmission watch folder.
            VolumeMapping(host=f"{APPS_DATA_PATH}/sabnzbd/incomplete", container="/incomplete-downloads"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="nzbget",
        name="NZBGet",
        description="Efficient Usenet downloader with web interface",
        icon="📨",
        category=AppCategory.DOWNLOAD,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/nzbget:latest",
        version="latest",
        website="https://nzbget.com",
        github="https://github.com/nzbgetcom/nzbget",
        documentation="https://nzbget.com/documentation/",
        min_memory="128m",
        recommended_memory="512m",
        ports=[PortMapping(host=6789, container=6789)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/nzbget/config", container="/config"),
            VolumeMapping(host=f"{POOL}/downloads", container="/downloads"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="jdownloader2",
        name="JDownloader 2",
        description="Multi-host download manager for file-sharing sites",
        icon="⏬",
        category=AppCategory.DOWNLOAD,
        source=AppSource.COMMUNITY,
        image="jlesage/jdownloader-2:latest",
        version="latest",
        website="https://jdownloader.org",
        github="https://github.com/jlesage/docker-jdownloader-2",
        documentation="https://github.com/jlesage/docker-jdownloader-2#readme",
        min_memory="512m",
        recommended_memory="1g",
        ports=[PortMapping(host=5800, container=5800)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/jdownloader2/config", container="/config"),
            VolumeMapping(host=f"{POOL}/downloads", container="/output"),
        ],
    ),
    
    AppTemplate(
        id="aria2",
        name="Aria2 Pro",
        description="Ultra fast download utility with BitTorrent and Metalink support",
        icon="⚡",
        category=AppCategory.DOWNLOAD,
        source=AppSource.COMMUNITY,
        image="p3terx/aria2-pro:latest",
        version="latest",
        website="https://aria2.github.io",
        github="https://github.com/P3TERX/Aria2-Pro-Docker",
        documentation="https://p3terx.com/archives/docker-aria2-pro.html",
        min_memory="64m",
        recommended_memory="256m",
        ports=[
            PortMapping(host=6800, container=6800),
            PortMapping(host=6888, container=6888),
            PortMapping(host=6888, container=6888, protocol="udp"),
        ],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/aria2/config", container="/config"),
            VolumeMapping(host=f"{POOL}/downloads", container="/downloads"),
        ],
        environment=[
            EnvVar(name="RPC_SECRET", value="", description="RPC secret token", secret=True),
            EnvVar(name="RPC_PORT", value="6800"),
        ],
    ),
    
    # ═══════════════════════════════════════════════════════════════════════════
    # PRODUCTIVITY - File Management, Notes, Office
    # ═══════════════════════════════════════════════════════════════════════════
    
    AppTemplate(
        id="nextcloud",
        name="Nextcloud",
        description="Self-hosted productivity platform - files, calendar, contacts, and more",
        icon="☁️",
        category=AppCategory.PRODUCTIVITY,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/nextcloud:latest",
        version="latest",
        website="https://nextcloud.com",
        github="https://github.com/nextcloud/server",
        documentation="https://docs.nextcloud.com/",
        min_memory="512m",
        recommended_memory="2g",
        ports=[PortMapping(host=8443, container=443)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/nextcloud/config", container="/config"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/nextcloud/data", container="/data"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="syncthing",
        name="Syncthing",
        description="Continuous file synchronization between devices - secure and decentralized",
        icon="🔄",
        category=AppCategory.PRODUCTIVITY,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/syncthing:latest",
        version="latest",
        website="https://syncthing.net",
        github="https://github.com/syncthing/syncthing",
        documentation="https://docs.syncthing.net/",
        min_memory="128m",
        recommended_memory="512m",
        ports=[
            PortMapping(host=8384, container=8384),
            PortMapping(host=22000, container=22000),
            PortMapping(host=22000, container=22000, protocol="udp"),
            PortMapping(host=21027, container=21027, protocol="udp"),
        ],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/syncthing/config", container="/config"),
            VolumeMapping(host=f"{POOL}/sync", container="/data"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="filebrowser",
        name="File Browser",
        description="Modern web file manager with sharing, users, and customization",
        icon="📁",
        category=AppCategory.PRODUCTIVITY,
        source=AppSource.OFFICIAL,
        image="filebrowser/filebrowser:latest",
        version="latest",
        website="https://filebrowser.org",
        github="https://github.com/filebrowser/filebrowser",
        documentation="https://filebrowser.org/features",
        min_memory="64m",
        recommended_memory="256m",
        ports=[PortMapping(host=8081, container=80)],
        # Current images keep the database in /database and settings.json in
        # /config (the old /database.db and /.filebrowser.json are not used).
        # The image runs as "user" (uid/gid 1000) and its init script copies
        # the default settings into /config, so both folders must be owned by
        # that user. The pool root (/srv) stays root-owned: browsing works,
        # and the app cannot rename the folders other apps are bound to.
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/filebrowser/database", container="/database", uid=1000, gid=1000),
            VolumeMapping(host=f"{APPS_DATA_PATH}/filebrowser/config", container="/config", uid=1000, gid=1000),
            VolumeMapping(host=POOL, container="/srv"),
        ],
    ),
    
    AppTemplate(
        id="trilium",
        name="Trilium Notes",
        description="Hierarchical note-taking app with focus on knowledge building",
        icon="📝",
        category=AppCategory.PRODUCTIVITY,
        source=AppSource.COMMUNITY,
        image="zadam/trilium:latest",
        version="latest",
        website="https://github.com/zadam/trilium",
        github="https://github.com/zadam/trilium",
        documentation="https://github.com/zadam/trilium/wiki",
        min_memory="256m",
        recommended_memory="512m",
        ports=[PortMapping(host=8082, container=8080)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/trilium/data", container="/home/node/trilium-data"),
        ],
    ),
    
    AppTemplate(
        id="wikijs",
        name="Wiki.js",
        description="Modern and powerful wiki app built on Node.js with beautiful interface",
        icon="📖",
        category=AppCategory.DOCUMENTS,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/wikijs:latest",
        version="latest",
        website="https://js.wiki",
        github="https://github.com/requarks/wiki",
        documentation="https://docs.requarks.io/",
        min_memory="256m",
        recommended_memory="512m",
        ports=[PortMapping(host=3000, container=3000)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/wikijs/config", container="/config"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/wikijs/data", container="/data"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="stirling-pdf",
        name="Stirling PDF",
        description="Self-hosted web-based PDF manipulation tool - merge, split, convert, compress",
        icon="📋",
        category=AppCategory.DOCUMENTS,
        source=AppSource.COMMUNITY,
        image="frooodle/s-pdf:latest",
        version="latest",
        website="https://stirlingtools.com",
        github="https://github.com/Stirling-Tools/Stirling-PDF",
        documentation="https://github.com/Stirling-Tools/Stirling-PDF#readme",
        min_memory="256m",
        recommended_memory="1g",
        ports=[PortMapping(host=8086, container=8080)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/stirling-pdf/config", container="/configs"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/stirling-pdf/logs", container="/logs"),
        ],
        environment=[
            EnvVar(name="DOCKER_ENABLE_SECURITY", value="false"),
        ],
    ),
    
    # ═══════════════════════════════════════════════════════════════════════════
    # MONITORING & DASHBOARDS
    # ═══════════════════════════════════════════════════════════════════════════
    
    AppTemplate(
        id="portainer",
        name="Portainer CE",
        description="Powerful Docker management UI - visualize and manage containers, images, networks",
        icon="🐳",
        category=AppCategory.MONITORING,
        source=AppSource.VERIFIED,
        image="portainer/portainer-ce:latest",
        version="latest",
        website="https://portainer.io",
        github="https://github.com/portainer/portainer",
        documentation="https://docs.portainer.io/",
        min_memory="64m",
        recommended_memory="256m",
        ports=[PortMapping(host=9000, container=9000)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/portainer/data", container="/data"),
            VolumeMapping(host="/var/run/docker.sock", container="/var/run/docker.sock"),
        ],
    ),
    
    AppTemplate(
        id="uptime-kuma",
        name="Uptime Kuma",
        description="Self-hosted monitoring tool with beautiful status pages and notifications",
        icon="📊",
        category=AppCategory.MONITORING,
        source=AppSource.COMMUNITY,
        image="louislam/uptime-kuma:latest",
        version="latest",
        website="https://uptime.kuma.pet",
        github="https://github.com/louislam/uptime-kuma",
        documentation="https://github.com/louislam/uptime-kuma/wiki",
        min_memory="128m",
        recommended_memory="256m",
        ports=[PortMapping(host=3001, container=3001)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/uptime-kuma/data", container="/app/data"),
        ],
    ),
    
    AppTemplate(
        id="homepage",
        name="Homepage",
        description="Modern application dashboard with 100+ service integrations and widgets",
        icon="🏠",
        category=AppCategory.MONITORING,
        source=AppSource.COMMUNITY,
        image="ghcr.io/gethomepage/homepage:latest",
        version="latest",
        website="https://gethomepage.dev",
        github="https://github.com/gethomepage/homepage",
        documentation="https://gethomepage.dev/configs/",
        min_memory="128m",
        recommended_memory="256m",
        ports=[PortMapping(host=3002, container=3000)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/homepage/config", container="/app/config"),
            VolumeMapping(host="/var/run/docker.sock", container="/var/run/docker.sock", readonly=True),
        ],
    ),
    
    AppTemplate(
        id="homarr",
        name="Homarr",
        description="Customizable browser homepage with Docker integration and widgets",
        icon="🎨",
        category=AppCategory.MONITORING,
        source=AppSource.COMMUNITY,
        image="ghcr.io/ajnart/homarr:latest",
        version="latest",
        website="https://homarr.dev",
        github="https://github.com/ajnart/homarr",
        documentation="https://homarr.dev/docs/getting-started/",
        min_memory="128m",
        recommended_memory="256m",
        ports=[PortMapping(host=7575, container=7575)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/homarr/configs", container="/app/data/configs"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/homarr/icons", container="/app/public/icons"),
            VolumeMapping(host="/var/run/docker.sock", container="/var/run/docker.sock", readonly=True),
        ],
    ),
    
    AppTemplate(
        id="heimdall",
        name="Heimdall",
        description="Application dashboard with enhanced app support and clean interface",
        icon="🔱",
        category=AppCategory.MONITORING,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/heimdall:latest",
        version="latest",
        website="https://heimdall.site",
        github="https://github.com/linuxserver/Heimdall",
        documentation="https://github.com/linuxserver/Heimdall#readme",
        min_memory="64m",
        recommended_memory="128m",
        ports=[PortMapping(host=8087, container=80)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/heimdall/config", container="/config"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),
    
    AppTemplate(
        id="grafana",
        name="Grafana",
        description="Open-source analytics and visualization platform with dashboards",
        icon="📉",
        category=AppCategory.MONITORING,
        source=AppSource.OFFICIAL,
        image="grafana/grafana:latest",
        version="latest",
        website="https://grafana.com",
        github="https://github.com/grafana/grafana",
        documentation="https://grafana.com/docs/grafana/latest/",
        min_memory="256m",
        recommended_memory="512m",
        ports=[PortMapping(host=3003, container=3000)],
        volumes=[
            # The image runs as grafana (uid 472, gid 0).
            VolumeMapping(host=f"{APPS_DATA_PATH}/grafana/data", container="/var/lib/grafana", uid=472, gid=0),
        ],
        environment=[
            EnvVar(name="GF_SECURITY_ADMIN_PASSWORD", value="", description="Admin password", required=True, secret=True),
        ],
    ),
    
    AppTemplate(
        id="prometheus",
        name="Prometheus",
        description="Monitoring system and time series database for metrics collection",
        icon="🔥",
        category=AppCategory.MONITORING,
        source=AppSource.OFFICIAL,
        image="prom/prometheus:latest",
        version="latest",
        website="https://prometheus.io",
        github="https://github.com/prometheus/prometheus",
        documentation="https://prometheus.io/docs/",
        min_memory="256m",
        recommended_memory="1g",
        ports=[PortMapping(host=9090, container=9090)],
        volumes=[
            # The image runs as nobody (uid/gid 65534); it writes its TSDB to /prometheus.
            VolumeMapping(host=f"{APPS_DATA_PATH}/prometheus/config", container="/etc/prometheus", uid=65534, gid=65534),
            VolumeMapping(host=f"{APPS_DATA_PATH}/prometheus/data", container="/prometheus", uid=65534, gid=65534),
        ],
    ),
    
    AppTemplate(
        id="dozzle",
        name="Dozzle",
        description="Real-time Docker container log viewer in your browser",
        icon="📜",
        category=AppCategory.MONITORING,
        source=AppSource.COMMUNITY,
        image="amir20/dozzle:latest",
        version="latest",
        website="https://dozzle.dev",
        github="https://github.com/amir20/dozzle",
        documentation="https://dozzle.dev/guide/what-is-dozzle",
        min_memory="32m",
        recommended_memory="64m",
        ports=[PortMapping(host=8088, container=8080)],
        volumes=[
            VolumeMapping(host="/var/run/docker.sock", container="/var/run/docker.sock", readonly=True),
        ],
    ),
    
    AppTemplate(
        id="netdata",
        name="Netdata",
        description="Real-time performance monitoring with zero configuration",
        icon="⚙️",
        category=AppCategory.MONITORING,
        source=AppSource.OFFICIAL,
        image="netdata/netdata:latest",
        version="latest",
        website="https://netdata.cloud",
        github="https://github.com/netdata/netdata",
        documentation="https://learn.netdata.cloud/",
        min_memory="256m",
        recommended_memory="512m",
        ports=[PortMapping(host=19999, container=19999)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/netdata/config", container="/etc/netdata"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/netdata/lib", container="/var/lib/netdata"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/netdata/cache", container="/var/cache/netdata"),
            VolumeMapping(host="/etc/passwd", container="/host/etc/passwd", readonly=True),
            VolumeMapping(host="/etc/group", container="/host/etc/group", readonly=True),
            VolumeMapping(host="/proc", container="/host/proc", readonly=True),
            VolumeMapping(host="/sys", container="/host/sys", readonly=True),
        ],
        capabilities=["SYS_PTRACE"],
    ),
    
    AppTemplate(
        id="glances",
        name="Glances",
        description="Cross-platform system monitoring tool with web interface",
        icon="👁️",
        category=AppCategory.MONITORING,
        source=AppSource.COMMUNITY,
        image="nicolargo/glances:latest-full",
        version="latest-full",
        website="https://nicolargo.github.io/glances/",
        github="https://github.com/nicolargo/glances",
        documentation="https://glances.readthedocs.io/",
        min_memory="64m",
        recommended_memory="128m",
        ports=[PortMapping(host=61208, container=61208)],
        volumes=[
            VolumeMapping(host="/var/run/docker.sock", container="/var/run/docker.sock", readonly=True),
        ],
        environment=[
            EnvVar(name="GLANCES_OPT", value="-w"),
        ],
    ),
    
    AppTemplate(
        id="changedetection",
        name="Change Detection",
        description="Website change detection and monitoring with notifications",
        icon="🔔",
        category=AppCategory.MONITORING,
        source=AppSource.COMMUNITY,
        image="ghcr.io/dgtlmoon/changedetection.io:latest",
        version="latest",
        website="https://changedetection.io",
        github="https://github.com/dgtlmoon/changedetection.io",
        documentation="https://github.com/dgtlmoon/changedetection.io/wiki",
        min_memory="128m",
        recommended_memory="256m",
        ports=[PortMapping(host=5001, container=5000)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/changedetection/data", container="/datastore"),
        ],
    ),
    
    # ═══════════════════════════════════════════════════════════════════════════
    # HOME AUTOMATION & IOT
    # ═══════════════════════════════════════════════════════════════════════════

    AppTemplate(
        id="homeassistant",
        name="Home Assistant",
        description="Open source home automation platform - control all your devices from a single interface",
        icon="🏠",
        category=AppCategory.HOME_AUTOMATION,
        source=AppSource.OFFICIAL,
        image="ghcr.io/home-assistant/home-assistant:stable",
        version="stable",
        website="https://www.home-assistant.io",
        github="https://github.com/home-assistant/core",
        documentation="https://www.home-assistant.io/docs/",
        min_memory="1g",
        recommended_memory="2g",
        ports=[PortMapping(host=8123, container=8123)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/homeassistant/config", container="/config"),
        ],
        environment=[
            EnvVar(name="TZ", value="UTC", description="Timezone"),
        ],
        privileged=True,
        network_mode="host",
    ),

    AppTemplate(
        id="frigate",
        name="Frigate NVR",
        description="AI-powered NVR with real-time object detection for security cameras",
        icon="📹",
        category=AppCategory.HOME_AUTOMATION,
        source=AppSource.COMMUNITY,
        image="ghcr.io/blakeblackshear/frigate:stable",
        version="stable",
        website="https://frigate.video",
        github="https://github.com/blakeblackshear/frigate",
        documentation="https://docs.frigate.video/",
        min_memory="2g",
        recommended_memory="4g",
        ports=[
            PortMapping(host=5000, container=5000),
            PortMapping(host=8554, container=8554),
            PortMapping(host=8555, container=8555),
        ],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/frigate/config", container="/config"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/frigate/media", container="/media/frigate"),
            VolumeMapping(host="/etc/localtime", container="/etc/localtime", readonly=True),
        ],
        environment=[
            EnvVar(name="FRIGATE_RTSP_PASSWORD", value="", description="Password for RTSP streams", secret=True),
        ],
        privileged=True,
    ),

    AppTemplate(
        id="scrypted",
        name="Scrypted",
        description="High performance home video integration platform for HomeKit, Google Home, Alexa",
        icon="🎥",
        category=AppCategory.HOME_AUTOMATION,
        source=AppSource.COMMUNITY,
        image="ghcr.io/koush/scrypted:latest",
        version="latest",
        website="https://scrypted.app",
        github="https://github.com/koush/scrypted",
        documentation="https://docs.scrypted.app/",
        min_memory="1g",
        recommended_memory="2g",
        ports=[PortMapping(host=10443, container=10443)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/scrypted/volume", container="/server/volume"),
        ],
        network_mode="host",
    ),

    AppTemplate(
        id="node-red",
        name="Node-RED",
        description="Low-code programming for event-driven applications and IoT automation",
        icon="🔴",
        category=AppCategory.HOME_AUTOMATION,
        source=AppSource.OFFICIAL,
        image="nodered/node-red:latest",
        version="latest",
        website="https://nodered.org",
        github="https://github.com/node-red/node-red",
        documentation="https://nodered.org/docs/",
        min_memory="256m",
        recommended_memory="512m",
        ports=[PortMapping(host=1880, container=1880)],
        volumes=[
            # The image runs as node-red (uid/gid 1000).
            VolumeMapping(host=f"{APPS_DATA_PATH}/nodered/data", container="/data", uid=1000, gid=1000),
        ],
        environment=[
            EnvVar(name="TZ", value="UTC"),
        ],
    ),

    AppTemplate(
        id="mosquitto",
        name="Eclipse Mosquitto",
        description="Lightweight open source MQTT message broker for IoT devices",
        icon="🦟",
        category=AppCategory.HOME_AUTOMATION,
        source=AppSource.OFFICIAL,
        image="eclipse-mosquitto:latest",
        version="latest",
        website="https://mosquitto.org",
        github="https://github.com/eclipse/mosquitto",
        documentation="https://mosquitto.org/documentation/",
        min_memory="32m",
        recommended_memory="128m",
        ports=[
            PortMapping(host=1883, container=1883),
            PortMapping(host=9001, container=9001),
        ],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/mosquitto/config", container="/mosquitto/config"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/mosquitto/data", container="/mosquitto/data"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/mosquitto/log", container="/mosquitto/log"),
        ],
    ),

    AppTemplate(
        id="zigbee2mqtt",
        name="Zigbee2MQTT",
        description="Bridge Zigbee devices to MQTT - control your smart home without vendor lock-in",
        icon="📡",
        category=AppCategory.HOME_AUTOMATION,
        source=AppSource.COMMUNITY,
        image="koenkk/zigbee2mqtt:latest",
        version="latest",
        website="https://www.zigbee2mqtt.io",
        github="https://github.com/Koenkk/zigbee2mqtt",
        documentation="https://www.zigbee2mqtt.io/guide/getting-started/",
        min_memory="128m",
        recommended_memory="256m",
        ports=[PortMapping(host=8080, container=8080)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/zigbee2mqtt/data", container="/app/data"),
            VolumeMapping(host="/run/udev", container="/run/udev", readonly=True),
        ],
        environment=[
            EnvVar(name="TZ", value="UTC"),
        ],
        privileged=True,
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # NETWORK & DNS
    # ═══════════════════════════════════════════════════════════════════════════

    AppTemplate(
        id="adguard-home",
        name="AdGuard Home",
        description="Network-wide ads & trackers blocking DNS server with parental controls",
        icon="🛡️",
        category=AppCategory.NETWORK,
        source=AppSource.OFFICIAL,
        image="adguard/adguardhome:latest",
        version="latest",
        website="https://adguard.com/adguard-home.html",
        github="https://github.com/AdguardTeam/AdGuardHome",
        documentation="https://github.com/AdguardTeam/AdGuardHome/wiki",
        min_memory="128m",
        recommended_memory="256m",
        ports=[
            PortMapping(host=3000, container=3000),
            PortMapping(host=53, container=53),
            PortMapping(host=53, container=53, protocol="udp"),
        ],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/adguard/work", container="/opt/adguardhome/work"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/adguard/conf", container="/opt/adguardhome/conf"),
        ],
    ),

    AppTemplate(
        id="pihole",
        name="Pi-hole",
        description="Network-wide ad blocking via your own DNS server",
        icon="🕳️",
        category=AppCategory.NETWORK,
        source=AppSource.OFFICIAL,
        image="pihole/pihole:latest",
        version="latest",
        website="https://pi-hole.net",
        github="https://github.com/pi-hole/pi-hole",
        documentation="https://docs.pi-hole.net/",
        min_memory="128m",
        recommended_memory="256m",
        ports=[
            PortMapping(host=8053, container=80),
            PortMapping(host=53, container=53),
            PortMapping(host=53, container=53, protocol="udp"),
        ],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/pihole/etc", container="/etc/pihole"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/pihole/dnsmasq", container="/etc/dnsmasq.d"),
        ],
        environment=[
            EnvVar(name="TZ", value="UTC"),
            EnvVar(name="WEBPASSWORD", value="", description="Admin password for web interface", secret=True),
        ],
    ),

    AppTemplate(
        id="tailscale",
        name="Tailscale",
        description="Zero config VPN - create a secure network between your devices",
        icon="🔗",
        category=AppCategory.NETWORK,
        source=AppSource.OFFICIAL,
        image="tailscale/tailscale:latest",
        version="latest",
        website="https://tailscale.com",
        github="https://github.com/tailscale/tailscale",
        documentation="https://tailscale.com/kb/",
        min_memory="64m",
        recommended_memory="128m",
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/tailscale/state", container="/var/lib/tailscale"),
            VolumeMapping(host="/dev/net/tun", container="/dev/net/tun"),
        ],
        environment=[
            EnvVar(name="TS_AUTHKEY", value="", description="Tailscale auth key from the admin console (optional). Without a key, open the app's logs after it starts and follow the login URL shown there.", secret=True),
            EnvVar(name="TS_STATE_DIR", value="/var/lib/tailscale"),
        ],
        capabilities=["NET_ADMIN", "NET_RAW"],
        network_mode="host",
    ),

    AppTemplate(
        id="wireguard",
        name="WireGuard",
        description="Fast, modern, secure VPN tunnel with easy configuration",
        icon="🔒",
        category=AppCategory.NETWORK,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/wireguard:latest",
        version="latest",
        website="https://www.wireguard.com",
        github="https://github.com/WireGuard/wireguard-go",
        documentation="https://www.wireguard.com/quickstart/",
        min_memory="64m",
        recommended_memory="128m",
        ports=[PortMapping(host=51820, container=51820, protocol="udp")],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/wireguard/config", container="/config"),
            VolumeMapping(host="/lib/modules", container="/lib/modules", readonly=True),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
            EnvVar(name="SERVERURL", value="auto", description="Server URL or IP"),
            EnvVar(name="PEERS", value="1", description="Number of peers/clients to generate"),
        ],
        capabilities=["NET_ADMIN", "SYS_MODULE"],
    ),

    AppTemplate(
        id="nginx-proxy-manager",
        name="Nginx Proxy Manager",
        description="Easy reverse proxy with SSL certificate management via Let's Encrypt",
        icon="🔀",
        category=AppCategory.NETWORK,
        source=AppSource.COMMUNITY,
        image="jc21/nginx-proxy-manager:latest",
        version="latest",
        website="https://nginxproxymanager.com",
        github="https://github.com/NginxProxyManager/nginx-proxy-manager",
        documentation="https://nginxproxymanager.com/guide/",
        min_memory="128m",
        recommended_memory="256m",
        ports=[
            PortMapping(host=80, container=80),
            PortMapping(host=443, container=443),
            PortMapping(host=81, container=81),
        ],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/npm/data", container="/data"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/npm/letsencrypt", container="/etc/letsencrypt"),
        ],
    ),

    AppTemplate(
        id="traefik",
        name="Traefik",
        description="Modern HTTP reverse proxy and load balancer with automatic HTTPS",
        icon="🚦",
        category=AppCategory.NETWORK,
        source=AppSource.OFFICIAL,
        image="traefik:v3.0",
        version="v3.0",
        website="https://traefik.io",
        github="https://github.com/traefik/traefik",
        documentation="https://doc.traefik.io/traefik/",
        min_memory="64m",
        recommended_memory="256m",
        ports=[
            PortMapping(host=80, container=80),
            PortMapping(host=443, container=443),
            PortMapping(host=8080, container=8080),
        ],
        volumes=[
            VolumeMapping(host="/var/run/docker.sock", container="/var/run/docker.sock", readonly=True),
            VolumeMapping(host=f"{APPS_DATA_PATH}/traefik/config", container="/etc/traefik"),
        ],
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # AI & MACHINE LEARNING
    # ═══════════════════════════════════════════════════════════════════════════

    AppTemplate(
        id="ollama",
        name="Ollama",
        description="Run large language models locally - Llama, Mistral, and more",
        icon="🦙",
        category=AppCategory.AI,
        source=AppSource.OFFICIAL,
        image="ollama/ollama:latest",
        version="latest",
        website="https://ollama.ai",
        github="https://github.com/ollama/ollama",
        documentation="https://github.com/ollama/ollama/blob/main/README.md",
        min_memory="4g",
        recommended_memory="8g",
        ports=[PortMapping(host=11434, container=11434)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/ollama/models", container="/root/.ollama"),
        ],
    ),

    AppTemplate(
        id="open-webui",
        name="Open WebUI",
        description="User-friendly web interface for Ollama and OpenAI-compatible APIs",
        icon="💬",
        category=AppCategory.AI,
        source=AppSource.COMMUNITY,
        image="ghcr.io/open-webui/open-webui:main",
        version="main",
        website="https://openwebui.com",
        github="https://github.com/open-webui/open-webui",
        documentation="https://docs.openwebui.com/",
        min_memory="512m",
        recommended_memory="1g",
        ports=[PortMapping(host=3000, container=8080)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/open-webui/data", container="/app/backend/data"),
        ],
        environment=[
            EnvVar(name="OLLAMA_BASE_URL", value="http://host.docker.internal:11434", description="Ollama API URL"),
        ],
    ),

    AppTemplate(
        id="localai",
        name="LocalAI",
        description="Self-hosted OpenAI-compatible API for running LLMs, image generation, and more",
        icon="🤖",
        category=AppCategory.AI,
        source=AppSource.COMMUNITY,
        image="localai/localai:latest-aio-cpu",
        version="latest",
        website="https://localai.io",
        github="https://github.com/mudler/LocalAI",
        documentation="https://localai.io/docs/",
        min_memory="4g",
        recommended_memory="8g",
        ports=[PortMapping(host=8080, container=8080)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/localai/models", container="/models"),
        ],
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # BACKUP & SYNC
    # ═══════════════════════════════════════════════════════════════════════════

    AppTemplate(
        id="duplicati",
        name="Duplicati",
        description="Free backup software with encryption and cloud storage support",
        icon="💾",
        category=AppCategory.BACKUP,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/duplicati:latest",
        version="latest",
        website="https://duplicati.com",
        github="https://github.com/duplicati/duplicati",
        documentation="https://duplicati.readthedocs.io/",
        min_memory="256m",
        recommended_memory="512m",
        ports=[PortMapping(host=8200, container=8200)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/duplicati/config", container="/config"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/duplicati/backups", container="/backups"),
            VolumeMapping(host=POOL, container="/source", readonly=True),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
        ],
    ),

    AppTemplate(
        id="restic-rest-server",
        name="Restic REST Server",
        description="High-performance HTTP server for Restic backup repositories",
        icon="🗄️",
        category=AppCategory.BACKUP,
        source=AppSource.OFFICIAL,
        image="restic/rest-server:latest",
        version="latest",
        website="https://restic.net",
        github="https://github.com/restic/rest-server",
        documentation="https://restic.readthedocs.io/",
        min_memory="64m",
        recommended_memory="256m",
        ports=[PortMapping(host=8000, container=8000)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/restic/data", container="/data"),
        ],
        environment=[
            EnvVar(name="OPTIONS", value="--prometheus --no-auth", description="Server options"),
        ],
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # PRODUCTIVITY - Additional Apps
    # ═══════════════════════════════════════════════════════════════════════════

    AppTemplate(
        id="mealie",
        name="Mealie",
        description="Self-hosted recipe manager and meal planner with shopping lists",
        icon="🍽️",
        category=AppCategory.PRODUCTIVITY,
        source=AppSource.COMMUNITY,
        image="ghcr.io/mealie-recipes/mealie:latest",
        version="latest",
        website="https://mealie.io",
        github="https://github.com/mealie-recipes/mealie",
        documentation="https://docs.mealie.io/",
        min_memory="256m",
        recommended_memory="512m",
        ports=[PortMapping(host=9925, container=9000)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/mealie/data", container="/app/data"),
        ],
        environment=[
            EnvVar(name="TZ", value="UTC"),
            EnvVar(name="ALLOW_SIGNUP", value="true"),
        ],
    ),

    AppTemplate(
        id="actual-budget",
        name="Actual Budget",
        description="Privacy-focused personal finance app with envelope budgeting",
        icon="💰",
        category=AppCategory.PRODUCTIVITY,
        source=AppSource.COMMUNITY,
        image="actualbudget/actual-server:latest",
        version="latest",
        website="https://actualbudget.org",
        github="https://github.com/actualbudget/actual",
        documentation="https://actualbudget.org/docs/",
        min_memory="128m",
        recommended_memory="256m",
        ports=[PortMapping(host=5006, container=5006)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/actual/data", container="/data"),
        ],
    ),

    AppTemplate(
        id="n8n",
        name="n8n",
        description="Workflow automation platform - connect apps and automate tasks",
        icon="⚡",
        category=AppCategory.PRODUCTIVITY,
        source=AppSource.OFFICIAL,
        image="n8nio/n8n:latest",
        version="latest",
        website="https://n8n.io",
        github="https://github.com/n8n-io/n8n",
        documentation="https://docs.n8n.io/",
        min_memory="256m",
        recommended_memory="1g",
        ports=[PortMapping(host=5678, container=5678)],
        volumes=[
            # The image runs as node (uid/gid 1000).
            VolumeMapping(host=f"{APPS_DATA_PATH}/n8n/data", container="/home/node/.n8n", uid=1000, gid=1000),
        ],
        environment=[
            EnvVar(name="TZ", value="UTC"),
            EnvVar(name="GENERIC_TIMEZONE", value="UTC"),
        ],
    ),

    AppTemplate(
        id="vikunja",
        name="Vikunja",
        description="Open source to-do app with lists, kanban boards, and team collaboration",
        icon="✅",
        category=AppCategory.PRODUCTIVITY,
        source=AppSource.COMMUNITY,
        image="vikunja/vikunja:latest",
        version="latest",
        website="https://vikunja.io",
        github="https://github.com/go-vikunja/vikunja",
        documentation="https://vikunja.io/docs/",
        min_memory="64m",
        recommended_memory="256m",
        ports=[PortMapping(host=3456, container=3456)],
        volumes=[
            # The image runs as uid 1000 (USER 1000 in a scratch image).
            VolumeMapping(host=f"{APPS_DATA_PATH}/vikunja/files", container="/app/vikunja/files", uid=1000, gid=1000),
            VolumeMapping(host=f"{APPS_DATA_PATH}/vikunja/db", container="/db", uid=1000, gid=1000),
        ],
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # DEVELOPMENT
    # ═══════════════════════════════════════════════════════════════════════════

    AppTemplate(
        id="gitea",
        name="Gitea",
        description="Lightweight self-hosted Git service - fast, easy, and painless",
        icon="🍵",
        category=AppCategory.DEVELOPMENT,
        source=AppSource.OFFICIAL,
        image="gitea/gitea:latest",
        version="latest",
        website="https://gitea.io",
        github="https://github.com/go-gitea/gitea",
        documentation="https://docs.gitea.io/",
        min_memory="256m",
        recommended_memory="512m",
        ports=[
            PortMapping(host=3000, container=3000),
            PortMapping(host=2222, container=22),
        ],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/gitea/data", container="/data"),
            # /etc/timezone is not mounted: newer Debian releases no longer ship
            # it, and bind sources must exist. Gitea reads /etc/localtime.
            VolumeMapping(host="/etc/localtime", container="/etc/localtime", readonly=True),
        ],
    ),

    AppTemplate(
        id="code-server",
        name="Code Server",
        description="VS Code in the browser - full IDE accessible from anywhere",
        icon="💻",
        category=AppCategory.DEVELOPMENT,
        source=AppSource.LINUXSERVER,
        image="lscr.io/linuxserver/code-server:latest",
        version="latest",
        website="https://coder.com",
        github="https://github.com/coder/code-server",
        documentation="https://coder.com/docs/code-server",
        min_memory="512m",
        recommended_memory="2g",
        ports=[PortMapping(host=8443, container=8443)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/code-server/config", container="/config"),
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
            EnvVar(name="PASSWORD", value="", description="Password for web access (without it anyone on the network gets a shell)", required=True, secret=True),
        ],
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # SECURITY & PRIVACY
    # ═══════════════════════════════════════════════════════════════════════════

    AppTemplate(
        id="vaultwarden",
        name="Vaultwarden",
        description="Lightweight Bitwarden-compatible password manager server",
        icon="🔐",
        category=AppCategory.SECURITY,
        source=AppSource.COMMUNITY,
        image="vaultwarden/server:latest",
        version="latest",
        website="https://github.com/dani-garcia/vaultwarden",
        github="https://github.com/dani-garcia/vaultwarden",
        documentation="https://github.com/dani-garcia/vaultwarden/wiki",
        min_memory="64m",
        recommended_memory="256m",
        ports=[PortMapping(host=8089, container=80)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/vaultwarden/data", container="/data"),
        ],
        environment=[
            EnvVar(name="ADMIN_TOKEN", value="", description="Admin panel token (generate secure random string)", secret=True),
            EnvVar(name="SIGNUPS_ALLOWED", value="true", description="Allow new user signups"),
        ],
    ),
    
    AppTemplate(
        id="authelia",
        name="Authelia",
        description="Single Sign-On and Two-Factor authentication portal",
        icon="🛡️",
        category=AppCategory.SECURITY,
        source=AppSource.COMMUNITY,
        image="authelia/authelia:latest",
        version="latest",
        website="https://authelia.com",
        github="https://github.com/authelia/authelia",
        documentation="https://www.authelia.com/docs/",
        min_memory="128m",
        recommended_memory="256m",
        ports=[PortMapping(host=9091, container=9091)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/authelia/config", container="/config"),
        ],
    ),
    
    AppTemplate(
        id="crowdsec",
        name="CrowdSec",
        description="Collaborative security engine for intrusion detection and prevention",
        icon="🦅",
        category=AppCategory.SECURITY,
        source=AppSource.COMMUNITY,
        image="crowdsecurity/crowdsec:latest",
        version="latest",
        website="https://crowdsec.net",
        github="https://github.com/crowdsecurity/crowdsec",
        documentation="https://docs.crowdsec.net/",
        min_memory="128m",
        recommended_memory="512m",
        ports=[PortMapping(host=8092, container=8080)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/crowdsec/config", container="/etc/crowdsec"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/crowdsec/data", container="/var/lib/crowdsec/data"),
            VolumeMapping(host="/var/log", container="/var/log", readonly=True),
        ],
    ),
]


# The templates whose images still float on a tag, pulled as the tag names them at the install. The
# set may only shrink (a test holds it to the ids it had when the first app was pinned), and a
# template leaves it once its images are pinned; apps.pinned-catalog-images empties it (ADR-0381).
UNPINNED_TEMPLATES = frozenset({
    "actual-budget", "adguard-home", "aria2", "audiobookshelf", "authelia", "bazarr", "calibre-web",
    "changedetection", "code-server", "crowdsec", "deluge", "dozzle", "duplicati", "emby", "filebrowser",
    "frigate", "gitea", "glances", "grafana", "heimdall", "homarr", "homeassistant", "homepage",
    "jdownloader2", "jellyfin", "kavita", "lidarr", "localai", "mealie", "mosquitto", "n8n", "navidrome",
    "netdata", "nextcloud", "nginx-proxy-manager", "node-red", "nzbget", "ollama", "open-webui", "overseerr",
    "photoprism", "pihole", "plex", "portainer", "prometheus", "prowlarr", "qbittorrent", "radarr", "readarr",
    "restic-rest-server", "sabnzbd", "scrypted", "sonarr", "stirling-pdf", "syncthing", "tailscale",
    "tautulli", "traefik", "transmission", "trilium", "uptime-kuma", "vaultwarden", "vikunja", "wikijs",
    "wireguard", "zigbee2mqtt",
})


def get_app_by_id(app_id: str) -> AppTemplate | None:
    """Get an app template by its ID."""
    for app in APP_CATALOG:
        if app.id == app_id:
            return app
    return None


def get_apps_by_category(category: AppCategory) -> list[AppTemplate]:
    """Get all apps in a specific category."""
    return [app for app in APP_CATALOG if app.category == category]


def search_apps(query: str) -> list[AppTemplate]:
    """Search apps by name or description."""
    query_lower = query.lower()
    return [
        app for app in APP_CATALOG
        if query_lower in app.name.lower() or query_lower in app.description.lower()
    ]
