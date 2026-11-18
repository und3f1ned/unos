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
from pydantic import TypeAdapter
from sqlalchemy import select, func, desc, text

from .. import state_files
from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..database import get_db
from ..times import db_now, utc_now
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
    FirewallRule, FirewallStatus, FirewallRuleCreate, FirewallAction,
    FirewallDirection, FirewallProtocol,
    Certificate, CertificateListResponse, CertificateType, CertificateStatus,
    CertificateUpload, SelfSignedCertRequest,
    MAX_CERTIFICATE_NAME_LENGTH, is_dns_hostname, parse_ip_address, validate_certificate_domain,
)

router = APIRouter(prefix="/security", tags=["security"])
logger = logging.getLogger(__name__)

FIREWALL_RULES_FILE = "/data/firewall_rules.json"
# Uploaded and self-signed certificates. Stored, not served: Caddy serves the certificate of its
# local CA, in the web UI's container, which has no access to this directory (ADR-0357).
CERTS_DIR = "/data/certs"
# Held by every store and delete of the stored certificates, which run in worker threads: two of
# them never mix their files (ADR-0357).
_CERTIFICATES_LOCK = threading.Lock()
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
    # Up to 100 years back: a negative period would put the cutoff in the future and delete
    # every attempt, and a much longer one reaches before the year 1.
    older_than_days: int = Query(30, ge=0, le=MAX_HISTORY_DAYS),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Clear login history older than specified days. Admin only."""
    # The stored times are UTC without a zone, as SQLite's CURRENT_TIMESTAMP gives them (ADR-0262).
    cutoff = db_now() - timedelta(days=older_than_days)

    from sqlalchemy import delete
    stmt = delete(LoginAttempt).where(LoginAttempt.timestamp < cutoff)
    result = await db.execute(stmt)
    await db.commit()

    return {"status": "ok", "deleted_count": result.rowcount}


# ==================== Firewall Management ====================
# Firewall and certificate changes write no audit entry, and AuditAction has no action for them
# (ADR-0359).

_FIREWALL_RULES = TypeAdapter(list[FirewallRule])


def load_firewall_rules() -> list[FirewallRule]:
    """The firewall rules; none without a file, or for one kept as .bad (ADR-0324)."""
    return state_files.load_json(
        Path(FIREWALL_RULES_FILE), _FIREWALL_RULES.validate_python, list, what="the firewall rules"
    )


def save_firewall_rules(rules: list[FirewallRule]) -> None:
    """Replace the firewall rules' file (ADR-0324)."""
    state_files.write_json(Path(FIREWALL_RULES_FILE), [r.model_dump() for r in rules], sync=False)


def get_ufw_status() -> tuple[bool, str, str]:
    """Get UFW firewall status."""
    try:
        result = subprocess.run(
            ["ufw", "status", "verbose"],
            capture_output=True,
            text=True
        )
        output = result.stdout

        enabled = "Status: active" in output

        # Parse default policies
        default_in = "deny"
        default_out = "allow"

        for line in output.split("\n"):
            if "Default:" in line:
                # Parse format: "Default: deny (incoming), allow (outgoing), disabled (routed)"
                import re
                # Match incoming policy
                incoming_match = re.search(r'(\w+)\s*\(incoming\)', line.lower())
                if incoming_match:
                    policy = incoming_match.group(1)
                    if policy in ('allow', 'deny', 'reject'):
                        default_in = policy
                # Match outgoing policy
                outgoing_match = re.search(r'(\w+)\s*\(outgoing\)', line.lower())
                if outgoing_match:
                    policy = outgoing_match.group(1)
                    if policy in ('allow', 'deny', 'reject'):
                        default_out = policy

        return enabled, default_in, default_out
    except FileNotFoundError:
        return False, "deny", "allow"
    except Exception:
        return False, "deny", "allow"


def apply_ufw_rule(rule: FirewallRule, delete: bool = False) -> tuple[bool, str]:
    """Apply a single UFW rule."""
    try:
        cmd = ["ufw"]

        if delete:
            cmd.append("delete")

        # Action
        cmd.append(rule.action.value)

        # Direction
        if rule.direction == FirewallDirection.IN:
            cmd.append("in")
        else:
            cmd.append("out")

        # Protocol and port
        if rule.port:
            if rule.protocol != FirewallProtocol.ANY:
                cmd.extend(["proto", rule.protocol.value])
            cmd.extend(["to", "any", "port", rule.port])
        elif rule.protocol != FirewallProtocol.ANY:
            cmd.extend(["proto", rule.protocol.value])

        # Source
        if rule.source:
            cmd.extend(["from", rule.source])

        # Comment
        if rule.comment and not delete:
            cmd.extend(["comment", rule.comment])

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            return False, result.stderr or result.stdout

        return True, ""
    except Exception as e:
        return False, str(e)


@router.get("/firewall", response_model=FirewallStatus)
async def get_firewall_status(_: User = Depends(require_admin)):
    """Get current firewall status and rules."""
    enabled, default_in, default_out = get_ufw_status()
    rules = load_firewall_rules()

    return FirewallStatus(
        enabled=enabled,
        default_incoming=default_in,
        default_outgoing=default_out,
        rules=rules
    )


@router.post("/firewall/enable")
async def enable_firewall(current_user: User = Depends(require_admin)):
    """Enable the firewall. Admin only."""
    try:
        # Enable UFW with default deny incoming
        result = subprocess.run(
            ["ufw", "--force", "enable"],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            raise HTTPException(500, f"Failed to enable firewall: {result.stderr}")

        return {"status": "ok", "message": "Firewall enabled"}
    except FileNotFoundError:
        raise HTTPException(500, "UFW is not installed on this system")
    except Exception as e:
        logger.exception("Could not enable firewall")
        raise HTTPException(500, f"Failed to enable firewall: {str(e)}")


@router.post("/firewall/disable")
async def disable_firewall(current_user: User = Depends(require_admin)):
    """Disable the firewall. Admin only."""
    try:
        result = subprocess.run(
            ["ufw", "--force", "disable"],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            raise HTTPException(500, f"Failed to disable firewall: {result.stderr}")

        return {"status": "ok", "message": "Firewall disabled"}
    except FileNotFoundError:
        raise HTTPException(500, "UFW is not installed on this system")
    except Exception as e:
        logger.exception("Could not disable firewall")
        raise HTTPException(500, f"Failed to disable firewall: {str(e)}")


@router.post("/firewall/rules", response_model=FirewallRule)
async def create_firewall_rule(
    rule: FirewallRuleCreate,
    current_user: User = Depends(require_admin)
):
    """Create a new firewall rule. Admin only."""
    rules = load_firewall_rules()

    # Generate ID
    new_rule = FirewallRule(
        id=f"rule_{utc_now().strftime('%Y%m%d%H%M%S')}",
        **rule.model_dump()
    )

    # Apply the rule via UFW
    success, error = apply_ufw_rule(new_rule)
    if not success:
        raise HTTPException(500, f"Failed to apply rule: {error}")

    rules.append(new_rule)
    save_firewall_rules(rules)

    return new_rule


@router.delete("/firewall/rules/{rule_id}")
async def delete_firewall_rule(
    rule_id: str,
    current_user: User = Depends(require_admin)
):
    """Delete a firewall rule. Admin only."""
    rules = load_firewall_rules()
    rule_to_delete = next((r for r in rules if r.id == rule_id), None)

    if not rule_to_delete:
        raise HTTPException(404, f"Rule not found: {rule_id}")

    # Remove the rule via UFW
    success, error = apply_ufw_rule(rule_to_delete, delete=True)
    # Don't fail if rule doesn't exist in UFW
    if not success and "Could not delete non-existent rule" not in error:
        raise HTTPException(500, f"Failed to delete rule: {error}")

    rules = [r for r in rules if r.id != rule_id]
    save_firewall_rules(rules)

    return {"status": "ok", "message": "Rule deleted"}


@router.post("/firewall/reset")
async def reset_firewall(current_user: User = Depends(require_admin)):
    """Reset firewall to default rules. Admin only."""
    try:
        # Reset UFW
        subprocess.run(["ufw", "--force", "reset"], capture_output=True, text=True)

        # Set default policies
        subprocess.run(["ufw", "default", "deny", "incoming"], capture_output=True, text=True)
        subprocess.run(["ufw", "default", "allow", "outgoing"], capture_output=True, text=True)

        # Allow SSH and HTTP/HTTPS by default
        subprocess.run(["ufw", "allow", "22/tcp", "comment", "SSH"], capture_output=True, text=True)
        subprocess.run(["ufw", "allow", "80/tcp", "comment", "HTTP"], capture_output=True, text=True)
        subprocess.run(["ufw", "allow", "443/tcp", "comment", "HTTPS"], capture_output=True, text=True)

        # Clear saved rules
        save_firewall_rules([
            FirewallRule(
                id="rule_default_ssh",
                name="SSH",
                action=FirewallAction.ALLOW,
                direction=FirewallDirection.IN,
                protocol=FirewallProtocol.TCP,
                port="22",
                enabled=True,
                comment="SSH access"
            ),
            FirewallRule(
                id="rule_default_http",
                name="HTTP",
                action=FirewallAction.ALLOW,
                direction=FirewallDirection.IN,
                protocol=FirewallProtocol.TCP,
                port="80",
                enabled=True,
                comment="HTTP access"
            ),
            FirewallRule(
                id="rule_default_https",
                name="HTTPS",
                action=FirewallAction.ALLOW,
                direction=FirewallDirection.IN,
                protocol=FirewallProtocol.TCP,
                port="443",
                enabled=True,
                comment="HTTPS access"
            )
        ])

        return {"status": "ok", "message": "Firewall reset to defaults"}
    except Exception as e:
        logger.exception("Could not reset firewall")
        raise HTTPException(500, f"Failed to reset firewall: {str(e)}")


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
    target (ADR-0042). Holds _CERTIFICATES_LOCK: blocking, call it in a worker thread (ADR-0357).
    """
    cert_file, key_file, chain_file = _certificate_files(name)
    files = [
        (cert_file, lambda path: _write_certificate_file(path, certificate, 0o644)),
        (key_file, lambda path: _write_private_key(path, key)),
    ]
    if chain:
        files.append((chain_file, lambda path: _write_certificate_file(path, chain, 0o644)))
    with _CERTIFICATES_LOCK:
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


def _delete_certificate_files(name: str) -> bool:
    """Remove the stored files of the certificate ``name``; False when none is stored under it.

    Holds _CERTIFICATES_LOCK, so that a store of the same name in between leaves no key without its
    certificate: blocking, call it in a worker thread (ADR-0357).
    """
    cert_file, key_file, chain_file = _certificate_files(name)
    with _CERTIFICATES_LOCK:
        if not cert_file.exists():
            return False
        cert_file.unlink(missing_ok=True)
        key_file.unlink(missing_ok=True)
        chain_file.unlink(missing_ok=True)
    return True


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
    that says nothing about the certificate.
    """
    try:
        # Get subject, issuer, dates
        result = subprocess.run(
            ["openssl", "x509", *source, "-noout",
             "-subject", "-issuer", "-dates", "-fingerprint"],
            input=pem,
            capture_output=True,
            text=True,
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
        verify_result = subprocess.run(
            ["openssl", "x509", *source, "-noout", "-checkend", "0"],
            input=pem,
            capture_output=True,
            text=True,
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
    """The stored certificates (uploaded or self-signed), none of them served (ADR-0357).

    Runs openssl for each: call this in a worker thread.
    """
    certs = []
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
            certs.append(Certificate(
                id=f"custom_{cert_file.stem}",
                type=CertificateType.SELF_SIGNED if is_self_signed else CertificateType.CUSTOM,
                domain=info.get("domain") or cert_file.stem,
                issuer=info.get("issuer"),
                valid_from=info.get("valid_from"),
                valid_until=info.get("valid_until"),
                is_valid=info.get("is_valid", False),
                fingerprint=info.get("fingerprint")
            ))

    return certs


@router.get("/certificates", response_model=CertificateListResponse)
async def list_certificates(_: User = Depends(require_admin)):
    """The stored certificates, and what browsers get instead of them (ADR-0357)."""
    try:
        certificates = await asyncio.to_thread(get_custom_certificates)
    except (OSError, subprocess.SubprocessError) as e:
        raise HTTPException(500, f"Failed to read the stored certificates: {_openssl_error(e)}")
    return CertificateListResponse(certificates=certificates)


@router.get("/certificates/status", response_model=CertificateStatus)
async def get_certificate_status(_: User = Depends(require_admin)):
    """What browsers get from https://<host>/: the certificate of Caddy's local CA (ADR-0025).

    Caddy runs in the web UI's container, the only one with its data (deploy/docker-compose.yml),
    and nothing hands it a stored certificate (ADR-0357).
    """
    return CertificateStatus()


@router.post("/certificates/self-signed")
async def generate_self_signed_certificate(
    request: SelfSignedCertRequest,
    current_user: User = Depends(require_admin)
):
    """Generate a self-signed certificate. Admin only."""
    # The schema only accepts a host name or an IP address short enough for a
    # subject CN (and so for the file names), so the domain can name files in
    # CERTS_DIR and go into -subj and -addext as it is; and an organization and
    # a country that X.509 allows.
    domain = request.domain
    san = f"IP:{domain}" if parse_ip_address(domain) is not None else f"DNS:{domain}"

    try:
        # Build subject string
        subject = f"/CN={domain}"
        if request.organization:
            subject += f"/O={_subject_value(request.organization)}"
        if request.country:
            subject += f"/C={_subject_value(request.country)}"

        # Generate self-signed certificate. openssl prints the new key and then
        # the certificate, which are stored like uploaded ones once openssl has
        # succeeded: one writer of the files, which follows no symbolic link
        # (ADR-0042) and makes keys 0600, and an openssl that fails leaves the
        # stored files as they were. -utf8: openssl counts the subject's
        # characters as the schema does, not the bytes of their UTF-8.
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "openssl", "req", "-x509", "-nodes", "-utf8",
                "-days", str(request.validity_days),
                "-newkey", "rsa:2048",
                "-keyout", "-",
                "-subj", subject,
                "-addext", f"subjectAltName={san}",
            ],
            capture_output=True, text=True, timeout=OPENSSL_GENERATE_TIMEOUT,
        )

        if result.returncode != 0:
            raise HTTPException(500, f"Failed to generate certificate: {result.stderr}")
        key = _pem_block(result.stdout, "PRIVATE KEY")
        certificate = _pem_block(result.stdout, "CERTIFICATE")
        if key is None or certificate is None:
            raise HTTPException(500, "Failed to generate certificate: openssl printed no key or no certificate")
        await asyncio.to_thread(_store_certificate, domain, certificate, key)

        return {
            "status": "ok",
            "message": "Self-signed certificate generated",
            "certificate_id": f"custom_{domain}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Could not generate self signed certificate")
        raise HTTPException(500, f"Failed to generate certificate: {_openssl_error(e)}")


@router.post("/certificates/upload")
async def upload_certificate(
    data: CertificateUpload,
    current_user: User = Depends(require_admin)
):
    """Upload a custom SSL/TLS certificate. Admin only."""
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

        return {
            "status": "ok",
            "message": "Certificate uploaded successfully",
            "certificate_id": f"custom_{domain}",
            "domain": domain
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Could not upload certificate")
        raise HTTPException(500, f"Failed to upload certificate: {_openssl_error(e)}")


@router.delete("/certificates/{cert_id}")
async def delete_certificate(
    cert_id: str,
    current_user: User = Depends(require_admin)
):
    """Delete a custom certificate. Admin only."""
    if not cert_id.startswith("custom_"):
        raise HTTPException(400, "Can only delete custom certificates")

    # Certificates are stored under the names that an upload and a self-signed
    # certificate take, which also keeps the files in CERTS_DIR.
    name = cert_id.removeprefix("custom_")
    if _uploaded_certificate_name(name) != name:
        raise HTTPException(400, "Invalid certificate id")

    try:
        deleted = await asyncio.to_thread(_delete_certificate_files, name)
    except Exception as e:
        logger.exception("Could not delete certificate")
        raise HTTPException(500, f"Failed to delete certificate: {str(e)}")
    if not deleted:
        raise HTTPException(404, "Certificate not found")
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
