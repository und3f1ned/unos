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
- A configuration file that the image ships in a folder and needs at its start
  is bound as that file, read-only, with a content that the install (and a
  start) writes when the file is missing: a bind of an empty folder there hides
  it, and the app restarts in a loop (Mosquitto's mosquitto.conf). The content
  is the image's own, unless that opens the app to the network without
  authentication (ADR-0480).
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
template's own fields describe (ADR-0381, ADR-0415: Immich, BookStack,
Paperless-ngx and Linkwarden): AppTemplate.companions, each a
Companion named ^[a-z][a-z0-9-]*$. The main container is named <id>, a
companion <id>-<name>, and all of them join the network unos-app-<id>, on
which each companion answers to its name: the main container reaches it by
that name (DB_HOSTNAME=database). The companions are created and started in
their order before the main container; each one named in depends_on is ready
(healthy, or running if its image has no health check) before the next
container starts; a database whose image has no health check names the
command that tells it is ready (ContainerSpec.ready_command), which UNOS runs in
it while it waits, and which ends within a timeout of its own (docker-py reads
an exec's output without one). A companion publishes no port, is never privileged, gets no
capability and takes no override of the install; its pool folders are under
{pool}/apps/<id>/.

An app that reaches another app's port on the NAS (Open WebUI's Ollama,
Zigbee2MQTT's Mosquitto) sets host_gateway: host.docker.internal names the
host in its container (ADR-0415).

A folder of an app's own data that a newer version migrates so that the earlier version cannot
read it (a database) is marked copy_before_update: an update copies it beside itself on the pool
before the new version starts, and a rollback puts the copy back (ADR-0408).

A password that containers of an app share is a file that UNOS generates on
the pool, never a variable: a VolumeMapping with generated_secret=True
(is_file, readonly, under {pool}/apps/<id>/, the same mapping in each
container that reads it), named there by a *_FILE variable (LinuxServer's
images: a FILE__ one; an image that reads neither: the container's command),
with secret_for naming the data folder that was set up with it; a key that the
app takes as its bytes gets the secret_digits it needs. Wiring that UNOS sets, such
as a companion's name or a password file's path, is EnvVar(editable=False).
Such an app asks the administrator for no secret.

Every image of the catalog is pinned: a PinnedImage (name:tag@sha256 of the
multi-platform index, as ADR-0305 pins the Dockerfiles) with its estimated
size where Docker keeps its images (ADR-0490) and the VOLUMEs it declares, listed in PINNED_IMAGES
(ADR-0381, ADR-0410); an app of one container takes its image from
APP_IMAGES and its version from the pin's tag. A test fails for an image
that is not pinned; no install, space check or update has a path for one.
LinuxServer's images are named ghcr.io/linuxserver/<name>, where its registry
serves them, never lscr.io/linuxserver/<name>, its redirector to the same
indexes, whose blobs Docker failed to pull on the dev device's network
(tests/test_linuxserver_ghcr.py; ADR-0426).

Nothing an app writes may land in Docker's data root, on the system disk
until the first pool takes it (ADR-0381, ADR-0486): every VOLUME that its image declares lies at or under a bind on
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
    # About what it takes where Docker keeps its images once pulled, from the registry: its compressed
    # layers plus their unpacked size (the containerd image store keeps both). The install checks the
    # free space there with it before a pull (ADR-0490).
    disk_bytes: int
    # The VOLUMEs that its configuration declares, the same on each of its platforms: each lies at or
    # under a bind on the pool or a tmpfs of the container that runs it (a test; ADR-0381).
    volumes: tuple[str, ...] = ()
    # The CMD of its configuration, recorded where a template's command runs it after its own steps
    # (Linkwarden's, ADR-0415): a re-pin reads it again, as it reads the VOLUMEs.
    cmd: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.name}:{self.tag}@{self.digest}"


# Immich's server and machine learning are one release: they move together (a test ties them).
# The digests are the indexes that Immich's release workflow pushed for v3.4.1, its latest release
# on 2026-11-16; Postgres and Valkey are pinned as Immich's own docker-compose.yml of v3.4.1 pins them,
# Valkey by the version tag that names the same index (9.1.2), since its tag 9 has moved since (ADR-0410).
IMMICH_VERSION = "v3.4.1"
IMMICH_SERVER = PinnedImage(
    "ghcr.io/immich-app/immich-server", IMMICH_VERSION,
    "sha256:7c489add298fc2f92cda4c777de6462aa206877e04087b815f62c7664af0b53b", 1_482_000_000, ("/data",),
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
    ("/var/lib/postgresql/data",),
)
VALKEY = PinnedImage(
    "docker.io/valkey/valkey", "9.1.2",
    "sha256:418652cfb58ef879d4978c33553735d7147016032d5aefaa14c828e611eb9dfd", 184_000_000,
)
# The images of the apps that came back with companions (ADR-0415): each app's own image by the
# publisher's version tag of the index that its floating tag named on 2026-11-18, and its database
# or broker as the app's own docker-compose.yml names it (BookStack's, which names none: MariaDB's
# 11.8 LTS line), pinned by its version tag, with the estimate where Docker keeps its images of
# APP_IMAGES (four times the compressed layers of the larger linux/arm64 or linux/amd64 image) and
# the VOLUMEs of its configuration. Each moves with its app, never with Immich's. Paperless-ngx's and
# Linkwarden's are the sizes that their pulls took on the dev device on 2026-11-19 with their
# companions (3308 and 2858 MiB), and BookStack's and its MariaDB's on 2026-11-19 (482.6 and 457.4
# MiB), plus about 5%: four times their compressed layers fell short.
BOOKSTACK = PinnedImage(
    "ghcr.io/linuxserver/bookstack", "v26.09.3-ls299",
    "sha256:c741211356966fd38f8fea10483f341ca29c41cdd1b96b5cfcdd4312f26d7f97", 531_000_000, ("/config",),
)
BOOKSTACK_MARIADB = PinnedImage(
    "mariadb", "11.8.9",
    "sha256:e1d5495f445654134f226f55a41ce0da227116f6f27f283101f405c794d841fc", 504_000_000, ("/var/lib/mysql",),
)
PAPERLESS = PinnedImage(
    "ghcr.io/paperless-ngx/paperless-ngx", "3.5.1",
    "sha256:8508853b8a0f8394074260957d0be186c8c64f346973b24a7583de4580f306b9", 3_560_000_000,
    ("/usr/src/paperless/consume", "/usr/src/paperless/data", "/usr/src/paperless/export", "/usr/src/paperless/media"),
)
# Paperless-ngx's broker: Valkey's Alpine build, as its docker-compose files name it (9-alpine).
PAPERLESS_VALKEY = PinnedImage(
    "docker.io/valkey/valkey", "9.1.3-alpine",
    "sha256:bf09d58e96d6b4524a15396b65b242bbe18beebbcc15a04d8ee75ddac62fae0b", 77_000_000,
)
LINKWARDEN = PinnedImage(
    "ghcr.io/linkwarden/linkwarden", "v2.17.0",
    "sha256:7c11fde57d1a0c819b6de0079966eba3a33584d718464af7dacde22bd470ae46", 2_690_000_000,
    cmd=(
        "sh", "-c",
        "export PATH=/data/node_modules/.bin:$PATH && prisma migrate deploy --schema=/data/packages/prisma/schema.prisma"
        ' && exec concurrently -k -n web,worker "cd /data/apps/web && exec next start" "cd /data/apps/worker && exec'
        ' tsx worker.ts"',
    ),
)
LINKWARDEN_POSTGRES = PinnedImage(
    "postgres", "16.16-alpine",
    "sha256:f2f98726820d0342055351c6e9d36e705f5bfffb3dd53ff556325114b08afb6d", 465_000_000,
    ("/var/lib/postgresql/data",),
)
# The image of each app of one container, by template id (ADR-0410): the publisher's most specific tag
# for the build (a version, LinuxServer's <version>-ls<build>; never latest, a channel or a branch)
# and the digest of the index that it named on 2026-11-18, with its estimate where Docker keeps images
# (four times the compressed layers of its larger image of linux/arm64 and linux/amd64) and the
# VOLUMEs that its configuration declares. LinuxServer's pins name ghcr.io, which served the same
# index for each tag on 2026-11-19 as lscr.io did on 2026-11-18 (ADR-0426). Traefik's tag is its
# line v3.7: the index of v3.7.19 lacks the linux/riscv64 image that v3.7 names beside the same
# images. Trilium's image is triliumnext/trilium, whose version tag names the index that
# zadam/trilium:latest mirrors.
# scripts/catalog-platforms.py tags finds a tag that moved.
APP_IMAGES: dict[str, PinnedImage] = {
    "plex": PinnedImage(
        "plexinc/pms-docker", "1.43.5.11079-b3b13e506",
        "sha256:6d778d41daae68f89115aa87a44ae14a96b23e71a2093da173b5a9aac4091336", 651_000_000, ("/config", "/transcode"),
    ),
    "jellyfin": PinnedImage(
        "jellyfin/jellyfin", "12.4.20261026-211555",
        "sha256:fea863d0cd9ec445985d9c75fc6ed64e38a1d2766cf0c250a33d553f4fc1b771", 3_005_000_000, ("/cache", "/config"),
    ),
    "emby": PinnedImage(
        "ghcr.io/linuxserver/emby", "4.10.2.0-ls307",
        "sha256:6149cec5ced1b071e9ed6dcce1e2690b679146a982ad311f7f9604be83eb859a", 1_752_000_000, ("/config",),
    ),
    "navidrome": PinnedImage(
        "deluan/navidrome", "0.64.2",
        "sha256:38dc2727bfcfd5ede290f8ada114fc90368146f265ae4701ddddbcbe2a44ee52", 377_000_000, ("/data", "/music"),
    ),
    "photoprism": PinnedImage(
        "photoprism/photoprism", "261029",
        "sha256:9409cf8e5a6ebcbe3170d14537556cbc20997aaa307096d6eeedbfc5eeef6719", 4_252_000_000, (),
    ),
    "audiobookshelf": PinnedImage(
        "ghcr.io/advplyr/audiobookshelf", "2.38.0",
        "sha256:60064c23f32fd9099fd39e82c5132c51701d1221135bbca04cd099b589e1edc3", 525_000_000, (),
    ),
    "calibre-web": PinnedImage(
        "ghcr.io/linuxserver/calibre-web", "0.6.27-ls410",
        "sha256:adb69d75b9c252e580fc691096f858833b901b5d106ae4080fb476bab104d946", 1_100_000_000, ("/config",),
    ),
    "kavita": PinnedImage(
        "jvmilazz0/kavita", "0.9.1",
        "sha256:31181a32f0dda73cae68721867028a7253d57881b58bea5754cd9e578e75421a", 1_014_000_000, (),
    ),
    "sonarr": PinnedImage(
        "ghcr.io/linuxserver/sonarr", "4.0.20.3014-ls5",
        "sha256:c7d1df25e428da6c9ace05daea0d34e59522978a3843ea1944812f855af4086c", 352_000_000, ("/config",),
    ),
    "radarr": PinnedImage(
        "ghcr.io/linuxserver/radarr", "7.0.3.10918-ls328",
        "sha256:044788f5508b7f67a3e0368cdd142c77c4d4cd5aec2efaba53e1a68b6b6231e7", 389_000_000, ("/config",),
    ),
    "lidarr": PinnedImage(
        "ghcr.io/linuxserver/lidarr", "3.1.6.5079-ls49",
        "sha256:8fb40ada166dd1880cb1b166df5bcbb081107523f20ab60df05c313e2052a467", 486_000_000, ("/config",),
    ),
    "prowlarr": PinnedImage(
        "ghcr.io/linuxserver/prowlarr", "3.0.2.5780-ls169",
        "sha256:45117ba8439e2337a92ffe38a451a4dafc0be771754d05d88cdf102419d09646", 341_000_000, ("/config",),
    ),
    "bazarr": PinnedImage(
        "ghcr.io/linuxserver/bazarr", "v1.6.2-ls373",
        "sha256:e0c5d45965242830c5eeddaafe9ae2b5e8ca3f6c01ecda9ee9364f550921dc50", 626_000_000, ("/config",),
    ),
    "overseerr": PinnedImage(
        "ghcr.io/linuxserver/overseerr", "v1.35.0-ls158",
        "sha256:53a1b839ffa81f139e1552840772bff8a5d13f5c78a69cce22458b9a6cd0844d", 724_000_000, ("/config",),
    ),
    "tautulli": PinnedImage(
        "ghcr.io/linuxserver/tautulli", "v2.19.1-ls252",
        "sha256:5e8f80cc18f03c606a3567c8a9b37d80812ed8017d2e2f8ff2e2a91b54a3aef1", 240_000_000, ("/config",),
    ),
    "transmission": PinnedImage(
        "ghcr.io/linuxserver/transmission", "4.1.4-r0-ls370",
        "sha256:5baf08cd73a566699f35baad3c4a84ba7d7057220566c5fb29969bf2292dc2ff", 141_000_000, ("/config",),
    ),
    "qbittorrent": PinnedImage(
        "ghcr.io/linuxserver/qbittorrent", "5.3.0_v2.1.2-ls487",
        "sha256:3d4c4545d51a690a0df15a2ff99038823a88a9822df8e794526447b9afbd7384", 411_000_000, ("/config",),
    ),
    "deluge": PinnedImage(
        "ghcr.io/linuxserver/deluge", "2.2.0-ls387",
        "sha256:e2a537991884a514b64d7adf857e39b5e9239a1c76e52114e52bf10707dde772", 320_000_000, ("/config",),
    ),
    "sabnzbd": PinnedImage(
        "ghcr.io/linuxserver/sabnzbd", "5.1.3-ls281",
        "sha256:b356d0a9b22db584e9005364ec4e752dfc081ce501b7145e9a5c0bb38e4d1ba4", 279_000_000, ("/config",),
    ),
    "nzbget": PinnedImage(
        "ghcr.io/linuxserver/nzbget", "v26.3-ls272",
        "sha256:17e13511d47b1439af19b7ef17d08c8643eb2a5f009b1d3f800b704ebe590218", 218_000_000, ("/config",),
    ),
    "jdownloader2": PinnedImage(
        "jlesage/jdownloader-2", "v26.09.1",
        "sha256:bd6cd2fdc72818f30207e677b33cd7f1b425048c4e7132624e541e99f992be4c", 815_000_000, ("/config", "/output"),
    ),
    "aria2": PinnedImage(
        "p3terx/aria2-pro", "202209060423",
        "sha256:086d1a37c586edb07ec0fb956bf9edd89d1d38d138ad5309ff96d510c8c9a011", 51_000_000, ("/config", "/downloads"),
    ),
    "nextcloud": PinnedImage(
        "ghcr.io/linuxserver/nextcloud", "35.0.3-ls2",
        "sha256:cc5a5cf7385e1a958fd4645aad76d0557ed0f58c8267825329ac7f7980fe0265", 1_632_000_000, ("/config",),
    ),
    "syncthing": PinnedImage(
        "ghcr.io/linuxserver/syncthing", "v2.1.7-ls237",
        "sha256:c4207d8f5b66d5f67eedb636b75500b5a04898e9f33299d252f4b966c41a4128", 113_000_000, ("/config",),
    ),
    "filebrowser": PinnedImage(
        "filebrowser/filebrowser", "v2.63.23",
        "sha256:a469ea076d4a1b4b1d86a41d130f2f536cd9da996a2b1fb39c0d7635f9d89b9a", 68_000_000, ("/config", "/database", "/srv"),
    ),
    "trilium": PinnedImage(
        "triliumnext/trilium", "v0.107.0",
        "sha256:739ba0f0488f506549f7c21dc9e792decd99382cc759f01d5ec1abba32e87da8", 627_000_000, (),
    ),
    "wikijs": PinnedImage(
        "ghcr.io/linuxserver/wikijs", "v2.5.317-ls232",
        "sha256:cc4d796a746304d2189de7f815f86462e9762164347972f47fdd67a6b743413e", 574_000_000, ("/config",),
    ),
    "stirling-pdf": PinnedImage(
        "frooodle/s-pdf", "3.3.0",
        "sha256:ed7b1775c6706b5e5ea24fa6155f078eff1027bd35a17d21834e4407036025e6", 4_522_000_000, (),
    ),
    "portainer": PinnedImage(
        "portainer/portainer-ce", "2.45.3",
        "sha256:3609c307943bca8e5c492f69ddd6965df177208b50678a4cff28391c8dd4e96e", 180_000_000, ("/data",),
    ),
    "uptime-kuma": PinnedImage(
        "louislam/uptime-kuma", "2.5.12",
        "sha256:8e4b6bef96417ec98a35dc103c99cc64fc229a01e584e56d95d9004d2003c02a", 2_480_000_000, (),
    ),
    "homepage": PinnedImage(
        "ghcr.io/gethomepage/homepage", "v2.8.0",
        "sha256:019d8f063a7299e7c1c51ee4a70b692291809af3244894fbec2c2ac7d1f71b18", 337_000_000, (),
    ),
    "homarr": PinnedImage(
        "ghcr.io/ajnart/homarr", "0.16.1",
        "sha256:e103abadfb527186af5b4bd43df4eff7c67afa5cd4df8e65fa97910c8840b2a9", 1_570_000_000, ("/app/data/configs", "/data"),
    ),
    "heimdall": PinnedImage(
        "ghcr.io/linuxserver/heimdall", "v2.8.4-ls373",
        "sha256:47f70751f900be4499017a48c01206c02ab9d18f34a580c6213a13d24cdc1a4a", 217_000_000, ("/config",),
    ),
    "grafana": PinnedImage(
        "grafana/grafana", "13.3.1",
        "sha256:0615ec4fbd4072004b7d86b46ded63013a74b7d6eb1119273dd89e8fa25c13cf", 2_025_000_000, (),
    ),
    "prometheus": PinnedImage(
        "prom/prometheus", "v3.16.0",
        "sha256:e04987641b57704f408e31f6373ba4138ecf27cb4a165d24aabb6c38fddd0f06", 465_000_000, ("/prometheus",),
    ),
    "dozzle": PinnedImage(
        "amir20/dozzle", "v11.4.3",
        "sha256:244f218c876d6ed766277e71a537f34bfb2c01829e1927737e3673a8327deeb8", 111_000_000, (),
    ),
    "netdata": PinnedImage(
        "netdata/netdata", "v2.12.3",
        "sha256:c7eab00e20901eb60595ebae0fbfe88e59464e663924461874719d40de0735d2", 1_309_000_000, (),
    ),
    "glances": PinnedImage(
        "nicolargo/glances", "4.5.9-full",
        "sha256:0b309652e8a4f2e827b823609044b9976b309765517760c0383d2e4f73f756c5", 637_000_000, (),
    ),
    "changedetection": PinnedImage(
        "ghcr.io/dgtlmoon/changedetection.io", "0.70.3",
        "sha256:47b1e9cc3eff820e6022b655e3fcc948625f60c1770ae8b9c30aa3bf104dc255", 1_635_000_000, (),
    ),
    "homeassistant": PinnedImage(
        "ghcr.io/home-assistant/home-assistant", "2026.11.2",
        "sha256:493a07e8f6530fc640223f4a0a5248f20a61ca2f718bb1d31efbf08a2c5ea28f", 2_696_000_000, (),
    ),
    "frigate": PinnedImage(
        "ghcr.io/blakeblackshear/frigate", "0.18.2",
        "sha256:fd60f99ffd4eb6c42360ed4e14953e270586f239309ab3983e55b2e97ffac09a", 8_630_000_000, (),
    ),
    "scrypted": PinnedImage(
        "ghcr.io/koush/scrypted", "v0.147.0-noble-full",
        "sha256:e78b4fcecdd6c61acaaf22b50ccef303c738da58cc0be57afdb116521df52296", 4_298_000_000, (),
    ),
    "node-red": PinnedImage(
        "nodered/node-red", "5.0.10",
        "sha256:3622786c15b6f57f61bf1d0ece59d06a18b742c5c8955f588d189b6bee8dc97d", 938_000_000, (),
    ),
    "mosquitto": PinnedImage(
        "eclipse-mosquitto", "2.1.4-alpine",
        "sha256:fa12172a051310efe5bcb7a6a11b77bd5faacb82cbacd7742aa758bf024fd8e4", 54_000_000, ("/mosquitto/data", "/mosquitto/log"),
    ),
    "zigbee2mqtt": PinnedImage(
        "koenkk/zigbee2mqtt", "2.14.3",
        "sha256:fb4a6b0f6868ad1cd6a9996479802b2420e599c3463b62bbff8c8cd9a908f43a", 188_000_000, (),
    ),
    "adguard-home": PinnedImage(
        "adguard/adguardhome", "v0.107.80",
        "sha256:e2e1b48c832f7705be915873f95de0d5114de573736441d7f2923a8a6293027c", 120_000_000, (),
    ),
    "pihole": PinnedImage(
        "pihole/pihole", "2026.11.0",
        "sha256:66c798d79a95c2691f280329c3611dba1746117c61c9f331907ca35a06a52778", 174_000_000, (),
    ),
    "tailscale": PinnedImage(
        "tailscale/tailscale", "v1.104.7",
        "sha256:72c1e19e0aedca96b8533bbb0b4368564410ff20b17e53b804eb6c1f04a31be4", 256_000_000, (),
    ),
    "wireguard": PinnedImage(
        "ghcr.io/linuxserver/wireguard", "1.0.20260223-r0-ls130",
        "sha256:10db56ad834f94541307d5dfdbe87370e26d155bb341a5bf65789f970f24ab5c", 163_000_000, (),
    ),
    "nginx-proxy-manager": PinnedImage(
        "jc21/nginx-proxy-manager", "2.17.0",
        "sha256:91277490a919c792aa73b8d291af1ca84d32be74df988e543137976515763534", 1_936_000_000, ("/data",),
    ),
    "traefik": PinnedImage(
        "traefik", "v3.7",
        "sha256:a6c0194dda77bcb804824580f36fa7edc56e315b1acfe933366c8b52844a78a4", 222_000_000, (),
    ),
    "ollama": PinnedImage(
        "ollama/ollama", "0.40.16",
        "sha256:910d0d8107b538fb0894e63163eb195de0cc7aefcbc666dc62d3b18f71124592", 13_591_000_000, (),
    ),
    "open-webui": PinnedImage(
        "ghcr.io/open-webui/open-webui", "v0.13.4",
        "sha256:d5168bfbc28609eff5bea1b438dd864cf64d185cf6582c02244865e18385b901", 6_581_000_000, (),
    ),
    "localai": PinnedImage(
        "localai/localai", "v3.12.1-aio-cpu",
        "sha256:4cbc20c59558ed6c1cae9bc3f6ae34d75d390b370aa8ac62a59327089fa56cec", 1_227_000_000, ("/backends", "/configuration", "/models"),
    ),
    "duplicati": PinnedImage(
        "ghcr.io/linuxserver/duplicati", "v2.4.1.0_stable_2026-10-29-ls318",
        "sha256:ea137d806f2c0556468f2c620bcac963f0825bf45876de274988b42ff13b453a", 747_000_000, ("/backups", "/config", "/source"),
    ),
    "restic-rest-server": PinnedImage(
        "restic/rest-server", "0.14.0",
        "sha256:d2aff06f47eb38637dff580c3e6bce4af98f386c396a25d32eb6727ec96214a5", 32_000_000, ("/data",),
    ),
    "mealie": PinnedImage(
        "ghcr.io/mealie-recipes/mealie", "v3.31.0",
        "sha256:97e21ac96e9a8d1ee15aaf558ba3f14998936b9d6f334e40ebb4c6c2127dc12f", 1_528_000_000, ("/app/data/",),
    ),
    "actual-budget": PinnedImage(
        "actualbudget/actual-server", "26.11.0",
        "sha256:06cf186569cf58d93e12659ff1a517b65b5b0ebee7050a3991d4bd8a1d398da6", 486_000_000, (),
    ),
    "n8n": PinnedImage(
        "n8nio/n8n", "2.48.5",
        "sha256:c0ea1246a81a34ce8e2b3cfb1f718b97de64b0a1b3747ad24d6d431826bb71ad", 1_275_000_000, (),
    ),
    "vikunja": PinnedImage(
        "vikunja/vikunja", "2.8.0",
        "sha256:35d937baf3b5d5803bed6b9e11d007448b7b536df9e4346dc35551e1072636c3", 219_000_000, (),
    ),
    "gitea": PinnedImage(
        "gitea/gitea", "28.4.0",
        "sha256:aa90e8f00b755b0d665214ab9c14447ea44d3190dbe6b6d0cb46cd9c4f55d309", 289_000_000, ("/data",),
    ),
    "code-server": PinnedImage(
        "ghcr.io/linuxserver/code-server", "4.145.2-ls377",
        "sha256:20ffca4512fcfc85dcd5612fb769233c1553192392d2ec417f0859135805f40c", 1_255_000_000, (),
    ),
    "vaultwarden": PinnedImage(
        "vaultwarden/server", "1.37.5",
        "sha256:4fe17f415f90087377d37c755e0ea3cf189dcc6794e49cdd2a9adbb4937d9e1f", 372_000_000, ("/data",),
    ),
    "authelia": PinnedImage(
        "authelia/authelia", "4.39.28",
        "sha256:bd97cff4fcbf715b5ff1f9ae286afbe6033afce385302520b0368122d43a6f54", 114_000_000, (),
    ),
    "crowdsec": PinnedImage(
        "crowdsecurity/crowdsec", "v1.8.2",
        "sha256:65b74942f7e7ed5c60fde4ac15fc4ba715904c398c8b16dd451f4907d90347d4", 686_000_000, (),
    ),
}
# Every pinned image of the catalog, by reference.
PINNED_IMAGES: dict[str, PinnedImage] = {
    image.ref: image
    for image in (
        IMMICH_SERVER, IMMICH_ML, IMMICH_ML_RKNN, IMMICH_POSTGRES, VALKEY,
        BOOKSTACK, BOOKSTACK_MARIADB, PAPERLESS, PAPERLESS_VALKEY, LINKWARDEN, LINKWARDEN_POSTGRES,
        *APP_IMAGES.values(),
    )
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


def _secret(app: str, path: str, container: str, unlocks: str, digits: int = 64) -> VolumeMapping:
    """A password or key of ``app`` that UNOS generates at ``{pool}/apps/<app>/<path>``, bound
    read-only at ``container``, beside the data ``unlocks`` that was set up with it (ADR-0381)."""
    return VolumeMapping(
        host=f"{APPS_DATA_PATH}/{app}/{path}",
        container=container,
        readonly=True,
        is_file=True,
        generated_secret=True,
        secret_for=f"{APPS_DATA_PATH}/{app}/{unlocks}",
        secret_digits=digits,
    )


# BookStack's database (ADR-0415): its user's password and root's, and BookStack's APP_KEY, which
# encrypts what BookStack keeps encrypted in the database (its users' MFA secrets). Laravel takes
# a key without "base64:" as its bytes, and AES-256 needs 32 of them: 32 hexadecimal digits.
BOOKSTACK_DB_DATA = f"{APPS_DATA_PATH}/bookstack/database/data"
BOOKSTACK_DB_PASSWORD = _secret("bookstack", "database/password", "/run/secrets/db-password", "database/data")
BOOKSTACK_DB_ROOT_PASSWORD = _secret(
    "bookstack", "database/root-password", "/run/secrets/db-root-password", "database/data"
)
BOOKSTACK_APP_KEY = _secret("bookstack", "app-key", "/run/secrets/app-key", "database/data", digits=32)
# Paperless-ngx's Django SECRET_KEY, which signs its sessions and tokens; its database is SQLite in
# its data folder.
PAPERLESS_SECRET_KEY = _secret("paperless-ngx", "secret-key", "/run/secrets/secret-key", "data")
# Linkwarden's database password and NextAuth's secret, which signs its sessions and access tokens.
LINKWARDEN_DB_DATA = f"{APPS_DATA_PATH}/linkwarden/database/data"
LINKWARDEN_DB_PASSWORD = _secret("linkwarden", "database/password", "/run/secrets/db-password", "database/data")
LINKWARDEN_AUTH_SECRET = _secret("linkwarden", "nextauth-secret", "/run/secrets/nextauth-secret", "database/data")
# Linkwarden takes its database's password only inside DATABASE_URL and its session secret only as
# NEXTAUTH_SECRET: its container's command reads both from their files into those variables, which
# so are in no Docker configuration, then runs the CMD of the pinned image's configuration
# (LINKWARDEN_START, recorded in its pin): its database's migrations, then its web server and its
# worker. The password is hexadecimal and needs no escape in the URL.
LINKWARDEN_START = LINKWARDEN.cmd[2]
LINKWARDEN_COMMAND = (
    'password="$(cat "$DATABASE_PASSWORD_FILE")" && secret="$(cat "$NEXTAUTH_SECRET_FILE")"'
    ' && export DATABASE_URL="postgresql://linkwarden:${password}@database:5432/linkwarden"'
    ' NEXTAUTH_SECRET="${secret}" && unset password secret && ' + LINKWARDEN_START
)
# The temporary files of a LinuxServer or Paperless-ngx image's s6-overlay, the variables that it
# reads from files (FILE__*, *_FILE) among them: in RAM, where s6 must be able to run its scripts.
S6_RUN = {"/run": "exec,size=16m"}

# Mosquitto's configuration on a fresh install (ADR-0480): none of the settings of the image's own
# file, which opens an MQTT listener on port 1883 to anonymous clients, so Mosquitto listens only
# inside its container, its local-only mode, until the administrator adds a listener and its
# authentication.
MOSQUITTO_CONF = """\
# Eclipse Mosquitto's configuration, which UNOS wrote at the install because it was missing.
# UNOS does not write it again: edit it here, then restart the app.
#
# It sets nothing, so Mosquitto listens only inside its container: no other container or host
# can connect. To let them, add a listener and its authentication, such as:
#
#   listener 1883
#   password_file /mosquitto/data/passwords
#
# The container sees this file and the app's folders data (/mosquitto/data) and log
# (/mosquitto/log), not the rest of this folder: make the passwords file in the data folder with
# mosquitto_passwd. To keep retained messages across restarts, and to log to the log folder:
#
#   persistence true
#   persistence_location /mosquitto/data/
#   log_dest file /mosquitto/log/mosquitto.log
"""

# Prometheus's configuration on a fresh install: the image's own, its
# documentation/examples/prometheus.yml (v3.16.0), which scrapes Prometheus itself.
PROMETHEUS_YML = """\
# Prometheus's configuration, which UNOS wrote at the install because it was missing: the image's
# own example. UNOS does not write it again: edit it here, then restart the app.

# my global config
global:
  scrape_interval: 15s # Set the scrape interval to every 15 seconds. Default is every 1 minute.
  evaluation_interval: 15s # Evaluate rules every 15 seconds. The default is every 1 minute.
  # scrape_timeout is set to the global default (10s).

# Alertmanager configuration
alerting:
  alertmanagers:
    - static_configs:
        - targets:
          # - alertmanager:9093

# Load rules once and periodically evaluate them according to the global 'evaluation_interval'.
rule_files:
  # - "first_rules.yml"
  # - "second_rules.yml"

# A scrape configuration containing exactly one endpoint to scrape:
# Here it's Prometheus itself.
scrape_configs:
  # The job name is added as a label `job=<job_name>` to any timeseries scraped from this config.
  - job_name: "prometheus"

    # metrics_path defaults to '/metrics'
    # scheme defaults to 'http'.

    static_configs:
      - targets: ["localhost:9090"]
       # The label name is added as a label `label_name=<label_value>` to any timeseries scraped from this config.
        labels:
          app: "prometheus"
    scrape_native_histograms: true
"""

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
        image=APP_IMAGES["plex"].ref,
        version=APP_IMAGES["plex"].tag,
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
        image=APP_IMAGES["jellyfin"].ref,
        version=APP_IMAGES["jellyfin"].tag,
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
        image=APP_IMAGES["emby"].ref,
        version=APP_IMAGES["emby"].tag,
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
        image=APP_IMAGES["navidrome"].ref,
        version=APP_IMAGES["navidrome"].tag,
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
        image=APP_IMAGES["photoprism"].ref,
        version=APP_IMAGES["photoprism"].tag,
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
                    # Immich's server migrates the database at its start and refuses a downgrade:
                    # an update copies it first (ADR-0408).
                    VolumeMapping(host=IMMICH_DB_DATA, container="/var/lib/postgresql/data", copy_before_update=True),
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
        image=APP_IMAGES["audiobookshelf"].ref,
        version=APP_IMAGES["audiobookshelf"].tag,
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
        image=APP_IMAGES["calibre-web"].ref,
        version=APP_IMAGES["calibre-web"].tag,
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
        image=APP_IMAGES["kavita"].ref,
        version=APP_IMAGES["kavita"].tag,
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
        image=APP_IMAGES["sonarr"].ref,
        version=APP_IMAGES["sonarr"].tag,
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
        image=APP_IMAGES["radarr"].ref,
        version=APP_IMAGES["radarr"].tag,
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
        image=APP_IMAGES["lidarr"].ref,
        version=APP_IMAGES["lidarr"].tag,
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
        id="prowlarr",
        name="Prowlarr",
        description="Indexer manager for Sonarr, Radarr, Lidarr - centralized tracker management",
        icon="🔎",
        category=AppCategory.MEDIA,
        source=AppSource.LINUXSERVER,
        image=APP_IMAGES["prowlarr"].ref,
        version=APP_IMAGES["prowlarr"].tag,
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
        image=APP_IMAGES["bazarr"].ref,
        version=APP_IMAGES["bazarr"].tag,
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
        image=APP_IMAGES["overseerr"].ref,
        version=APP_IMAGES["overseerr"].tag,
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
        image=APP_IMAGES["tautulli"].ref,
        version=APP_IMAGES["tautulli"].tag,
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
        image=APP_IMAGES["transmission"].ref,
        version=APP_IMAGES["transmission"].tag,
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
        image=APP_IMAGES["qbittorrent"].ref,
        version=APP_IMAGES["qbittorrent"].tag,
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
        image=APP_IMAGES["deluge"].ref,
        version=APP_IMAGES["deluge"].tag,
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
        image=APP_IMAGES["sabnzbd"].ref,
        version=APP_IMAGES["sabnzbd"].tag,
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
        image=APP_IMAGES["nzbget"].ref,
        version=APP_IMAGES["nzbget"].tag,
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
        image=APP_IMAGES["jdownloader2"].ref,
        version=APP_IMAGES["jdownloader2"].tag,
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
        image=APP_IMAGES["aria2"].ref,
        version=APP_IMAGES["aria2"].tag,
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
        image=APP_IMAGES["nextcloud"].ref,
        version=APP_IMAGES["nextcloud"].tag,
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
        image=APP_IMAGES["syncthing"].ref,
        version=APP_IMAGES["syncthing"].tag,
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
        image=APP_IMAGES["filebrowser"].ref,
        version=APP_IMAGES["filebrowser"].tag,
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
        image=APP_IMAGES["trilium"].ref,
        version=APP_IMAGES["trilium"].tag,
        website="https://triliumnotes.org",
        github="https://github.com/TriliumNext/Trilium",
        documentation="https://docs.triliumnotes.org",
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
        image=APP_IMAGES["wikijs"].ref,
        version=APP_IMAGES["wikijs"].tag,
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
        image=APP_IMAGES["stirling-pdf"].ref,
        version=APP_IMAGES["stirling-pdf"].tag,
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

    # BookStack of two containers (ADR-0415): LinuxServer's image, which runs nginx, PHP and
    # BookStack's migrations, and its MariaDB on the network unos-app-bookstack. The passwords and
    # the APP_KEY are files that UNOS generates on the pool, which LinuxServer's FILE__ variables
    # and MariaDB's *_FILE ones read at the start, as root. BookStack starts once its database
    # takes connections over TCP (MariaDB's healthcheck.sh), which its first start's temporary
    # server does not: its migrations then meet a database that is set up. Its first account is
    # admin@admin.com with the password "password", which the App Store's notice asks to change.
    AppTemplate(
        id="bookstack",
        name="BookStack",
        description="Simple, self-hosted wiki platform for organizing and storing information",
        icon="📚",
        category=AppCategory.DOCUMENTS,
        source=AppSource.LINUXSERVER,
        image=BOOKSTACK.ref,
        version=BOOKSTACK.tag,
        website="https://www.bookstackapp.com",
        github="https://github.com/linuxserver/docker-bookstack",
        documentation="https://www.bookstackapp.com/docs/",
        min_memory="256m",
        recommended_memory="1g",
        ports=[PortMapping(host=6875, container=80)],
        volumes=[
            # The image's VOLUME: its .env, uploads, images, themes, logs and backups.
            VolumeMapping(host=f"{APPS_DATA_PATH}/bookstack/config", container="/config"),
            BOOKSTACK_APP_KEY,
            BOOKSTACK_DB_PASSWORD,
        ],
        environment=[
            EnvVar(name="PUID", value="1000"),
            EnvVar(name="PGID", value="1000"),
            EnvVar(name="TZ", value="UTC"),
            EnvVar(
                name="APP_URL",
                value="",
                description="The address you open BookStack at, with its port, such as http://192.168.1.10:6875: "
                "BookStack makes every link of its pages with it",
                required=True,
            ),
            EnvVar(name="FILE__APP_KEY", value=BOOKSTACK_APP_KEY.container, editable=False),
            EnvVar(name="DB_HOST", value="database", editable=False),
            EnvVar(name="DB_PORT", value="3306", editable=False),
            EnvVar(name="DB_DATABASE", value="bookstack", editable=False),
            EnvVar(name="DB_USERNAME", value="bookstack", editable=False),
            EnvVar(name="FILE__DB_PASSWORD", value=BOOKSTACK_DB_PASSWORD.container, editable=False),
        ],
        # nginx keeps an upload's body in /tmp while PHP keeps its own copy there: room for two of
        # the 100 MB that the image allows.
        tmpfs={**S6_RUN, "/tmp": "size=256m"},
        depends_on=["database"],
        companions=[
            Companion(
                name="database",
                image=BOOKSTACK_MARIADB.ref,
                volumes=[
                    # BookStack migrates its database at its start: an update copies it first (ADR-0408).
                    VolumeMapping(host=BOOKSTACK_DB_DATA, container="/var/lib/mysql", copy_before_update=True),
                    BOOKSTACK_DB_PASSWORD,
                    BOOKSTACK_DB_ROOT_PASSWORD,
                ],
                environment=[
                    EnvVar(name="MARIADB_DATABASE", value="bookstack"),
                    EnvVar(name="MARIADB_USER", value="bookstack"),
                    EnvVar(name="MARIADB_PASSWORD_FILE", value=BOOKSTACK_DB_PASSWORD.container),
                    EnvVar(name="MARIADB_ROOT_PASSWORD_FILE", value=BOOKSTACK_DB_ROOT_PASSWORD.container),
                    # A newer MariaDB upgrades the database's system tables at its first start.
                    EnvVar(name="MARIADB_AUTO_UPGRADE", value="1"),
                ],
                # Its socket and process id, and its temporary tables.
                tmpfs={"/run/mysqld": "size=1m", "/tmp": "size=64m"},
                stop_timeout=60,
                # Within 10 s, which ends its client too (coreutils' timeout signals its process group).
                ready_command=["timeout", "10", "healthcheck.sh", "--connect", "--innodb_initialized"],
            ),
        ],
    ),

    # Paperless-ngx of two containers (ADR-0415): its server, its consumer and its task queue in
    # one image, with its database in SQLite in its data folder, as its docker-compose.sqlite.yml,
    # and Valkey as the broker of its task queue on the network unos-app-paperless-ngx. Its
    # SECRET_KEY is a file that UNOS generates on the pool. Everything it writes is on the pool:
    # its documents, the OCR's temporary files (PAPERLESS_SCRATCH_DIR and TMPDIR), its search index
    # and its logs; S6_READ_ONLY_ROOT keeps its start from compiling Python's bytecode into the
    # container's layer, and no PAPERLESS_OCR_LANGUAGES is set, which would install packages there.
    # Its first visit asks for its first account, which becomes its superuser.
    AppTemplate(
        id="paperless-ngx",
        name="Paperless-ngx",
        description="Document management system that transforms physical documents into searchable archive",
        icon="📄",
        category=AppCategory.DOCUMENTS,
        source=AppSource.COMMUNITY,
        image=PAPERLESS.ref,
        version=PAPERLESS.tag,
        website="https://docs.paperless-ngx.com",
        github="https://github.com/paperless-ngx/paperless-ngx",
        documentation="https://docs.paperless-ngx.com/",
        min_memory="1g",
        recommended_memory="2g",
        ports=[PortMapping(host=8010, container=8000)],
        volumes=[
            # Its database, search index and logs; an update copies it first (ADR-0408).
            VolumeMapping(
                host=f"{APPS_DATA_PATH}/paperless-ngx/data", container="/usr/src/paperless/data",
                copy_before_update=True,
            ),
            VolumeMapping(host=f"{APPS_DATA_PATH}/paperless-ngx/media", container="/usr/src/paperless/media"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/paperless-ngx/export", container="/usr/src/paperless/export"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/paperless-ngx/consume", container="/usr/src/paperless/consume"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/paperless-ngx/scratch", container="/usr/src/paperless/scratch"),
            PAPERLESS_SECRET_KEY,
        ],
        environment=[
            EnvVar(name="PAPERLESS_REDIS", value="redis://redis:6379", editable=False),
            EnvVar(name="PAPERLESS_DBENGINE", value="sqlite", editable=False),
            EnvVar(name="PAPERLESS_SECRET_KEY_FILE", value=PAPERLESS_SECRET_KEY.container, editable=False),
            EnvVar(name="PAPERLESS_SCRATCH_DIR", value="/usr/src/paperless/scratch", editable=False),
            EnvVar(name="TMPDIR", value="/usr/src/paperless/scratch", editable=False),
            EnvVar(name="S6_READ_ONLY_ROOT", value="1", editable=False),
            EnvVar(name="PAPERLESS_OCR_LANGUAGE", value="eng", description="OCR language (eng, deu, fra, ita or spa)"),
            EnvVar(name="PAPERLESS_TIME_ZONE", value="UTC"),
            EnvVar(
                name="PAPERLESS_URL",
                value="",
                description="Optional: the address you open Paperless-ngx at, such as https://paperless.example.com, "
                "behind a reverse proxy",
            ),
        ],
        tmpfs={**S6_RUN, "/tmp": "size=64m"},
        # Its image's curl every 30 s would write the container's state to the system disk.
        image_healthcheck=False,
        depends_on=["redis"],
        companions=[
            Companion(
                name="redis",
                image=PAPERLESS_VALKEY.ref,
                # Its snapshots of the queued tasks; the image declares no VOLUME for its /data.
                volumes=[VolumeMapping(host=f"{APPS_DATA_PATH}/paperless-ngx/redis", container="/data")],
            ),
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
        image=APP_IMAGES["portainer"].ref,
        version=APP_IMAGES["portainer"].tag,
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
        image=APP_IMAGES["uptime-kuma"].ref,
        version=APP_IMAGES["uptime-kuma"].tag,
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
        image=APP_IMAGES["homepage"].ref,
        version=APP_IMAGES["homepage"].tag,
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
        image=APP_IMAGES["homarr"].ref,
        version=APP_IMAGES["homarr"].tag,
        website="https://homarr.dev",
        github="https://github.com/ajnart/homarr",
        documentation="https://homarr.dev/docs/getting-started/",
        min_memory="128m",
        recommended_memory="256m",
        ports=[PortMapping(host=7575, container=7575)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/homarr/configs", container="/app/data/configs"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/homarr/icons", container="/app/public/icons"),
            # The image's VOLUME: its database.
            VolumeMapping(host=f"{APPS_DATA_PATH}/homarr/data", container="/data"),
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
        image=APP_IMAGES["heimdall"].ref,
        version=APP_IMAGES["heimdall"].tag,
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
        image=APP_IMAGES["grafana"].ref,
        version=APP_IMAGES["grafana"].tag,
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
        image=APP_IMAGES["prometheus"].ref,
        version=APP_IMAGES["prometheus"].tag,
        website="https://prometheus.io",
        github="https://github.com/prometheus/prometheus",
        documentation="https://prometheus.io/docs/",
        min_memory="256m",
        recommended_memory="1g",
        ports=[PortMapping(host=9090, container=9090)],
        volumes=[
            # The image runs as nobody (uid/gid 65534); it writes its TSDB to /prometheus. Its
            # configuration is a file it only reads, the image's own on a fresh install.
            VolumeMapping(
                host=f"{APPS_DATA_PATH}/prometheus/config/prometheus.yml", container="/etc/prometheus/prometheus.yml",
                readonly=True, is_file=True, content=PROMETHEUS_YML,
            ),
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
        image=APP_IMAGES["dozzle"].ref,
        version=APP_IMAGES["dozzle"].tag,
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
        image=APP_IMAGES["netdata"].ref,
        version=APP_IMAGES["netdata"].tag,
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
        image=APP_IMAGES["glances"].ref,
        version=APP_IMAGES["glances"].tag,
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
        image=APP_IMAGES["changedetection"].ref,
        version=APP_IMAGES["changedetection"].tag,
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
        image=APP_IMAGES["homeassistant"].ref,
        version=APP_IMAGES["homeassistant"].tag,
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
        image=APP_IMAGES["frigate"].ref,
        version=APP_IMAGES["frigate"].tag,
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
        image=APP_IMAGES["scrypted"].ref,
        version=APP_IMAGES["scrypted"].tag,
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
        image=APP_IMAGES["node-red"].ref,
        version=APP_IMAGES["node-red"].tag,
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
        image=APP_IMAGES["mosquitto"].ref,
        version=APP_IMAGES["mosquitto"].tag,
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
            VolumeMapping(
                host=f"{APPS_DATA_PATH}/mosquitto/config/mosquitto.conf", container="/mosquitto/config/mosquitto.conf",
                readonly=True, is_file=True, content=MOSQUITTO_CONF,
            ),
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
        image=APP_IMAGES["zigbee2mqtt"].ref,
        version=APP_IMAGES["zigbee2mqtt"].tag,
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
            # Its MQTT broker: the Mosquitto app of the same NAS, on the port that it publishes there
            # (ADR-0415); Mosquitto takes connections from other hosts only with a listener in its
            # mosquitto.conf.
            EnvVar(
                name="ZIGBEE2MQTT_CONFIG_MQTT_SERVER",
                value="mqtt://host.docker.internal:1883",
                description="The MQTT broker: the Mosquitto app of this NAS by default, whose mosquitto.conf needs "
                "a listener on port 1883",
            ),
        ],
        privileged=True,
        host_gateway=True,
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
        image=APP_IMAGES["adguard-home"].ref,
        version=APP_IMAGES["adguard-home"].tag,
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
        image=APP_IMAGES["pihole"].ref,
        version=APP_IMAGES["pihole"].tag,
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
        image=APP_IMAGES["tailscale"].ref,
        version=APP_IMAGES["tailscale"].tag,
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
        image=APP_IMAGES["wireguard"].ref,
        version=APP_IMAGES["wireguard"].tag,
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
        image=APP_IMAGES["nginx-proxy-manager"].ref,
        version=APP_IMAGES["nginx-proxy-manager"].tag,
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
        image=APP_IMAGES["traefik"].ref,
        version=APP_IMAGES["traefik"].tag,
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
        image=APP_IMAGES["ollama"].ref,
        version=APP_IMAGES["ollama"].tag,
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
        image=APP_IMAGES["open-webui"].ref,
        version=APP_IMAGES["open-webui"].tag,
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
        # host.docker.internal is the NAS, where the Ollama app publishes its port 11434 (ADR-0415).
        host_gateway=True,
    ),

    AppTemplate(
        id="localai",
        name="LocalAI",
        description="Self-hosted OpenAI-compatible API for running LLMs, image generation, and more",
        icon="🤖",
        category=AppCategory.AI,
        source=AppSource.COMMUNITY,
        image=APP_IMAGES["localai"].ref,
        version=APP_IMAGES["localai"].tag,
        website="https://localai.io",
        github="https://github.com/mudler/LocalAI",
        documentation="https://localai.io/docs/",
        min_memory="4g",
        recommended_memory="8g",
        ports=[PortMapping(host=8080, container=8080)],
        volumes=[
            VolumeMapping(host=f"{APPS_DATA_PATH}/localai/models", container="/models"),
            # The image's other VOLUMEs: the backends it downloads and its configuration.
            VolumeMapping(host=f"{APPS_DATA_PATH}/localai/backends", container="/backends"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/localai/configuration", container="/configuration"),
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
        image=APP_IMAGES["duplicati"].ref,
        version=APP_IMAGES["duplicati"].tag,
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
        image=APP_IMAGES["restic-rest-server"].ref,
        version=APP_IMAGES["restic-rest-server"].tag,
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
        image=APP_IMAGES["mealie"].ref,
        version=APP_IMAGES["mealie"].tag,
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
        image=APP_IMAGES["actual-budget"].ref,
        version=APP_IMAGES["actual-budget"].tag,
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
        image=APP_IMAGES["n8n"].ref,
        version=APP_IMAGES["n8n"].tag,
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

    # Linkwarden of two containers (ADR-0415): its web server and its worker in one image, which
    # also archives pages with Chromium, and PostgreSQL on the network unos-app-linkwarden, as its
    # docker-compose.yml (without Meilisearch, which its search does not need). Its command reads
    # the database's password and NextAuth's secret from the files that UNOS generates
    # (LINKWARDEN_COMMAND). Linkwarden starts once PostgreSQL takes connections over TCP, which its
    # first start's temporary server does not. Its archives are on the pool, Next.js's image cache
    # too, and Chromium's profiles and caches in RAM; its first account to register is its
    # administrator.
    AppTemplate(
        id="linkwarden",
        name="Linkwarden",
        description="Collaborative bookmark manager to collect, organize and preserve webpages",
        icon="🔖",
        category=AppCategory.PRODUCTIVITY,
        source=AppSource.COMMUNITY,
        image=LINKWARDEN.ref,
        version=LINKWARDEN.tag,
        website="https://linkwarden.app",
        github="https://github.com/linkwarden/linkwarden",
        documentation="https://docs.linkwarden.app/",
        min_memory="1g",
        recommended_memory="2g",
        ports=[PortMapping(host=3010, container=3000)],
        volumes=[
            # Its archives of the pages: screenshots, PDFs, readable copies.
            VolumeMapping(host=f"{APPS_DATA_PATH}/linkwarden/data", container="/data/data"),
            VolumeMapping(host=f"{APPS_DATA_PATH}/linkwarden/next-cache", container="/data/apps/web/.next/cache"),
            LINKWARDEN_DB_PASSWORD,
            LINKWARDEN_AUTH_SECRET,
        ],
        environment=[
            EnvVar(
                name="NEXTAUTH_URL",
                value="http://localhost:3010/api/v1/auth",
                description="The address of Linkwarden's sign-in, ending in /api/v1/auth; set the address you open "
                "it at behind a reverse proxy with HTTPS",
            ),
            EnvVar(
                name="NEXT_PUBLIC_DISABLE_REGISTRATION",
                value="false",
                description="true: nobody can register a new account. Anyone who reaches Linkwarden can register "
                "while it is false",
            ),
            EnvVar(name="DATABASE_PASSWORD_FILE", value=LINKWARDEN_DB_PASSWORD.container, editable=False),
            EnvVar(name="NEXTAUTH_SECRET_FILE", value=LINKWARDEN_AUTH_SECRET.container, editable=False),
            # No telemetry, no update check of Prisma, and the caches in RAM: nothing of these is
            # written into the container's layer.
            EnvVar(name="NEXT_TELEMETRY_DISABLED", value="1", editable=False),
            EnvVar(name="CHECKPOINT_DISABLE", value="1", editable=False),
            EnvVar(name="XDG_CACHE_HOME", value="/tmp/cache", editable=False),
        ],
        command=["sh", "-c", LINKWARDEN_COMMAND],
        # Chromium's profiles and its shared memory (Playwright runs it with --disable-dev-shm-usage),
        # and Prisma's cache, which its start writes to /root/.cache/prisma whatever XDG_CACHE_HOME
        # says (the dev device's check, 2026-11-19).
        tmpfs={"/tmp": "size=512m", "/root/.cache": "size=64m"},
        # Its image's curl every 30 s would write the container's state to the system disk.
        image_healthcheck=False,
        depends_on=["database"],
        companions=[
            Companion(
                name="database",
                image=LINKWARDEN_POSTGRES.ref,
                volumes=[
                    # Linkwarden migrates its database at its start: an update copies it first (ADR-0408).
                    VolumeMapping(
                        host=LINKWARDEN_DB_DATA, container="/var/lib/postgresql/data", copy_before_update=True
                    ),
                    LINKWARDEN_DB_PASSWORD,
                ],
                environment=[
                    EnvVar(name="POSTGRES_USER", value="linkwarden"),
                    EnvVar(name="POSTGRES_DB", value="linkwarden"),
                    EnvVar(name="POSTGRES_PASSWORD_FILE", value=LINKWARDEN_DB_PASSWORD.container),
                ],
                tmpfs={"/var/run/postgresql": "size=1m"},
                stop_timeout=60,
                ready_command=["pg_isready", "-t", "5", "-h", "127.0.0.1", "-U", "linkwarden", "-d", "linkwarden"],
            ),
        ],
    ),

    AppTemplate(
        id="vikunja",
        name="Vikunja",
        description="Open source to-do app with lists, kanban boards, and team collaboration",
        icon="✅",
        category=AppCategory.PRODUCTIVITY,
        source=AppSource.COMMUNITY,
        image=APP_IMAGES["vikunja"].ref,
        version=APP_IMAGES["vikunja"].tag,
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
        image=APP_IMAGES["gitea"].ref,
        version=APP_IMAGES["gitea"].tag,
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
        image=APP_IMAGES["code-server"].ref,
        version=APP_IMAGES["code-server"].tag,
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
        image=APP_IMAGES["vaultwarden"].ref,
        version=APP_IMAGES["vaultwarden"].tag,
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
        image=APP_IMAGES["authelia"].ref,
        version=APP_IMAGES["authelia"].tag,
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
        image=APP_IMAGES["crowdsec"].ref,
        version=APP_IMAGES["crowdsec"].tag,
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
