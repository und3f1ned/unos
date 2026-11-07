import os
import subprocess
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from ..database import get_db
from ..auth.utils import client_ip, require_admin, verify_token
from ..auth.models import User
from .models import LoginAttempt
from .schemas import (
    LoginAttemptResponse, LoginHistoryResponse,
    FirewallRule, FirewallStatus, FirewallRuleCreate, FirewallAction,
    FirewallDirection, FirewallProtocol,
    Certificate, CertificateListResponse, CertificateType, CertificateStatus,
    CertificateUpload, SelfSignedCertRequest,
    MAX_CERTIFICATE_NAME_LENGTH, is_dns_hostname, parse_ip_address, validate_certificate_domain,
)

router = APIRouter(prefix="/security", tags=["security"])

FIREWALL_RULES_FILE = "/data/firewall_rules.json"
CERTS_DIR = "/data/certs"
CADDY_CERTS_DIR = "/data/caddy/certificates"
# Longest username a login attempt keeps (in characters).
LOGIN_ATTEMPT_USERNAME_CHARS = 128


# ==================== Login History ====================

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
    query = select(LoginAttempt)

    # Non-admin users can only see their own history
    if not current_user.is_admin:
        query = query.where(LoginAttempt.username == current_user.username)
    elif username:
        query = query.where(LoginAttempt.username == username)

    if success is not None:
        query = query.where(LoginAttempt.success == success)

    # Get total counts
    count_query = select(func.count()).select_from(LoginAttempt)
    if not current_user.is_admin:
        count_query = count_query.where(LoginAttempt.username == current_user.username)
    elif username:
        count_query = count_query.where(LoginAttempt.username == username)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get failed count
    failed_query = count_query.where(LoginAttempt.success == False)
    failed_result = await db.execute(failed_query)
    failed_count = failed_result.scalar() or 0

    # Get attempts with pagination, newest first. Times have whole seconds: the id orders
    # the attempts of one second (ADR-0248).
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
    older_than_days: int = 30,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Clear login history older than specified days. Admin only."""
    # The stored times are UTC without a zone, as SQLite's CURRENT_TIMESTAMP gives them (ADR-0248).
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=older_than_days)

    from sqlalchemy import delete
    stmt = delete(LoginAttempt).where(LoginAttempt.timestamp < cutoff)
    result = await db.execute(stmt)
    await db.commit()

    return {"status": "ok", "deleted_count": result.rowcount}


# ==================== Firewall Management ====================

def load_firewall_rules() -> list[FirewallRule]:
    """Load firewall rules from config file."""
    path = Path(FIREWALL_RULES_FILE)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return [FirewallRule(**r) for r in data]
    except Exception:
        return []


def save_firewall_rules(rules: list[FirewallRule]) -> None:
    """Save firewall rules to config file."""
    path = Path(FIREWALL_RULES_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([r.model_dump() for r in rules], indent=2))


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
        id=f"rule_{datetime.now().strftime('%Y%m%d%H%M%S')}",
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
        certs_path / f"{name}.chain.crt",
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


def _write_private_key(path: Path, key: str) -> None:
    """Write ``key`` to ``path``, readable and writable by the owner only (0600)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w") as fh:
        # open() applies the mode only to a new file: tighten one that already
        # existed (older versions wrote uploaded keys 0644) before writing.
        os.fchmod(fd, 0o600)
        fh.write(key)


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
    ``source`` empty, the PEM text ``pem`` on its standard input.
    """
    try:
        # Get subject, issuer, dates
        result = subprocess.run(
            ["openssl", "x509", *source, "-noout",
             "-subject", "-issuer", "-dates", "-fingerprint"],
            input=pem,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            return None

        info = {
            "domain": "",
            "issuer": "",
            "valid_from": "",
            "valid_until": "",
            "fingerprint": "",
            "is_valid": False
        }

        for line in result.stdout.split("\n"):
            if line.startswith("subject="):
                # Extract CN from subject
                cn_match = re.search(r'CN\s*=\s*([^,/]+)', line)
                if cn_match:
                    info["domain"] = cn_match.group(1).strip()
            elif line.startswith("issuer="):
                # Extract issuer organization or CN
                org_match = re.search(r'O\s*=\s*([^,/]+)', line)
                cn_match = re.search(r'CN\s*=\s*([^,/]+)', line)
                info["issuer"] = (org_match or cn_match).group(1).strip() if (org_match or cn_match) else "Unknown"
            elif line.startswith("notBefore="):
                info["valid_from"] = line.split("=", 1)[1].strip()
            elif line.startswith("notAfter="):
                info["valid_until"] = line.split("=", 1)[1].strip()
            elif "Fingerprint" in line:
                info["fingerprint"] = line.split("=", 1)[1].strip()

        # Check if certificate is currently valid
        verify_result = subprocess.run(
            ["openssl", "x509", *source, "-noout", "-checkend", "0"],
            input=pem,
            capture_output=True,
            text=True
        )
        info["is_valid"] = verify_result.returncode == 0

        return info
    except Exception:
        return None


def get_caddy_certificates() -> list[Certificate]:
    """Get certificates managed by Caddy."""
    certs = []
    caddy_path = Path(CADDY_CERTS_DIR)

    if not caddy_path.exists():
        return certs

    # Caddy stores certs in subdirectories by domain
    for issuer_dir in caddy_path.iterdir():
        if issuer_dir.is_dir():
            for domain_dir in issuer_dir.iterdir():
                if domain_dir.is_dir():
                    cert_file = domain_dir / f"{domain_dir.name}.crt"
                    if cert_file.exists():
                        info = get_certificate_info(str(cert_file))
                        if info:
                            cert_type = CertificateType.LETS_ENCRYPT if "Let's Encrypt" in (info.get("issuer") or "") else CertificateType.CUSTOM
                            certs.append(Certificate(
                                id=f"caddy_{domain_dir.name}",
                                type=cert_type,
                                domain=info.get("domain") or domain_dir.name,
                                issuer=info.get("issuer"),
                                valid_from=info.get("valid_from"),
                                valid_until=info.get("valid_until"),
                                is_valid=info.get("is_valid", False),
                                auto_renew=True,
                                fingerprint=info.get("fingerprint")
                            ))

    return certs


def get_custom_certificates() -> list[Certificate]:
    """Get custom uploaded certificates."""
    certs = []
    certs_path = Path(CERTS_DIR)

    if not certs_path.exists():
        return certs

    for cert_file in certs_path.glob("*.crt"):
        info = get_certificate_info(str(cert_file))
        if info:
            # Check if it's self-signed (issuer matches subject)
            is_self_signed = info.get("issuer") == info.get("domain")
            certs.append(Certificate(
                id=f"custom_{cert_file.stem}",
                type=CertificateType.SELF_SIGNED if is_self_signed else CertificateType.CUSTOM,
                domain=info.get("domain") or cert_file.stem,
                issuer=info.get("issuer"),
                valid_from=info.get("valid_from"),
                valid_until=info.get("valid_until"),
                is_valid=info.get("is_valid", False),
                auto_renew=False,
                fingerprint=info.get("fingerprint")
            ))

    return certs


@router.get("/certificates", response_model=CertificateListResponse)
async def list_certificates(_: User = Depends(require_admin)):
    """List all SSL/TLS certificates."""
    caddy_certs = get_caddy_certificates()
    custom_certs = get_custom_certificates()

    all_certs = caddy_certs + custom_certs

    # Determine active certificate (first valid one)
    active = None
    for cert in all_certs:
        if cert.is_valid:
            active = cert.id
            break

    return CertificateListResponse(
        certificates=all_certs,
        active_certificate=active
    )


@router.get("/certificates/status", response_model=CertificateStatus)
async def get_certificate_status(_: User = Depends(require_admin)):
    """Get current SSL/TLS certificate status."""
    caddy_certs = get_caddy_certificates()
    custom_certs = get_custom_certificates()

    all_certs = caddy_certs + custom_certs

    if not all_certs:
        return CertificateStatus(has_certificate=False)

    # Find the first valid certificate
    for cert in all_certs:
        if cert.is_valid:
            return CertificateStatus(
                has_certificate=True,
                type=cert.type,
                domain=cert.domain,
                valid_until=cert.valid_until,
                is_valid=True,
                auto_renew=cert.auto_renew
            )

    # If no valid cert, return info about the first one
    cert = all_certs[0]
    return CertificateStatus(
        has_certificate=True,
        type=cert.type,
        domain=cert.domain,
        valid_until=cert.valid_until,
        is_valid=False,
        auto_renew=cert.auto_renew
    )


@router.post("/certificates/self-signed")
async def generate_self_signed_certificate(
    request: SelfSignedCertRequest,
    current_user: User = Depends(require_admin)
):
    """Generate a self-signed certificate. Admin only."""
    # The schema only accepts a host name or an IP address short enough for a
    # subject CN (and so for the file names), so the domain can name files in
    # CERTS_DIR and go into -subj and -addext as it is.
    domain = request.domain
    cert_file, key_file, _chain_file = _certificate_files(domain)
    cert_file.parent.mkdir(parents=True, exist_ok=True)
    san = f"IP:{domain}" if parse_ip_address(domain) is not None else f"DNS:{domain}"

    try:
        # Build subject string
        subject = f"/CN={domain}"
        if request.organization:
            subject += f"/O={_subject_value(request.organization)}"
        if request.country:
            subject += f"/C={_subject_value(request.country)}"

        # openssl creates a new key file 0600, but writes into an existing one
        # without changing its mode: tighten a key an older version left 0644.
        try:
            key_file.chmod(0o600)
        except FileNotFoundError:
            pass

        # Generate self-signed certificate
        result = subprocess.run([
            "openssl", "req", "-x509", "-nodes",
            "-days", str(request.validity_days),
            "-newkey", "rsa:2048",
            "-keyout", str(key_file),
            "-out", str(cert_file),
            "-subj", subject,
            "-addext", f"subjectAltName={san}"
        ], capture_output=True, text=True)

        if result.returncode != 0:
            raise HTTPException(500, f"Failed to generate certificate: {result.stderr}")

        return {
            "status": "ok",
            "message": "Self-signed certificate generated",
            "certificate_id": f"custom_{domain}"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to generate certificate: {str(e)}")


@router.post("/certificates/upload")
async def upload_certificate(
    data: CertificateUpload,
    current_user: User = Depends(require_admin)
):
    """Upload a custom SSL/TLS certificate. Admin only."""
    certs_path = Path(CERTS_DIR)
    certs_path.mkdir(parents=True, exist_ok=True)

    try:
        # Validate and parse the certificate to get domain. openssl reads it
        # on its standard input: no temporary file to leave in /tmp on errors.
        info = get_pem_certificate_info(data.certificate)

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
        cert_file, key_file, chain_file = _certificate_files(domain)

        # Text that cannot be encoded (a lone surrogate) would fail half-way
        # through the writes below, with the certificate replaced and the old
        # key or chain file emptied.
        for text, what in ((data.private_key, "private key"),
                           (data.chain or "", "certificate chain")):
            try:
                text.encode()
            except UnicodeEncodeError:
                raise HTTPException(400, f"Invalid {what} format") from None

        # Save certificate and key
        cert_file.write_text(data.certificate)
        _write_private_key(key_file, data.private_key)

        # Save chain if provided
        if data.chain:
            chain_file.write_text(data.chain)

        return {
            "status": "ok",
            "message": "Certificate uploaded successfully",
            "certificate_id": f"custom_{domain}",
            "domain": domain
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to upload certificate: {str(e)}")


@router.delete("/certificates/{cert_id}")
async def delete_certificate(
    cert_id: str,
    current_user: User = Depends(require_admin)
):
    """Delete a custom certificate. Admin only."""
    if not cert_id.startswith("custom_"):
        raise HTTPException(400, "Can only delete custom certificates")

    # Not re-validated as a host name: certificates stored by older versions
    # under other names must stay deletable. The files must be in CERTS_DIR.
    try:
        cert_file, key_file, chain_file = _certificate_files(cert_id.removeprefix("custom_"))
    except ValueError:
        raise HTTPException(400, "Invalid certificate id") from None

    if not cert_file.exists():
        raise HTTPException(404, "Certificate not found")

    try:
        cert_file.unlink(missing_ok=True)
        key_file.unlink(missing_ok=True)
        chain_file.unlink(missing_ok=True)

        return {"status": "ok", "message": "Certificate deleted"}
    except Exception as e:
        raise HTTPException(500, f"Failed to delete certificate: {str(e)}")


# ==================== Helper for login tracking ====================

async def record_login_attempt(
    db: AsyncSession,
    username: str,
    success: bool,
    request: Request | None = None,
    failure_reason: str | None = None
):
    """Record a login attempt in the database."""
    ip_address = None
    user_agent = None

    if request:
        # Real client address: uvicorn already resolved the proxy's
        # X-Forwarded-For into request.client (see auth.utils.client_ip).
        ip_address = client_ip(request)
        user_agent = request.headers.get("User-Agent", "")[:500]  # Limit length

    attempt = LoginAttempt(
        # Failed logins are recorded before anyone is authenticated: keep the name short.
        username=username[:LOGIN_ATTEMPT_USERNAME_CHARS] if isinstance(username, str) else username,
        ip_address=ip_address,
        user_agent=user_agent,
        success=success,
        failure_reason=failure_reason
    )

    db.add(attempt)
    await db.commit()
