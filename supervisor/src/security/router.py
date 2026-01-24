import subprocess
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from ..database import get_db
from ..auth.utils import verify_token
from ..auth.models import User
from .models import LoginAttempt
from .schemas import (
    LoginAttemptResponse, LoginHistoryResponse,
    FirewallRule, FirewallStatus, FirewallRuleCreate, FirewallAction,
    FirewallDirection, FirewallProtocol,
    Certificate, CertificateListResponse, CertificateType, CertificateStatus,
    CertificateUpload, SelfSignedCertRequest, LetsEncryptRequest
)

router = APIRouter(prefix="/security", tags=["security"])

FIREWALL_RULES_FILE = "/data/firewall_rules.json"
CERTS_DIR = "/data/certs"
CADDY_CERTS_DIR = "/data/caddy/certificates"


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

    # Get attempts with pagination
    query = query.order_by(desc(LoginAttempt.timestamp)).offset(offset).limit(limit)
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
    current_user: User = Depends(verify_token),
    db: AsyncSession = Depends(get_db)
):
    """Clear login history older than specified days. Admin only."""
    if not current_user.is_admin:
        raise HTTPException(403, "Admin access required")

    cutoff = datetime.now() - timedelta(days=older_than_days)

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
                if "incoming" in line.lower():
                    if "allow" in line.lower():
                        default_in = "allow"
                    elif "reject" in line.lower():
                        default_in = "reject"
                elif "outgoing" in line.lower():
                    if "deny" in line.lower():
                        default_out = "deny"
                    elif "reject" in line.lower():
                        default_out = "reject"

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
async def get_firewall_status(_: User = Depends(verify_token)):
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
async def enable_firewall(current_user: User = Depends(verify_token)):
    """Enable the firewall. Admin only."""
    if not current_user.is_admin:
        raise HTTPException(403, "Admin access required")

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
async def disable_firewall(current_user: User = Depends(verify_token)):
    """Disable the firewall. Admin only."""
    if not current_user.is_admin:
        raise HTTPException(403, "Admin access required")

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
    current_user: User = Depends(verify_token)
):
    """Create a new firewall rule. Admin only."""
    if not current_user.is_admin:
        raise HTTPException(403, "Admin access required")

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
    current_user: User = Depends(verify_token)
):
    """Delete a firewall rule. Admin only."""
    if not current_user.is_admin:
        raise HTTPException(403, "Admin access required")

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
async def reset_firewall(current_user: User = Depends(verify_token)):
    """Reset firewall to default rules. Admin only."""
    if not current_user.is_admin:
        raise HTTPException(403, "Admin access required")

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

def get_certificate_info(cert_path: str) -> dict | None:
    """Parse certificate and extract information using openssl."""
    try:
        # Get subject, issuer, dates
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-noout",
             "-subject", "-issuer", "-dates", "-fingerprint"],
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
            ["openssl", "x509", "-in", cert_path, "-noout", "-checkend", "0"],
            capture_output=True
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
async def list_certificates(_: User = Depends(verify_token)):
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
async def get_certificate_status(_: User = Depends(verify_token)):
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
    current_user: User = Depends(verify_token)
):
    """Generate a self-signed certificate. Admin only."""
    if not current_user.is_admin:
        raise HTTPException(403, "Admin access required")

    certs_path = Path(CERTS_DIR)
    certs_path.mkdir(parents=True, exist_ok=True)

    key_file = certs_path / f"{request.domain}.key"
    cert_file = certs_path / f"{request.domain}.crt"

    try:
        # Build subject string
        subject = f"/CN={request.domain}"
        if request.organization:
            subject += f"/O={request.organization}"
        if request.country:
            subject += f"/C={request.country}"

        # Generate self-signed certificate
        result = subprocess.run([
            "openssl", "req", "-x509", "-nodes",
            "-days", str(request.validity_days),
            "-newkey", "rsa:2048",
            "-keyout", str(key_file),
            "-out", str(cert_file),
            "-subj", subject,
            "-addext", f"subjectAltName=DNS:{request.domain}"
        ], capture_output=True, text=True)

        if result.returncode != 0:
            raise HTTPException(500, f"Failed to generate certificate: {result.stderr}")

        return {
            "status": "ok",
            "message": "Self-signed certificate generated",
            "certificate_id": f"custom_{request.domain}"
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to generate certificate: {str(e)}")


@router.post("/certificates/upload")
async def upload_certificate(
    data: CertificateUpload,
    current_user: User = Depends(verify_token)
):
    """Upload a custom SSL/TLS certificate. Admin only."""
    if not current_user.is_admin:
        raise HTTPException(403, "Admin access required")

    certs_path = Path(CERTS_DIR)
    certs_path.mkdir(parents=True, exist_ok=True)

    try:
        # Validate and parse the certificate to get domain
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.crt', delete=False) as f:
            f.write(data.certificate)
            temp_cert = f.name

        info = get_certificate_info(temp_cert)
        Path(temp_cert).unlink()

        if not info:
            raise HTTPException(400, "Invalid certificate format")

        domain = info.get("domain", "custom")

        # Save certificate and key
        cert_file = certs_path / f"{domain}.crt"
        key_file = certs_path / f"{domain}.key"

        cert_file.write_text(data.certificate)
        key_file.write_text(data.private_key)

        # Save chain if provided
        if data.chain:
            chain_file = certs_path / f"{domain}.chain.crt"
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
    current_user: User = Depends(verify_token)
):
    """Delete a custom certificate. Admin only."""
    if not current_user.is_admin:
        raise HTTPException(403, "Admin access required")

    if not cert_id.startswith("custom_"):
        raise HTTPException(400, "Can only delete custom certificates")

    domain = cert_id.replace("custom_", "")
    certs_path = Path(CERTS_DIR)

    cert_file = certs_path / f"{domain}.crt"
    key_file = certs_path / f"{domain}.key"
    chain_file = certs_path / f"{domain}.chain.crt"

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
        # Get client IP (handle proxy headers)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip_address = forwarded.split(",")[0].strip()
        else:
            ip_address = request.client.host if request.client else None

        user_agent = request.headers.get("User-Agent", "")[:500]  # Limit length

    attempt = LoginAttempt(
        username=username,
        ip_address=ip_address,
        user_agent=user_agent,
        success=success,
        failure_reason=failure_reason
    )

    db.add(attempt)
    await db.commit()
