"""The off-device copy's API (ADR-0337, ADR-0422): the copy target, UNOS's key pair for SFTP, Run now, the
state, the snapshots and the restores.

Administrators only: main.py includes the router with the others that are (_ADMIN_ONLY), and the
router asks for an administrator itself, since a restore writes files into the pools and the
settings decide where the configuration and the data go. service.py holds the logic.
"""

from fastapi import APIRouter, Depends, Request

from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.models import User
from ..auth.utils import require_admin
from ..openapi import BODY, answer, errors
from . import service

router = APIRouter(prefix="/offsite", tags=["offsite"], dependencies=[Depends(require_admin)])

# The API documentation (ADR-0417).
_CONFIGURATION_ID = "4f9a1c2e7d0b5a3f8e6c1d9b2a4f7e0c3b5d8a1f6e9c2b4d7a0f3e5c8b1d6a9f"
_DATA_ID = "9b31d07f2c5e8a4b6d1f3e7c0a9b2d5f8e1c4a7b0d3f6e9c2a5b8d1f4e7c0a3b"
_SETTINGS_ANSWER = {
    "configured": True,
    "settings": {
        "kind": "disk", "path": "/mnt/pools/backup/unos-copy", "endpoint": None, "bucket": None, "prefix": None,
        "region": None, "access_key_id": None, "username": None, "host": None, "port": None, "host_key": None,
        "tls": None, "certificate": None, "certificate_sha256": None,
        "sources": ["/mnt/pools/data/documents", "/mnt/pools/data/photos"],
        "limit_upload_kib": 0, "limit_download_kib": 0, "keep_daily": 7, "keep_weekly": 4, "keep_monthly": 12,
        "check_percent": 2,
    },
    "has_password": True,
    "has_s3_secret_access_key": False,
    "has_rest_password": False,
    "sftp_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHk0bzvQ8a3m2Yc3k6m1x7bU4yJcV0tN1f9zW2p8sLqE unos-off-device-copy",
    "host_key_fingerprint": None,
    "pools": [{"name": "backup", "path": "/mnt/pools/backup"}, {"name": "data", "path": "/mnt/pools/data"}],
    "pools_base": "/mnt/pools",
    "warnings": [],
}
_SETTINGS_TEXT = (
    "configured: whether a copy repository is set up; settings: its settings without a secret, null when none; "
    "has_password, has_s3_secret_access_key and has_rest_password: whether those secrets are stored; "
    "sftp_public_key: the public key of UNOS's key pair for SFTP, null before one is made; host_key_fingerprint: "
    "the SHA256 fingerprint of an SFTP server's pinned host key, null otherwise; pools: the managed pools "
    "(name, path), which a copy takes by default; pools_base: the pools directory; warnings: same_disk, with "
    "its message, when a disk copy repository is on a disk that it copies."
)
_UNREADABLE_SETTINGS = "The stored settings or secrets of the off-device copy cannot be read."
_BUSY = "A copy, a restore or a change of the settings runs."
_NOT_READY = "No copy repository is set up, or its password or the S3 secret access key is not stored."
_REPOSITORY_UNUSABLE = "The copy repository's folder cannot be used, or restic cannot read the repository"


@router.get(
    "/settings", summary="Read the off-device copy's settings",
    responses={**answer(_SETTINGS_ANSWER, _SETTINGS_TEXT), **errors({500: _UNREADABLE_SETTINGS})},
)
async def get_settings():
    """The copy target and what is copied, without secrets: has_password and has_s3_secret_access_key
    say whether they are stored. pools: the managed pools, which a copy takes by default."""
    return await service.settings_answer()


@router.put(
    "/settings", summary="Set up the off-device copy",
    responses={
        **answer(_SETTINGS_ANSWER, f"The settings as GET /offsite/settings answers them. {_SETTINGS_TEXT}"),
        **errors(BODY, {
            400: "A setting is not valid or a secret is missing; a folder cannot be copied or cannot hold the copy "
                 "repository; the host has no ssh for SFTP, or UNOS has no key pair for it; the store cannot be "
                 "reached, shows another certificate or host key than the one pinned or typed, or restic cannot open "
                 "or make the repository, with restic's message.",
            409: _BUSY,
            500: _UNREADABLE_SETTINGS,
        }),
    },
)
async def put_settings(
    request: service.SettingsRequest, http_request: Request, user: User = Depends(require_admin)
):
    """Check the settings and test the target: restic opens its repository with the password, or makes
    one when none is there. 400 with restic's message for a wrong password or a store out of reach. The
    first test of an SFTP server pins its host key, which every later command holds to.
    The audit log gets settings_changed when they changed, with the names of the fields and never a
    secret.
    \f
    ADR-0402.
    """
    changes: list[tuple[str, str]] = []
    answer = await service.save_settings(request, changes)
    for where, details in changes:
        await audit_change(AuditAction.SETTINGS_CHANGED, http_request, user.username, resource_type="offsite",
                           resource_id=where, details=details)
    return answer


@router.post(
    "/sftp-key", summary="Make UNOS's key pair for SFTP",
    responses={
        **answer(_SETTINGS_ANSWER, f"The settings as GET /offsite/settings answers them, with the new public key in "
                                   f"sftp_public_key. {_SETTINGS_TEXT}"),
        **errors({409: _BUSY, 500: _UNREADABLE_SETTINGS}),
    },
)
async def make_sftp_key(http_request: Request, user: User = Depends(require_admin)):
    """Make a new SSH key pair (Ed25519) with which the copy logs in to an SFTP server: its public key
    goes to the server's authorized keys, its private key stays in the secrets file and is never
    answered. It replaces the one before, which a server then no longer takes. The audit log gets
    settings_changed with the new key's fingerprint.
    \f
    ADR-0422.
    """
    changes: list[tuple[str, str]] = []
    answer = await service.make_key_pair(changes)
    for where, details in changes:
        await audit_change(AuditAction.SETTINGS_CHANGED, http_request, user.username, resource_type="offsite",
                           resource_id=where, details=details, severity=AuditSeverity.WARNING)
    return answer


@router.delete(
    "/settings", summary="Forget the off-device copy's settings",
    responses={
        **answer({"status": "deleted"}, "status deleted, also when nothing was set up."),
        **errors({409: _BUSY}),
    },
)
async def delete_settings(http_request: Request, user: User = Depends(require_admin)):
    """Forget the target: the settings, the secrets (with UNOS's key pair for SFTP) and the state. The
    copies stay where they are.
    The audit log gets settings_changed when there was something to forget.
    \f
    ADR-0402.
    """
    changes: list[tuple[str, str]] = []
    answer = await service.remove_settings(changes)
    for where, details in changes:
        await audit_change(AuditAction.SETTINGS_CHANGED, http_request, user.username, resource_type="offsite",
                           resource_id=where, details=details, severity=AuditSeverity.WARNING)
    return answer


@router.get(
    "/status", summary="Read what the off-device copy does",
    responses=answer(
        {
            "running": {"kind": "copy", "started": "2026-11-19T02:00:00.512331+00:00", "step": "data",
                        "snapshot_id": None, "folder": None},
            "last_copy": {
                "started": "2026-11-18T02:00:00.498120+00:00", "ended": "2026-11-18T02:41:13.220871+00:00",
                "trigger": "task", "success": True, "step": None, "error": None,
                "summary": {"files_new": 42, "files_changed": 3, "files_unmodified": 18211, "bytes_added": 734003200,
                            "configuration_snapshot": _CONFIGURATION_ID, "data_snapshot": _DATA_ID},
                "check": "passed", "restore_test": "passed", "note": None,
            },
            "last_restore": {
                "snapshot_id": _DATA_ID, "path": "/mnt/pools/data/photos/2025",
                "folder": "/mnt/pools/data/restored-20261117-153002", "started": "2026-11-17T15:30:02.104772+00:00",
                "ended": "2026-11-17T15:52:47.930115+00:00", "success": True, "error": None,
            },
        },
        "running: what runs now (kind copy, restore-files, restore-configuration, save or remove, with its "
        "start, step, snapshot ID and folder), null when nothing does; last_copy and last_restore: the last copy "
        "and the last restore of files, null before the first.",
    ),
)
async def get_status():
    """What runs, and the last copy and restore of files."""
    return await service.status_answer()


@router.post(
    "/run", status_code=202, summary="Start a copy now",
    responses={
        **answer({"status": "started"}, "The copy started; GET /offsite/status shows how it goes.", status=202),
        **errors({409: "No copy repository is set up, or a copy, a restore or a change of the settings runs.",
                  500: _UNREADABLE_SETTINGS}),
    },
)
async def run_now():
    """Start a copy in the background; GET /offsite/status shows it."""
    await service.start_copy()
    return {"status": "started"}


@router.get(
    "/snapshots", summary="List the snapshots of the copy repository",
    responses={
        **answer(
            {"snapshots": [
                {"id": _DATA_ID, "short_id": _DATA_ID[:8], "time": "2026-11-19T02:00:44.118302157Z", "kind": "data",
                 "tags": ["unos-data"], "paths": ["/mnt/pools/data/documents", "/mnt/pools/data/photos"],
                 "hostname": "unos", "size": 51539607552, "files": 18256},
                {"id": _CONFIGURATION_ID, "short_id": _CONFIGURATION_ID[:8], "time": "2026-11-19T02:00:41.731994512Z",
                 "kind": "configuration", "tags": ["unos-configuration"], "paths": ["/unos-configuration.tar.gz"],
                 "hostname": "unos", "size": 176128, "files": 1},
            ]},
            "snapshots: newest first, each with its ID and short ID, its time as restic wrote it, its kind "
            "(configuration, data or other), tags, paths and host, and the bytes and files it took, null when "
            "restic did not record them.",
        ),
        **errors({
            409: _NOT_READY,
            500: _UNREADABLE_SETTINGS,
            503: f"{_REPOSITORY_UNUSABLE} or list its snapshots.",
        }),
    },
)
async def get_snapshots():
    """The snapshots of the copy repository, newest first."""
    return await service.list_snapshots()


@router.post(
    "/restore-configuration", summary="Put a snapshot's configuration into the backup list",
    responses={
        **answer({"status": "success", "backup": {
            "filename": "offsite_20261119_020041.tar.gz", "size": 176128, "created": "2026-11-19T10:41:05.338271Z",
            "version": "1.0", "encrypted": False,
        }}, "status success and the new backup, offsite_<the snapshot's time in UTC>.tar.gz."),
        **errors(BODY, {
            400: "The snapshot ID is not 8 to 64 hexadecimal digits, or more than one snapshot starts with it; the "
                 "snapshot is not a configuration snapshot; or its archive is larger than a backup may be or "
                 "fails the checks of an uploaded backup.",
            404: "The copy repository has no snapshot with that ID.",
            409: "No copy repository is set up or its secrets are not stored, or a copy, a restore or a change "
                 "of the settings runs.",
            500: _UNREADABLE_SETTINGS,
            503: f"{_REPOSITORY_UNUSABLE} or the snapshot's configuration.",
        }),
    },
)
async def restore_configuration(request: service.RestoreConfigurationRequest):
    """Put the configuration of a snapshot into the backup list (offsite_<time>.tar.gz), where it is
    restored with the checks of a restore."""
    return await service.restore_configuration(request.snapshot_id)


@router.post(
    "/restore-files", status_code=202, summary="Restore files from a snapshot",
    responses={
        **answer({"status": "started", "folder": "/mnt/pools/data/restored-20261119-104512"},
                 "status started and the new folder that the restore writes; GET /offsite/status shows how it "
                 "ends.", status=202),
        **errors(BODY, {
            400: "The snapshot ID, the pool's name or the folder in the snapshot is not valid, or more than one "
                 "snapshot starts with the ID; the pool does not exist or is not mounted; or the new folder "
                 "cannot be made.",
            404: "The copy repository has no snapshot with that ID.",
            409: "No copy repository is set up or its secrets are not stored; a copy, a restore or a change of "
                 "the settings runs; or the new folder exists already.",
            500: _UNREADABLE_SETTINGS,
            503: f"{_REPOSITORY_UNUSABLE}.",
        }),
    },
)
async def restore_files(request: service.RestoreFilesRequest):
    """Restore a snapshot, or a folder of it, into a new folder restored-<UTC time> at the top of a
    pool, in the background; GET /offsite/status shows it. Nothing is overwritten."""
    return await service.restore_files(request)
