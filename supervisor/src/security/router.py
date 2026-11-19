import asyncio
import logging
import os
import subprocess
import json
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, text

from .. import host, paths
from . import firewall, serving
from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..database import get_db
from ..times import db_now
from ..auth import requirement
from ..auth.utils import (
    API_TOKEN_PREFIX, TOTP_CODE_REUSED, Credential, TotpCheck, check_totp_code, client_ip, credential,
    require_admin, verify_token,
)
from ..auth.models import TwoFactorRequirement, User, UsernameTaken
from .models import LoginAttempt
from .schemas import (
    TwoFactorRequirementDisable, TwoFactorRequirementEnable, TwoFactorRequirementStatus,
    LoginAttemptResponse, LoginHistoryResponse,
    FirewallChange, FirewallStatus, FirewallRuleCreate,
    Certificate, CertificateListResponse, CertificateType, CertificateStatus,
    CertificateUpload, SelfSignedCertRequest, ServeCertificateResponse, ServedCertificate,
    MAX_CERTIFICATE_NAME_LENGTH, is_dns_hostname, parse_ip_address, validate_certificate_domain,
)

router = APIRouter(prefix="/security", tags=["security"])
logger = logging.getLogger(__name__)

# Uploaded and self-signed certificates. The web UI's container cannot read this directory: the one
# an administrator chooses is copied, with its chain and key, into the served file of
# src/security/serving.py, which Caddy loads (ADR-0404).
CERTS_DIR = str(paths.CERTIFICATES)
# Held by every store and delete of the stored certificates and every change of the served one,
# which run in worker threads: two of them never mix their files, and the served certificate is
# never replaced or deleted beside a reload of Caddy (ADR-0404).
_CERTIFICATES_LOCK = threading.Lock()
# The id that stands for Caddy's local CA in POST /certificates/{cert_id}/serve and the audit log.
LOCAL_CA_ID = ServedCertificate.CADDY_LOCAL_CA.value
# How long openssl may take, in a worker thread (ADR-0021): reading a certificate takes well under
# a second, making a 2048-bit RSA key a few seconds on a slow board.
OPENSSL_READ_TIMEOUT = 10
OPENSSL_GENERATE_TIMEOUT = 60
# Longest username a login attempt keeps (in characters).
LOGIN_ATTEMPT_USERNAME_CHARS = 128
# The longest period that clearing the login history counts back (100 years, in days; ADR-0263).
MAX_HISTORY_DAYS = 36500


# ==================== Login History ====================

def _visible_attempts(current_user: User, username: str | None) -> list:
    """The conditions that pick the login attempts ``current_user`` may see.

    Administrators see every attempt, or those with ``username``. The history is kept by
    name, also for names without an account, so a regular user sees the attempts on their
    name since their account was created (ADR-0263) and since a rename last gave them the
    name (usernames_taken, ADR-0368): not those of a deleted or renamed account whose name
    was given to them. The times are SQLite's CURRENT_TIMESTAMP, text to the second;
    datetime() compares them so, and a sign-in in the second of the account's creation is
    the account's (a time bound from Python has microseconds and, as text, comes later).
    """
    if current_user.is_admin:
        return [LoginAttempt.username == username] if username else []
    since = func.datetime(current_user.created_at)
    taken = (
        select(func.datetime(UsernameTaken.taken_at))
        .where(UsernameTaken.username == current_user.username)
        .scalar_subquery()
    )
    return [
        LoginAttempt.username == current_user.username,
        func.datetime(LoginAttempt.timestamp) >= since,
        func.datetime(LoginAttempt.timestamp) >= func.coalesce(taken, since),
    ]


@router.get("/login-history", response_model=LoginHistoryResponse)
async def get_login_history(
    limit: int = 50,
    offset: int = 0,
    username: str | None = None,
    success: bool | None = None,
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Get login history. Admin sees all, users see only their own."""
    visible = _visible_attempts(current_user, username)
    query = select(LoginAttempt).where(*visible)

    if success is not None:
        query = query.where(LoginAttempt.success == success)

    # Get total counts
    count_query = select(func.count()).select_from(LoginAttempt).where(*visible)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get failed count
    failed_query = count_query.where(LoginAttempt.success == False)
    failed_result = await db.execute(failed_query)
    failed_count = failed_result.scalar() or 0

    # Get attempts with pagination, newest first. Times have whole seconds: the id orders
    # the attempts of one second (ADR-0262).
    query = query.order_by(desc(LoginAttempt.timestamp), desc(LoginAttempt.id)).offset(offset).limit(limit)
    result = await db.execute(query)
    attempts = result.scalars().all()

    return LoginHistoryResponse(
        attempts=[LoginAttemptResponse.model_validate(a) for a in attempts],
        total=total,
        failed_count=failed_count,
        success_count=total - failed_count
    )


@router.delete("/login-history")
async def clear_login_history(
    request: Request,
    # Up to 100 years back: a negative period would put the cutoff in the future and delete
    # every attempt, and a much longer one reaches before the year 1.
    older_than_days: int = Query(30, ge=0, le=MAX_HISTORY_DAYS),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Clear login history older than specified days. Admin only. The audit log gets
    login_history_cleared when it cleared any attempts (ADR-0402)."""
    # The stored times are UTC without a zone, as SQLite's CURRENT_TIMESTAMP gives them (ADR-0262).
    cutoff = db_now() - timedelta(days=older_than_days)

    from sqlalchemy import delete
    stmt = delete(LoginAttempt).where(LoginAttempt.timestamp < cutoff)
    result = await db.execute(stmt)
    await db.commit()

    if result.rowcount:
        count = result.rowcount
        await audit_change(
            AuditAction.LOGIN_HISTORY_CLEARED, request, current_user.username, resource_type="login_history",
            details=f"Cleared {count} login attempt{'' if count == 1 else 's'} older than {older_than_days} days",
            severity=AuditSeverity.WARNING,
        )

    return {"status": "ok", "deleted_count": result.rowcount}


# ==================== Firewall Management ====================
# The host's ufw (src/security/firewall.py, ADR-0416). The firewall's changes write an audit entry
# after they were made, for a real change only (ADR-0402); so do the certificates' changes (ADR-0404).


@router.get("/firewall", response_model=FirewallStatus)
async def get_firewall_status(request: Request, _: User = Depends(require_admin)):
    """The host's ufw: whether it is installed and on, its default policies, its rules and the change
    that waits to be kept; and whether the asking computer is in the local networks that UNOS's
    rules let in."""
    installed, state = await firewall.status()
    client_local = firewall.is_local(client_ip(request))
    if state is None:
        return FirewallStatus(installed=installed, enabled=False, default_incoming="deny", default_outgoing="allow",
                              ipv6=True, rules=[], revert_seconds=firewall.REVERT_SECONDS, client_local=client_local)
    return FirewallStatus(
        installed=True, enabled=state.enabled, default_incoming=state.defaults.incoming,
        default_outgoing=state.defaults.outgoing, ipv6=state.defaults.ipv6, rules=state.rules,
        revert_in=state.waiting.revert_in, reverting=state.waiting.reverting, revert_failed=state.waiting.failed,
        revert_seconds=firewall.REVERT_SECONDS, client_local=client_local,
    )


@router.post("/firewall/enable", response_model=FirewallChange)
async def enable_firewall(request: Request, current_user: User = Depends(require_admin)):
    """Turn the host's ufw on with UNOS's rules, the revert armed first. The audit log gets
    firewall_enabled when it was off."""
    change = await firewall.enable()
    if change.changed:
        await audit_change(AuditAction.FIREWALL_ENABLED, request, current_user.username,
                           resource_type="firewall", details="The firewall was turned on")
    return FirewallChange(changed=change.changed, revert_in=change.revert_in)


@router.post("/firewall/disable", response_model=FirewallChange)
async def disable_firewall(request: Request, current_user: User = Depends(require_admin)):
    """Turn the host's ufw off. The audit log gets firewall_disabled when it was on."""
    changed = await firewall.disable()
    if changed:
        await audit_change(AuditAction.FIREWALL_DISABLED, request, current_user.username,
                           resource_type="firewall", details="The firewall was turned off",
                           severity=AuditSeverity.WARNING)
    return FirewallChange(changed=changed)


@router.post("/firewall/confirm")
async def keep_firewall_change(request: Request, current_user: User = Depends(require_admin)):
    """Keep the firewall change that waits to be kept: its revert is called off. The audit log gets
    firewall_change_kept."""
    await firewall.keep()
    await audit_change(AuditAction.FIREWALL_CHANGE_KEPT, request, current_user.username,
                       resource_type="firewall", details="The firewall change was kept")
    return {"status": "ok"}


@router.post("/firewall/rules", response_model=FirewallChange)
async def create_firewall_rule(
    request: Request,
    rule: FirewallRuleCreate,
    current_user: User = Depends(require_admin)
):
    """Add an incoming rule to the host's ufw. The audit log gets firewall_rule_added."""
    change, added = await firewall.add_rule(rule)
    await audit_change(AuditAction.FIREWALL_RULE_ADDED, request, current_user.username,
                       resource_type="firewall_rule", resource_id=added[0].id if added else None,
                       details=f"Added the rule {firewall.describe(rule)}")
    return FirewallChange(changed=change.changed, revert_in=change.revert_in)


@router.delete("/firewall/rules/{rule_id}", response_model=FirewallChange)
async def delete_firewall_rule(
    request: Request,
    rule_id: str,
    current_user: User = Depends(require_admin)
):
    """Delete a rule of the host's ufw. The audit log gets firewall_rule_deleted."""
    change, rule = await firewall.delete_rule(rule_id)
    await audit_change(AuditAction.FIREWALL_RULE_DELETED, request, current_user.username,
                       resource_type="firewall_rule", resource_id=rule_id,
                       details=f"Removed the rule {firewall.describe(rule)}",
                       severity=AuditSeverity.WARNING)
    return FirewallChange(changed=change.changed, revert_in=change.revert_in)


@router.post("/firewall/reset", response_model=FirewallChange)
async def reset_firewall(request: Request, current_user: User = Depends(require_admin)):
    """ufw's reset, its default policies and UNOS's rules; the firewall stays off.

    The audit log gets firewall_reset: a reset is a change whenever ufw took it, as failed, with what
    was done, when a step after ufw's own reset fails (ADR-0402).
    """
    try:
        await firewall.reset()
    except firewall.ResetFailed as e:
        if e.reset_done:
            await audit_change(
                AuditAction.FIREWALL_RESET, request, current_user.username, resource_type="firewall",
                details=f"ufw was reset, then: {e.detail}", severity=AuditSeverity.WARNING, success=False,
            )
        raise
    await audit_change(
        AuditAction.FIREWALL_RESET, request, current_user.username, resource_type="firewall",
        details="The firewall was reset to its defaults: off, with UNOS's rules for the web UI, SSH and SMB",
        severity=AuditSeverity.WARNING,
    )
    return FirewallChange(changed=True)


# ==================== SSL/TLS Certificate Management ====================

def _certificate_files(name: str) -> tuple[Path, Path, Path]:
    """Certificate, key and chain file of the custom certificate ``name``.

    Raises ValueError unless all three are direct children of CERTS_DIR, so a
    name can never reach another directory.
    """
    certs_path = Path(CERTS_DIR)
    if name in ("", ".", "..") or "/" in name or "\0" in name:
        raise ValueError(f"Invalid certificate name: {name!r}")
    files = (
        certs_path / f"{name}.crt",
        certs_path / f"{name}.key",
        certs_path / f"{name}.chain.pem",
    )
    if any(path.parent != certs_path for path in files):
        raise ValueError(f"Invalid certificate name: {name!r}")
    return files


def _uploaded_certificate_name(common_name: str) -> str | None:
    """Name to store an uploaded certificate under: its subject CN, if that is a
    host name, a wildcard host name (*.example.com) or an IP address, of at most
    MAX_CERTIFICATE_NAME_LENGTH characters."""
    if common_name.startswith("*.") and is_dns_hostname(common_name[2:]):
        return common_name if len(common_name) <= MAX_CERTIFICATE_NAME_LENGTH else None
    try:
        return validate_certificate_domain(common_name)
    except ValueError:
        return None


def _write_certificate_file(path: Path, text: str, mode: int) -> None:
    """Write ``text`` to ``path``, a new file created with ``mode``.

    O_EXCL and O_NOFOLLOW: a file or a symbolic link already at ``path`` fails the write, and a
    link's target stays as it was (ADR-0042).
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def _write_private_key(path: Path, key: str) -> None:
    """Write ``key`` to ``path``, a file created readable and writable by the owner only (0600)."""
    _write_certificate_file(path, key, 0o600)


def _store_certificate(name: str, certificate: str, key: str, chain: str | None = None) -> None:
    """Store a certificate, its key and its chain (when given) under ``name`` in CERTS_DIR.

    Each file is written to a temporary file in CERTS_DIR first, ``.<random>.tmp``: short, so
    that it fits NAME_MAX for every name, and never listed as a certificate. Only once every write
    has succeeded are they renamed over the stored files, and a stored chain is removed when none
    is given, as it belonged to the replaced certificate. A failed write leaves the stored files as
    they were and no temporary file. A rename replaces a symbolic link at a file's path, never its
    target (ADR-0042). CertificateServed, and nothing written, when the certificate stored under
    ``name`` is the served one (ADR-0404). Holds _CERTIFICATES_LOCK: blocking, call it in a worker
    thread.
    """
    cert_file, key_file, chain_file = _certificate_files(name)
    files = [
        (cert_file, lambda path: _write_certificate_file(path, certificate, 0o644)),
        (key_file, lambda path: _write_private_key(path, key)),
    ]
    if chain:
        files.append((chain_file, lambda path: _write_certificate_file(path, chain, 0o644)))
    with _CERTIFICATES_LOCK:
        if _is_chosen(name):
            raise CertificateServed(name)
        cert_file.parent.mkdir(parents=True, exist_ok=True)
        staged: list[tuple[Path, Path]] = []
        try:
            for stored, write in files:
                temporary = stored.parent / f".{secrets.token_hex(8)}.tmp"
                staged.append((temporary, stored))
                write(temporary)
            for temporary, stored in staged:
                os.replace(temporary, stored)
            if not chain:
                chain_file.unlink(missing_ok=True)
        finally:
            for temporary, _ in staged:
                temporary.unlink(missing_ok=True)


class CertificateServed(Exception):
    """The certificate stored under a name is the served one: it is neither replaced nor, without
    Caddy's local CA first, deleted (ADR-0404)."""

    def __init__(self, name: str):
        super().__init__(f"{name} is the chosen served certificate: serve another certificate or Caddy's local CA "
                         "before you replace it")


def _read_stored(path: Path) -> bytes | None:
    """A stored file's bytes; None when there is none (a symbolic link at its path is not followed)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as fh:
        return fh.read()


def _stored_names() -> list[str]:
    """The names of the stored certificates: those an upload or a generation stores, as listed."""
    certs_path = Path(CERTS_DIR)
    if not certs_path.exists():
        return []
    return sorted(p.stem for p in certs_path.glob("*.crt") if _uploaded_certificate_name(p.stem) == p.stem)


def _stored_fingerprint(name: str) -> str | None:
    """The SHA-256 fingerprint of the certificate stored under ``name``, read by cryptography."""
    try:
        pem = _read_stored(_certificate_files(name)[0])
    except OSError:
        return None
    return serving.leaf_fingerprint(pem) if pem else None


def _is_chosen(name: str) -> bool:
    """Whether the served file holds the certificate stored under ``name``."""
    served = serving.read_served()
    if served is None:
        return False
    chosen = serving.leaf_fingerprint(served)
    return chosen is not None and chosen == _stored_fingerprint(name)


def _served_and_stored_fingerprints() -> tuple[str | None, dict[str, str]]:
    """The fingerprint of the served file's certificate, and of each stored certificate by its id.
    Blocking: call it in a worker thread."""
    served = serving.read_served()
    stored = {}
    for name in _stored_names():
        found = _stored_fingerprint(name)
        if found:
            stored[f"custom_{name}"] = found
    return (serving.leaf_fingerprint(served) if served else None), stored


async def _certificate_status() -> CertificateStatus:
    """What browsers get, as a TLS handshake with the web UI's Caddy shows it, and the stored
    certificate an administrator chose, as the served file holds it (ADR-0404).

    A certificate that is neither a stored one nor the chosen one is taken for the local CA's: the
    two sites of webui/Caddyfile serve nothing else. Every change of the served certificate, the
    delete of the one Caddy serves included, reloads Caddy first, so that Caddy does not keep
    serving a certificate that is no longer stored.
    """
    probed = await asyncio.to_thread(serving.probe_fingerprint)
    chosen, stored = await asyncio.to_thread(_served_and_stored_fingerprints)
    chosen_id = next((cert_id for cert_id, found in stored.items() if found == chosen), None) if chosen else None
    if probed is None:
        return CertificateStatus(served_by=ServedCertificate.UNKNOWN, chosen_id=chosen_id)
    served_id = next((cert_id for cert_id, found in stored.items() if found == probed), None)
    if served_id or probed == chosen:
        return CertificateStatus(served_by=ServedCertificate.STORED_CERTIFICATE, served_id=served_id,
                                 chosen_id=chosen_id, fingerprint=probed)
    return CertificateStatus(served_by=ServedCertificate.CADDY_LOCAL_CA, chosen_id=chosen_id, fingerprint=probed)


def _caddy_takes(tls: str, previous: bytes | None, *, file_changed: bool) -> None:
    """Reload Caddy with UNOS_TLS=``tls``. When Caddy refused it, or Docker could not run it, put
    the served file back as it was (``previous``, or none) if the request changed it, so that the
    file holds what Caddy still serves, and raise. When Docker did not answer in time (Caddy may
    have loaded it), the file stays as the request wrote it."""
    try:
        serving.reload_caddy(tls)
    except serving.CaddyTimedOut:
        raise
    except BaseException:
        if file_changed:
            if previous is None:
                serving.remove_served()
            else:
                serving.write_served(previous)
        raise


def _serve_stored(name: str) -> tuple[bool, serving.Prepared]:
    """Serve the certificate stored under ``name``: check it, write the served file and reload
    Caddy. Whether that changed anything, and what was checked: nothing changes when the file holds
    it and Caddy serves it, or cannot be asked (a TLS handshake under the lock, so that two requests
    never both reload). Raises FileNotFoundError without one, CertificateRefused, CaddyRefused or
    CaddyUnreachable (the served file then as before). Holds _CERTIFICATES_LOCK: blocking, call it
    in a worker thread.
    """
    cert_file, key_file, chain_file = _certificate_files(name)
    with _CERTIFICATES_LOCK:
        certificate, key = _read_stored(cert_file), _read_stored(key_file)
        if certificate is None:
            raise FileNotFoundError(cert_file)
        if key is None:
            raise serving.CertificateRefused("Its private key is not stored")
        prepared = serving.prepare(certificate, key, _read_stored(chain_file))
        previous = serving.read_served()
        file_changed = previous != prepared.bundle
        if not file_changed:
            probed = serving.probe_fingerprint()
            if probed is None or probed == prepared.fingerprint:
                return False, prepared
        else:
            serving.write_served(prepared.bundle)
        _caddy_takes("served", previous, file_changed=file_changed)
        return True, prepared


def _serve_local_ca() -> bool:
    """Serve Caddy's local CA: remove the served file and reload Caddy. False when nothing changed:
    there is no served file and Caddy serves no stored certificate, or cannot be asked. Holds
    _CERTIFICATES_LOCK: blocking, call it in a worker thread.
    """
    with _CERTIFICATES_LOCK:
        previous = serving.read_served()
        if previous is None:
            _, stored = _served_and_stored_fingerprints()
            if serving.probe_fingerprint() not in stored.values():
                return False
        serving.remove_served()
        _caddy_takes("local", previous, file_changed=previous is not None)
        return True


def _delete_certificate_files(name: str) -> tuple[bool, str | None]:
    """Remove the stored files of the certificate ``name``: whether one was stored under it, and the
    id of what Caddy serves instead when it served this one (ADR-0404): caddy_local_ca when it was
    the chosen one, whose served file goes, else the chosen certificate's (a reload that Docker did
    not answer in time, which Caddy then loaded, can leave Caddy serving a certificate that the
    served file does not hold). When Caddy does not take that, CaddyRefused or CaddyUnreachable, and
    nothing is deleted.

    Holds _CERTIFICATES_LOCK, so that a store of the same name in between leaves no key without its
    certificate: blocking, call it in a worker thread.
    """
    cert_file, key_file, chain_file = _certificate_files(name)
    instead = None
    with _CERTIFICATES_LOCK:
        if not cert_file.exists():
            return False, None
        previous = serving.read_served()
        if _is_chosen(name):
            serving.remove_served()
            _caddy_takes("local", previous, file_changed=True)
            instead = LOCAL_CA_ID
        elif (stored := _stored_fingerprint(name)) is not None and serving.probe_fingerprint() == stored:
            _caddy_takes("local" if previous is None else "served", previous, file_changed=False)
            instead = _chosen_id() or LOCAL_CA_ID
        cert_file.unlink(missing_ok=True)
        key_file.unlink(missing_ok=True)
        chain_file.unlink(missing_ok=True)
    return True, instead


def _chosen_id() -> str | None:
    """The id of the stored certificate that the served file holds."""
    chosen, stored = _served_and_stored_fingerprints()
    return next((cert_id for cert_id, found in stored.items() if found == chosen), None) if chosen else None


def _openssl_error(error: Exception) -> str:
    """What an answer says of ``error``: of an openssl that did not finish, how long it had."""
    if isinstance(error, subprocess.TimeoutExpired):
        return f"openssl did not finish within {error.timeout:g} seconds"
    return str(error)


def _pem_block(text: str, label: str) -> str | None:
    """The first PEM block of type ``label`` ("CERTIFICATE", "PRIVATE KEY") in ``text``, or None."""
    match = re.search(rf"-----BEGIN {label}-----\n.*?\n-----END {label}-----\n", text, re.DOTALL)
    return match.group(0) if match else None


def _subject_value(value: str) -> str:
    """Escape a value for openssl's -subj ("/type=value/..."), where \\, / and + are special."""
    return re.sub(r"([\\/+])", r"\\\1", value)


def get_certificate_info(cert_path: str) -> dict | None:
    """Parse the certificate file ``cert_path`` and extract information using openssl."""
    return _certificate_info(["-in", cert_path])


def get_pem_certificate_info(pem: str) -> dict | None:
    """Like get_certificate_info, for a certificate given as PEM text.

    openssl reads it on its standard input, so nothing is written to disk and
    nothing can be left behind. None also when the text cannot be encoded (a
    lone surrogate, which JSON can carry as an escape).
    """
    try:
        pem.encode()
    except UnicodeEncodeError:
        return None
    return _certificate_info([], pem)


def _certificate_info(source: list[str], pem: str | None = None) -> dict | None:
    """Parse a certificate and extract information using openssl x509.

    openssl reads the file named in ``source`` (["-in", path]) or, with
    ``source`` empty, the PEM text ``pem`` on its standard input. It blocks:
    call this in a worker thread. None when openssl does not read a
    certificate there; an openssl that cannot run (OSError) or takes longer
    than OPENSSL_READ_TIMEOUT seconds (subprocess.TimeoutExpired) raises, as
    that says nothing about the certificate. openssl runs in the container, where /data/certs is,
    through host.run_in_container (ADR-0416).
    """
    try:
        # Get subject, issuer, dates
        result = host.run_in_container(
            ["openssl", "x509", *source, "-noout",
             "-subject", "-issuer", "-dates", "-fingerprint", "-sha256"],
            input=pem,
            timeout=OPENSSL_READ_TIMEOUT,
        )

        if result.returncode != 0:
            return None

        info = {
            "domain": "",
            "issuer": "",
            "valid_from": None,
            "valid_until": None,
            "fingerprint": "",
            "is_valid": False,
            # The whole subject and issuer as openssl prints them: a certificate is
            # self-signed when they are the same name.
            "subject_name": "",
            "issuer_name": "",
        }

        for line in result.stdout.split("\n"):
            if line.startswith("subject="):
                info["subject_name"] = line.split("=", 1)[1].strip()
                # Extract CN from subject
                cn_match = re.search(r'CN\s*=\s*([^,/]+)', line)
                if cn_match:
                    info["domain"] = cn_match.group(1).strip()
            elif line.startswith("issuer="):
                info["issuer_name"] = line.split("=", 1)[1].strip()
                # Extract issuer organization or CN
                org_match = re.search(r'O\s*=\s*([^,/]+)', line)
                cn_match = re.search(r'CN\s*=\s*([^,/]+)', line)
                info["issuer"] = (org_match or cn_match).group(1).strip() if (org_match or cn_match) else "Unknown"
            elif line.startswith("notBefore="):
                info["valid_from"] = openssl_time(line.split("=", 1)[1])
            elif line.startswith("notAfter="):
                info["valid_until"] = openssl_time(line.split("=", 1)[1])
            elif "Fingerprint" in line:
                info["fingerprint"] = line.split("=", 1)[1].strip()

        # Check if certificate is currently valid
        verify_result = host.run_in_container(
            ["openssl", "x509", *source, "-noout", "-checkend", "0"],
            input=pem,
            timeout=OPENSSL_READ_TIMEOUT,
        )
        info["is_valid"] = verify_result.returncode == 0

        return info
    except (OSError, subprocess.SubprocessError):
        raise
    except Exception:
        return None


def openssl_time(text: str) -> datetime | None:
    """A date as openssl x509 prints it, "Nov  8 01:47:31 2027 GMT", in UTC (ADR-0401); None for
    another form."""
    try:
        return datetime.strptime(" ".join(text.split()), "%b %d %H:%M:%S %Y GMT").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def get_custom_certificates() -> list[Certificate]:
    """The stored certificates (uploaded or self-signed), without what is served of them, which the
    list's status adds (ADR-0404).

    Runs openssl for each: call this in a worker thread.
    """
    certs = []
    now = datetime.now(timezone.utc)
    certs_path = Path(CERTS_DIR)

    if not certs_path.exists():
        return certs

    for cert_file in certs_path.glob("*.crt"):
        # Only what an upload or a self-signed certificate stored: the names
        # that the delete takes.
        if _uploaded_certificate_name(cert_file.stem) != cert_file.stem:
            continue
        info = get_certificate_info(str(cert_file))
        if info:
            # Self-signed: the issuer is the subject. Comparing the issuer's O or CN with the
            # subject's CN took one made with an organization for a custom certificate.
            is_self_signed = bool(info.get("subject_name")) and info.get("issuer_name") == info.get("subject_name")
            valid_until = info.get("valid_until")
            certs.append(Certificate(
                id=f"custom_{cert_file.stem}",
                type=CertificateType.SELF_SIGNED if is_self_signed else CertificateType.CUSTOM,
                domain=info.get("domain") or cert_file.stem,
                issuer=info.get("issuer"),
                valid_from=info.get("valid_from"),
                valid_until=valid_until,
                is_valid=info.get("is_valid", False),
                expires_soon=bool(info.get("is_valid") and valid_until
                                  and valid_until - now <= timedelta(days=serving.EXPIRY_WARNING_DAYS)),
                fingerprint=info.get("fingerprint")
            ))

    return certs


@router.get("/certificates", response_model=CertificateListResponse)
async def list_certificates(_: User = Depends(require_admin)):
    """The stored certificates, which of them browsers get and which was chosen (ADR-0404)."""
    try:
        certificates = await asyncio.to_thread(get_custom_certificates)
    except (OSError, subprocess.SubprocessError) as e:
        raise HTTPException(500, f"Failed to read the stored certificates: {_openssl_error(e)}")
    status = await _certificate_status()
    for certificate in certificates:
        certificate.served = certificate.id == status.served_id
        certificate.chosen = certificate.id == status.chosen_id
    return CertificateListResponse(certificates=certificates, status=status)


@router.get("/certificates/status", response_model=CertificateStatus)
async def get_certificate_status(_: User = Depends(require_admin)):
    """What browsers get from https://<host>/, as a TLS handshake with the web UI's Caddy shows
    it, and the stored certificate an administrator chose (ADR-0404)."""
    return await _certificate_status()


def _serve_refusal(what: str, error: Exception) -> HTTPException:
    """The answer to a change of the served certificate that Caddy did not take or could not get."""
    if isinstance(error, serving.CaddyRefused):
        return HTTPException(502, f"Caddy did not load {what} and serves what it served before: {error}")
    return HTTPException(503, f"The web UI's Caddy cannot be reached: {error}")


@router.post("/certificates/{cert_id}/serve", response_model=ServeCertificateResponse)
async def serve_certificate(cert_id: str, request: Request, current_user: User = Depends(require_admin)):
    """Make the stored certificate ``cert_id`` the one that the web UI's Caddy serves, or, with
    caddy_local_ca, the certificate of Caddy's local CA again (ADR-0404). Admin only.

    A stored certificate is checked first (src/security/serving.py's prepare): one that fails the
    checks is refused with 409 and reaches no Caddy. A change that Caddy does not take answers 502,
    one that cannot reach it 503; Caddy then serves what it served, and the served file holds it.
    Writes the served file and the audit log only on a real change.
    """
    if cert_id == LOCAL_CA_ID:
        name = None
    else:
        name = cert_id.removeprefix("custom_")
        if not cert_id.startswith("custom_") or _uploaded_certificate_name(name) != name:
            raise HTTPException(400, "Invalid certificate id")
    prepared = None
    try:
        if name is None:
            changed = await asyncio.to_thread(_serve_local_ca)
        else:
            changed, prepared = await asyncio.to_thread(_serve_stored, name)
    except FileNotFoundError:
        raise HTTPException(404, "Certificate not found") from None
    except serving.CertificateRefused as e:
        raise HTTPException(409, f"{name} cannot be served: {e}") from None
    except (serving.CaddyRefused, serving.CaddyUnreachable) as e:
        what = name if name else "its local CA's certificate"
        logger.warning("Caddy did not take %s: %s", name or "its local CA", e)
        raise _serve_refusal(what, e) from None
    except Exception as e:
        logger.exception("Could not change the served certificate")
        raise HTTPException(500, f"Failed to change the served certificate: {e}") from None
    if changed:
        if name is None:
            details = "Browsers get the certificate of Caddy's local CA"
        else:
            details = f"Browsers get the stored certificate {name}, until {prepared.not_after:%Y-%m-%d}"
            if prepared.expires_soon:
                details += f"; it expires within {serving.EXPIRY_WARNING_DAYS} days"
        await audit_change(AuditAction.SSL_CERT_SERVED, request, current_user.username,
                           resource_type="certificate", resource_id=cert_id, details=details)
    return ServeCertificateResponse(changed=changed, expires_soon=bool(prepared and prepared.expires_soon),
                                    status=await _certificate_status())


@router.post("/certificates/self-signed")
async def generate_self_signed_certificate(
    data: SelfSignedCertRequest,
    request: Request,
    current_user: User = Depends(require_admin)
):
    """Generate a self-signed certificate. Admin only. 409 when it would replace the served one."""
    # The schema only accepts a host name or an IP address short enough for a
    # subject CN (and so for the file names), so the domain can name files in
    # CERTS_DIR and go into -subj and -addext as it is; and an organization and
    # a country that X.509 allows.
    domain = data.domain
    san = f"IP:{domain}" if parse_ip_address(domain) is not None else f"DNS:{domain}"

    try:
        # Build subject string
        subject = f"/CN={domain}"
        if data.organization:
            subject += f"/O={_subject_value(data.organization)}"
        if data.country:
            subject += f"/C={_subject_value(data.country)}"

        # Generate self-signed certificate. openssl prints the new key and then
        # the certificate, which are stored like uploaded ones once openssl has
        # succeeded: one writer of the files, which follows no symbolic link
        # (ADR-0042) and makes keys 0600, and an openssl that fails leaves the
        # stored files as they were. -utf8: openssl counts the subject's
        # characters as the schema does, not the bytes of their UTF-8.
        result = await asyncio.to_thread(
            host.run_in_container,
            [
                "openssl", "req", "-x509", "-nodes", "-utf8",
                "-days", str(data.validity_days),
                "-newkey", "rsa:2048",
                "-keyout", "-",
                "-subj", subject,
                "-addext", f"subjectAltName={san}",
            ],
            timeout=OPENSSL_GENERATE_TIMEOUT,
        )

        if result.returncode != 0:
            raise HTTPException(500, f"Failed to generate certificate: {result.stderr}")
        key = _pem_block(result.stdout, "PRIVATE KEY")
        certificate = _pem_block(result.stdout, "CERTIFICATE")
        if key is None or certificate is None:
            raise HTTPException(500, "Failed to generate certificate: openssl printed no key or no certificate")
        await asyncio.to_thread(_store_certificate, domain, certificate, key)
    except HTTPException:
        raise
    except CertificateServed as e:
        raise HTTPException(409, str(e)) from None
    except Exception as e:
        logger.exception("Could not generate self signed certificate")
        raise HTTPException(500, f"Failed to generate certificate: {_openssl_error(e)}")
    await audit_change(AuditAction.SSL_CERT_GENERATED, request, current_user.username,
                       resource_type="certificate", resource_id=f"custom_{domain}",
                       details=f"A self-signed certificate for {domain}, valid for {data.validity_days} days")
    return {
        "status": "ok",
        "message": "Self-signed certificate generated",
        "certificate_id": f"custom_{domain}"
    }


@router.post("/certificates/upload")
async def upload_certificate(
    data: CertificateUpload,
    request: Request,
    current_user: User = Depends(require_admin)
):
    """Upload a custom SSL/TLS certificate. Admin only. 409 when it would replace the served one."""
    try:
        # Validate and parse the certificate to get domain. openssl reads it
        # on its standard input: no temporary file to leave in /tmp on errors.
        info = await asyncio.to_thread(get_pem_certificate_info, data.certificate)

        if not info:
            raise HTTPException(400, "Invalid certificate format")

        # The files are named after the subject CN, so it must be a name.
        domain = _uploaded_certificate_name(info.get("domain") or "")
        if domain is None:
            raise HTTPException(
                400,
                "The certificate's subject CN must be a host name, a wildcard host name "
                "(*.example.com) or an IP address, at most "
                f"{MAX_CERTIFICATE_NAME_LENGTH} characters long",
            )
        # Text that cannot be encoded (a lone surrogate) is a bad request: the
        # write below would fail on it, and then leave the stored files as
        # they were.
        for text, what in ((data.private_key, "private key"),
                           (data.chain or "", "certificate chain")):
            try:
                text.encode()
            except UnicodeEncodeError:
                raise HTTPException(400, f"Invalid {what} format") from None

        await asyncio.to_thread(_store_certificate, domain, data.certificate, data.private_key,
                                data.chain)
    except HTTPException:
        raise
    except CertificateServed as e:
        raise HTTPException(409, str(e)) from None
    except Exception as e:
        logger.exception("Could not upload certificate")
        raise HTTPException(500, f"Failed to upload certificate: {_openssl_error(e)}")
    await audit_change(AuditAction.SSL_CERT_UPLOADED, request, current_user.username,
                       resource_type="certificate", resource_id=f"custom_{domain}",
                       details=f"A certificate for {domain}" + (" with its chain" if data.chain else ""))
    return {
        "status": "ok",
        "message": "Certificate uploaded successfully",
        "certificate_id": f"custom_{domain}",
        "domain": domain
    }


@router.delete("/certificates/{cert_id}")
async def delete_certificate(
    cert_id: str,
    request: Request,
    current_user: User = Depends(require_admin)
):
    """Delete a custom certificate. Admin only. The served one is replaced by Caddy's local CA
    first (ADR-0404): when Caddy does not take it, 502 or 503, and nothing is deleted."""
    if not cert_id.startswith("custom_"):
        raise HTTPException(400, "Can only delete custom certificates")

    # Certificates are stored under the names that an upload and a self-signed
    # certificate take, which also keeps the files in CERTS_DIR.
    name = cert_id.removeprefix("custom_")
    if _uploaded_certificate_name(name) != name:
        raise HTTPException(400, "Invalid certificate id")

    try:
        deleted, instead = await asyncio.to_thread(_delete_certificate_files, name)
    except (serving.CaddyRefused, serving.CaddyUnreachable) as e:
        logger.warning("Caddy did not take its local CA for the delete of %s: %s", name, e)
        raise _serve_refusal("its local CA's certificate", e) from None
    except Exception as e:
        logger.exception("Could not delete certificate")
        raise HTTPException(500, f"Failed to delete certificate: {str(e)}")
    if not deleted:
        raise HTTPException(404, "Certificate not found")
    if instead:
        what = "the certificate of Caddy's local CA" if instead == LOCAL_CA_ID else \
            f"the chosen stored certificate {instead.removeprefix('custom_')}"
        await audit_change(AuditAction.SSL_CERT_SERVED, request, current_user.username,
                           resource_type="certificate", resource_id=instead,
                           details=f"Browsers get {what}: the served {name} was deleted")
    await audit_change(AuditAction.SSL_CERT_DELETED, request, current_user.username,
                       resource_type="certificate", resource_id=cert_id)
    return {"status": "ok", "message": "Certificate deleted"}


# ==================== Two-factor authentication for every account (ADR-0386) ====================

REQUIREMENT_SESSION_NEEDED = "An API token cannot change the two-factor requirement: sign in to do it"
OWN_TWO_FACTOR_NEEDED = "Turn on two-factor authentication for your own account first"
ALREADY_REQUIRED = "Two-factor authentication is already required for every account"
NOT_REQUIRED = "Two-factor authentication is not required"
CANNOT_STORE = "The two-factor requirement cannot be stored: its table in the database has another layout"


def _requirement_status(current: requirement.Requirement | None) -> TwoFactorRequirementStatus:
    if current is None:
        return TwoFactorRequirementStatus(enabled=False)
    return TwoFactorRequirementStatus(
        enabled=True, grace_days=current.grace_days, enabled_at=current.enabled_at, enabled_by=current.enabled_by
    )


def _require_own_second_factor(found: Credential, user: User) -> None:
    """What turning the requirement on or off takes of the administrator before anything else: a
    session, not an API token, and two-factor authentication of their own."""
    if found.token.startswith(API_TOKEN_PREFIX):
        raise HTTPException(status_code=403, detail=REQUIREMENT_SESSION_NEEDED)
    if not user.totp_enabled:
        raise HTTPException(status_code=409, detail=OWN_TWO_FACTOR_NEEDED)


def _use_own_code(user: User, code: str) -> None:
    """A current code of the administrator's own authenticator, used up as every code is (ADR-0067).
    Checked last, so that a request refused for anything else uses no code."""
    check = check_totp_code(user.id, user.totp_secret, code)
    if check is not TotpCheck.OK:
        raise HTTPException(
            status_code=400, detail=TOTP_CODE_REUSED if check is TotpCheck.REUSED else "Invalid TOTP code"
        )


@router.get("/two-factor-requirement", response_model=TwoFactorRequirementStatus)
async def get_two_factor_requirement(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Whether every account must have two-factor authentication, and with which grace period."""
    return _requirement_status(await requirement.current(db))


@router.post("/two-factor-requirement/enable", response_model=TwoFactorRequirementStatus)
async def enable_two_factor_requirement(
    request: Request,
    data: TwoFactorRequirementEnable,
    found: Credential = Depends(credential),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Require two-factor authentication for every account, after ``grace_days``. Only an
    administrator who has it turns it on, with a current code: the one who imposes it meets it."""
    _require_own_second_factor(found, current_user)
    if await requirement.current(db) is not None:
        raise HTTPException(status_code=409, detail=ALREADY_REQUIRED)
    if not await requirement.storable(db):  # a restored table of another layout; no code is used
        raise HTTPException(status_code=500, detail=CANNOT_STORE)
    _use_own_code(current_user, data.code)
    user_id, username = current_user.id, current_user.username
    enabled_at = requirement._now()
    db.add(TwoFactorRequirement(
        id=requirement.REQUIREMENT_ID, grace_days=data.grace_days, enabled_at=enabled_at, enabled_by=username
    ))
    try:
        # The flush takes SQLite's write lock; the administrator's own two-factor authentication is
        # then read again, in this transaction: their own turning it off, committed meanwhile, leaves
        # the requirement off.
        await db.flush()
        if not await db.scalar(select(User.totp_enabled).where(User.id == user_id)):
            await db.rollback()
            raise HTTPException(status_code=409, detail=OWN_TWO_FACTOR_NEEDED)
        await db.commit()
    except IntegrityError:  # another administrator turned it on meanwhile
        await db.rollback()
        raise HTTPException(status_code=409, detail=ALREADY_REQUIRED) from None
    days = data.grace_days
    grace = "without a grace period" if days == 0 else f"with a grace period of {days} day{'' if days == 1 else 's'}"
    await audit_change(
        AuditAction.SETTINGS_CHANGED, request, username, resource_type="two_factor_requirement",
        details=f"Two-factor authentication required for every account, {grace}",
    )
    return _requirement_status(requirement.Requirement(data.grace_days, enabled_at, username))


@router.post("/two-factor-requirement/disable", response_model=TwoFactorRequirementStatus)
async def disable_two_factor_requirement(
    request: Request,
    data: TwoFactorRequirementDisable,
    found: Credential = Depends(credential),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Stop requiring two-factor authentication: accounts without it have their rights again at
    their next request. It weakens every sign-in, so it takes what turning it on takes."""
    _require_own_second_factor(found, current_user)
    if await requirement.current(db) is None:
        raise HTTPException(status_code=409, detail=NOT_REQUIRED)
    _use_own_code(current_user, data.code)
    username = current_user.username
    # Every row, whatever the table's layout (src/auth/requirement.py).
    result = await db.execute(text(requirement.DELETE_ALL))
    await db.commit()
    if result.rowcount < 1:  # another administrator turned it off meanwhile
        raise HTTPException(status_code=409, detail=NOT_REQUIRED)
    await audit_change(
        AuditAction.SETTINGS_CHANGED, request, username, resource_type="two_factor_requirement",
        details="Two-factor authentication no longer required for every account", severity=AuditSeverity.WARNING,
    )
    return _requirement_status(None)


# ==================== Helper for login tracking ====================

async def record_login_attempt(
    db: AsyncSession,
    username: str,
    success: bool,
    request: Request | None = None,
    failure_reason: str | None = None
):
    """Record a login attempt in the database.

    An attempt that cannot be stored, such as on a full /data, is reported in the log and the
    sign-in goes on (ADR-0251). The rollback then expires the rows the caller loaded with
    ``db``: read what the sign-in needs of them before.
    """
    ip_address = None
    user_agent = None

    if request:
        # Real client address: uvicorn already resolved the proxy's
        # X-Forwarded-For into request.client (see auth.utils.client_ip).
        ip_address = client_ip(request)
        user_agent = request.headers.get("User-Agent", "")[:500]  # Limit length

    fields = {
        # Failed logins are recorded before anyone is authenticated: keep the name short.
        "username": username[:LOGIN_ATTEMPT_USERNAME_CHARS] if isinstance(username, str) else username,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "success": success,
        "failure_reason": failure_reason,
    }
    # The row takes its time from the database (server_default); the log line has to carry it.
    attempted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.add(LoginAttempt(**fields))
    try:
        await db.commit()
    except OperationalError as exc:
        await db.rollback()
        logger.error(
            "Could not store this login attempt in the login history: %s (%s)",
            json.dumps({"timestamp": attempted_at, **fields}), exc.orig or exc,
        )
