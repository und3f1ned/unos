import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
import errno
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.auth.utils import verify_token
from src.main import app


class TestLoginAttemptModel:
    """Tests for LoginAttempt model."""

    def test_login_attempt_model_has_required_fields(self):
        """Test that LoginAttempt model has required fields."""
        from src.security.models import LoginAttempt

        assert hasattr(LoginAttempt, "id")
        assert hasattr(LoginAttempt, "username")
        assert hasattr(LoginAttempt, "ip_address")
        assert hasattr(LoginAttempt, "user_agent")
        assert hasattr(LoginAttempt, "success")
        assert hasattr(LoginAttempt, "failure_reason")
        assert hasattr(LoginAttempt, "timestamp")

    def test_login_attempt_tablename(self):
        """Test LoginAttempt model tablename."""
        from src.security.models import LoginAttempt

        assert LoginAttempt.__tablename__ == "login_attempts"


class TestSecuritySchemas:
    """Tests for security schemas."""

    def test_login_attempt_response_schema(self):
        """Test LoginAttemptResponse schema."""
        from src.security.schemas import LoginAttemptResponse

        attempt = LoginAttemptResponse(
            id=1,
            username="testuser",
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
            success=True,
            failure_reason=None,
            timestamp=datetime.now()
        )

        assert attempt.id == 1
        assert attempt.username == "testuser"
        assert attempt.success is True

    def test_login_history_response_schema(self):
        """Test LoginHistoryResponse schema."""
        from src.security.schemas import LoginHistoryResponse

        response = LoginHistoryResponse(
            attempts=[],
            total=10,
            failed_count=2,
            success_count=8
        )

        assert response.total == 10
        assert response.failed_count == 2
        assert response.success_count == 8

    def test_firewall_rule_schema(self):
        """Test FirewallRule schema."""
        from src.security.schemas import FirewallRule, FirewallAction, FirewallDirection, FirewallProtocol

        rule = FirewallRule(
            id="rule_001",
            name="SSH Access",
            action=FirewallAction.ALLOW,
            direction=FirewallDirection.IN,
            protocol=FirewallProtocol.TCP,
            port="22",
            enabled=True
        )

        assert rule.name == "SSH Access"
        assert rule.action == FirewallAction.ALLOW
        assert rule.port == "22"

    def test_firewall_status_schema(self):
        """Test FirewallStatus schema."""
        from src.security.schemas import FirewallStatus

        status = FirewallStatus(
            enabled=True,
            default_incoming="deny",
            default_outgoing="allow",
            rules=[]
        )

        assert status.enabled is True
        assert status.default_incoming == "deny"

    def test_certificate_schema(self):
        """Test Certificate schema."""
        from src.security.schemas import Certificate, CertificateType

        cert = Certificate(
            id="cert_001",
            type=CertificateType.SELF_SIGNED,
            domain="nas.local",
            issuer="Self",
            is_valid=True
        )

        assert cert.domain == "nas.local"
        assert cert.type == CertificateType.SELF_SIGNED

    def test_certificate_status_schema(self):
        """Test CertificateStatus schema."""
        from src.security.schemas import CertificateStatus, CertificateType

        status = CertificateStatus(
            has_certificate=True,
            type=CertificateType.LETS_ENCRYPT,
            domain="example.com",
            is_valid=True,
            auto_renew=True
        )

        assert status.has_certificate is True
        assert status.auto_renew is True

    def test_self_signed_cert_request_schema(self):
        """Test SelfSignedCertRequest schema."""
        from src.security.schemas import SelfSignedCertRequest

        request = SelfSignedCertRequest(
            domain="nas.local",
            validity_days=365,
            organization="Home Lab",
            country="US"
        )

        assert request.domain == "nas.local"
        assert request.validity_days == 365


class TestFirewallFunctions:
    """Tests for firewall management functions."""

    def test_load_firewall_rules_empty(self, tmp_path):
        """Test loading firewall rules when file doesn't exist."""
        from src.security.router import load_firewall_rules

        with patch("src.security.router.FIREWALL_RULES_FILE", str(tmp_path / "rules.json")):
            rules = load_firewall_rules()
            assert rules == []

    def test_save_and_load_firewall_rules(self, tmp_path):
        """Test saving and loading firewall rules."""
        from src.security.router import load_firewall_rules, save_firewall_rules
        from src.security.schemas import FirewallRule, FirewallAction, FirewallDirection, FirewallProtocol

        rules_file = tmp_path / "rules.json"

        rule = FirewallRule(
            id="rule_test",
            name="Test Rule",
            action=FirewallAction.ALLOW,
            direction=FirewallDirection.IN,
            protocol=FirewallProtocol.TCP,
            port="8080",
            enabled=True
        )

        with patch("src.security.router.FIREWALL_RULES_FILE", str(rules_file)):
            save_firewall_rules([rule])
            loaded = load_firewall_rules()

            assert len(loaded) == 1
            assert loaded[0].name == "Test Rule"
            assert loaded[0].port == "8080"

    def test_get_ufw_status_active(self):
        """Test parsing UFW status when active."""
        from src.security.router import get_ufw_status

        mock_result = MagicMock()
        mock_result.stdout = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)"""

        with patch("subprocess.run", return_value=mock_result):
            enabled, default_in, default_out = get_ufw_status()

            assert enabled is True
            assert default_in == "deny"
            assert default_out == "allow"

    def test_get_ufw_status_inactive(self):
        """Test parsing UFW status when inactive."""
        from src.security.router import get_ufw_status

        mock_result = MagicMock()
        mock_result.stdout = "Status: inactive"

        with patch("subprocess.run", return_value=mock_result):
            enabled, default_in, default_out = get_ufw_status()

            assert enabled is False

    def test_get_ufw_status_not_installed(self):
        """Test UFW status when not installed."""
        from src.security.router import get_ufw_status

        with patch("subprocess.run", side_effect=FileNotFoundError):
            enabled, default_in, default_out = get_ufw_status()

            assert enabled is False
            assert default_in == "deny"
            assert default_out == "allow"


class TestCertificateFunctions:
    """Tests for certificate management functions."""

    def test_get_certificate_info_valid(self, tmp_path):
        """Test parsing valid certificate info."""
        from src.security.router import get_certificate_info

        # Create a mock openssl output
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = """subject=CN = nas.local
issuer=CN = nas.local
notBefore=Jan  1 00:00:00 2025 GMT
notAfter=Dec 31 23:59:59 2025 GMT
SHA256 Fingerprint=AA:BB:CC:DD:EE:FF"""

        verify_result = MagicMock()
        verify_result.returncode = 0

        with patch("subprocess.run", side_effect=[mock_result, verify_result]):
            info = get_certificate_info("/tmp/test.crt")

            assert info is not None
            assert info["domain"] == "nas.local"
            assert info["is_valid"] is True

    def test_get_certificate_info_invalid(self):
        """Test parsing certificate when openssl fails."""
        from src.security.router import get_certificate_info

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            info = get_certificate_info("/tmp/invalid.crt")

            assert info is None

    def test_get_pem_certificate_info_gives_openssl_the_text(self):
        """No file: openssl reads the certificate on its standard input."""
        from src.security.router import get_pem_certificate_info

        calls = []

        def openssl(cmd, **kwargs):
            calls.append((cmd, kwargs.get("input")))
            return subprocess.CompletedProcess(cmd, 0, "subject=CN = nas.local\n", "")

        with patch("subprocess.run", side_effect=openssl):
            info = get_pem_certificate_info("PEM")

        assert (info["domain"], info["is_valid"]) == ("nas.local", True)
        assert [cmd[:2] for cmd, _ in calls] == [["openssl", "x509"]] * 2
        assert all("-in" not in cmd and text == "PEM" for cmd, text in calls)

    def test_get_pem_certificate_info_refuses_text_that_cannot_be_encoded(self):
        from src.security.router import get_pem_certificate_info

        with patch("subprocess.run") as run:
            assert get_pem_certificate_info("PEM\ud800") is None
        run.assert_not_called()

    def test_get_custom_certificates_empty(self, tmp_path):
        """Test getting custom certificates when directory is empty."""
        from src.security.router import get_custom_certificates

        with patch("src.security.router.CERTS_DIR", str(tmp_path)):
            certs = get_custom_certificates()
            assert certs == []

    def test_get_caddy_certificates_missing_dir(self, tmp_path):
        """Test getting Caddy certificates when directory doesn't exist."""
        from src.security.router import get_caddy_certificates

        with patch("src.security.router.CADDY_CERTS_DIR", str(tmp_path / "nonexistent")):
            certs = get_caddy_certificates()
            assert certs == []


class TestRecordLoginAttempt:
    """Tests for login attempt recording."""

    @pytest.mark.asyncio
    async def test_record_login_attempt_success(self):
        """Test recording a successful login attempt."""
        from src.security.router import record_login_attempt

        mock_db = AsyncMock()
        mock_request = MagicMock()
        mock_request.headers = {"User-Agent": "Test Browser", "X-Forwarded-For": "192.168.1.100"}
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"

        await record_login_attempt(
            db=mock_db,
            username="testuser",
            success=True,
            request=mock_request
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_login_attempt_failed(self):
        """Test recording a failed login attempt."""
        from src.security.router import record_login_attempt

        mock_db = AsyncMock()

        await record_login_attempt(
            db=mock_db,
            username="baduser",
            success=False,
            failure_reason="Incorrect password"
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


class TestFirewallRuleApplication:
    """Tests for applying firewall rules."""

    def test_apply_ufw_rule_allow(self):
        """Test applying an allow rule."""
        from src.security.router import apply_ufw_rule
        from src.security.schemas import FirewallRule, FirewallAction, FirewallDirection, FirewallProtocol

        rule = FirewallRule(
            id="test",
            name="Test",
            action=FirewallAction.ALLOW,
            direction=FirewallDirection.IN,
            protocol=FirewallProtocol.TCP,
            port="80",
            enabled=True
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = "Rule added"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            success, error = apply_ufw_rule(rule)

            assert success is True
            assert error == ""
            mock_run.assert_called_once()

    def test_apply_ufw_rule_failure(self):
        """Test applying a rule that fails."""
        from src.security.router import apply_ufw_rule
        from src.security.schemas import FirewallRule, FirewallAction, FirewallDirection, FirewallProtocol

        rule = FirewallRule(
            id="test",
            name="Test",
            action=FirewallAction.DENY,
            direction=FirewallDirection.OUT,
            protocol=FirewallProtocol.UDP,
            port="53",
            enabled=True
        )

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Permission denied"
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            success, error = apply_ufw_rule(rule)

            assert success is False
            assert "Permission denied" in error

    def test_apply_ufw_rule_delete(self):
        """Test deleting a rule."""
        from src.security.router import apply_ufw_rule
        from src.security.schemas import FirewallRule, FirewallAction, FirewallDirection, FirewallProtocol

        rule = FirewallRule(
            id="test",
            name="Test",
            action=FirewallAction.ALLOW,
            direction=FirewallDirection.IN,
            protocol=FirewallProtocol.TCP,
            port="443",
            enabled=True
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = "Rule deleted"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            success, error = apply_ufw_rule(rule, delete=True)

            assert success is True
            # Verify 'delete' is in the command
            call_args = mock_run.call_args[0][0]
            assert "delete" in call_args


class TestEnums:
    """Tests for security enums."""

    def test_firewall_action_values(self):
        """Test FirewallAction enum values."""
        from src.security.schemas import FirewallAction

        assert FirewallAction.ALLOW.value == "allow"
        assert FirewallAction.DENY.value == "deny"
        assert FirewallAction.REJECT.value == "reject"

    def test_firewall_protocol_values(self):
        """Test FirewallProtocol enum values."""
        from src.security.schemas import FirewallProtocol

        assert FirewallProtocol.TCP.value == "tcp"
        assert FirewallProtocol.UDP.value == "udp"
        assert FirewallProtocol.ANY.value == "any"

    def test_firewall_direction_values(self):
        """Test FirewallDirection enum values."""
        from src.security.schemas import FirewallDirection

        assert FirewallDirection.IN.value == "in"
        assert FirewallDirection.OUT.value == "out"

    def test_certificate_type_values(self):
        """Test CertificateType enum values."""
        from src.security.schemas import CertificateType

        assert CertificateType.SELF_SIGNED.value == "self_signed"
        assert CertificateType.LETS_ENCRYPT.value == "lets_encrypt"
        assert CertificateType.CUSTOM.value == "custom"


# ==================== Certificate names (INJ-7) ====================

API = "/api/v1/security/certificates"

INVALID_DOMAINS = [
    "../../ESCAPED",
    "../x",
    "/etc/x",
    "/proc/1/root/etc/ssl/private/x",
    "a/b",
    "..",
    ".",
    "",
    "nas.local/../../x",
    "x\x00y",
    "a b",
    " nas.local",
    "nas.local\n",
    "*",
    "*.example.com",
    "-leading.example",
    "trailing-.example",
    "a..b",
    "nas.local.",
    ".nas.local",
    "nas_local",
    "x" * 64 + ".example",
    ".".join(["a" * 63] * 4),  # 255 characters
    "fe80::1%eth0",
    "fe80::1%../../x",
    "1.2.3.4/24",
    "nas.local -addext x",
    "CN=evil/O=x",
    "näs.local",
]


@pytest.fixture
def certs(tmp_path, monkeypatch):
    """CERTS_DIR in a scratch tree; openssl replaced by a fake that writes the files."""
    certs_dir = tmp_path / "data" / "certs"
    monkeypatch.setattr("src.security.router.CERTS_DIR", str(certs_dir))
    calls = []

    def fake_openssl(cmd, **kwargs):
        assert cmd[:2] == ["openssl", "req"], cmd
        calls.append(cmd)
        Path(cmd[cmd.index("-keyout") + 1]).write_text("KEY")
        Path(cmd[cmd.index("-out") + 1]).write_text("CERT")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_openssl)
    return SimpleNamespace(root=tmp_path, dir=certs_dir, calls=calls)


@pytest.fixture
def admin_api():
    admin = SimpleNamespace(id=1, username="admin", is_admin=True)
    app.dependency_overrides[verify_token] = lambda: admin
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_token, None)


def _files_under(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


class TestCertificateDomainValidation:
    @pytest.mark.parametrize("domain", INVALID_DOMAINS)
    def test_schema_refuses_anything_but_host_names_and_addresses(self, domain):
        from pydantic import ValidationError
        from src.security.schemas import SelfSignedCertRequest

        with pytest.raises(ValidationError):
            SelfSignedCertRequest(domain=domain)

    @pytest.mark.parametrize("domain,expected", [
        ("nas.local", "nas.local"),
        ("NAS", "NAS"),
        ("my-nas.home.arpa", "my-nas.home.arpa"),
        ("x" * 63, "x" * 63),  # the longest label (the whole CN can have 64 characters)
        ("192.168.1.10", "192.168.1.10"),
        ("FD00::1", "fd00::1"),
    ])
    def test_schema_accepts_host_names_and_addresses(self, domain, expected):
        from src.security.schemas import SelfSignedCertRequest

        assert SelfSignedCertRequest(domain=domain).domain == expected

    def test_traversal_attempts_get_422_and_write_nothing(self, certs, admin_api):
        for domain in INVALID_DOMAINS:
            response = admin_api.post(f"{API}/self-signed", json={"domain": domain})
            assert response.status_code == 422, (domain, response.text)

        assert certs.calls == []
        assert _files_under(certs.root) == []

    def test_host_name_gets_a_dns_san(self, certs, admin_api):
        response = admin_api.post(f"{API}/self-signed", json={"domain": "nas.local"})

        assert response.status_code == 200, response.text
        assert response.json()["certificate_id"] == "custom_nas.local"
        [cmd] = certs.calls
        assert cmd[cmd.index("-keyout") + 1] == str(certs.dir / "nas.local.key")
        assert cmd[cmd.index("-out") + 1] == str(certs.dir / "nas.local.crt")
        assert cmd[cmd.index("-subj") + 1] == "/CN=nas.local"
        assert cmd[cmd.index("-addext") + 1] == "subjectAltName=DNS:nas.local"
        assert _files_under(certs.root) == ["data/certs/nas.local.crt", "data/certs/nas.local.key"]

    @pytest.mark.parametrize("domain,name", [("192.168.1.10", "192.168.1.10"), ("FD00::1", "fd00::1")])
    def test_ip_address_gets_an_ip_san(self, certs, admin_api, domain, name):
        response = admin_api.post(f"{API}/self-signed", json={"domain": domain})

        assert response.status_code == 200, response.text
        [cmd] = certs.calls
        assert cmd[cmd.index("-addext") + 1] == f"subjectAltName=IP:{name}"
        assert cmd[cmd.index("-subj") + 1] == f"/CN={name}"
        assert (certs.dir / f"{name}.crt").is_file()

    def test_subject_fields_are_escaped(self, certs, admin_api):
        response = admin_api.post(f"{API}/self-signed", json={
            "domain": "nas.local", "organization": "Home/Lab+Co\\", "country": "DE",
        })

        assert response.status_code == 200
        [cmd] = certs.calls
        assert cmd[cmd.index("-subj") + 1] == "/CN=nas.local/O=Home\\/Lab\\+Co\\\\/C=DE"

    def test_openssl_failure_is_reported_once(self, certs, admin_api, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 1, "", "unknown option"))
        response = admin_api.post(f"{API}/self-signed", json={"domain": "nas.local"})

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to generate certificate: unknown option"


class TestCertificateFilesStayInCertsDir:
    @pytest.mark.parametrize("name", ["", ".", "..", "../x", "a/b", "/etc/passwd", "x\x00"])
    def test_names_that_leave_the_directory_are_refused(self, certs, name):
        from src.security.router import _certificate_files

        with pytest.raises(ValueError):
            _certificate_files(name)

    def test_files_are_direct_children(self, certs):
        from src.security.router import _certificate_files

        files = _certificate_files("*.example.com")
        assert [f.name for f in files] == ["*.example.com.crt", "*.example.com.key",
                                           "*.example.com.chain.crt"]
        assert all(f.parent == certs.dir for f in files)

    @pytest.mark.parametrize("cert_id", ["custom_", "custom_.", "custom_.."])
    def test_delete_refuses_names_that_are_not_names(self, certs, admin_api, cert_id):
        # What these ids used to map to: ".crt", "..crt" and "...crt".
        certs.dir.mkdir(parents=True)
        for name in (".crt", "..crt", "...crt"):
            (certs.dir / name).write_text("keep me")

        assert admin_api.delete(f"{API}/{cert_id}").status_code == 400
        assert sorted(p.name for p in certs.dir.iterdir()) == ["...crt", "..crt", ".crt"]

    def test_delete_with_encoded_slash_does_not_leave_the_directory(self, certs, admin_api):
        victim = certs.root / "data" / "victim.crt"
        victim.parent.mkdir(parents=True, exist_ok=True)
        victim.write_text("keep me")

        response = admin_api.delete(f"{API}/custom_..%2Fvictim")

        assert response.status_code in (400, 404)
        assert victim.read_text() == "keep me"

    def test_delete_removes_a_stored_certificate(self, certs, admin_api):
        certs.dir.mkdir(parents=True)
        for suffix in (".crt", ".key", ".chain.crt"):
            (certs.dir / f"nas.local{suffix}").write_text("x")

        assert admin_api.delete(f"{API}/custom_nas.local").status_code == 200
        assert admin_api.delete(f"{API}/custom_missing.local").status_code == 404
        assert list(certs.dir.iterdir()) == []

    @pytest.mark.parametrize("name", ["My NAS", "*", "a b.local", "-x.local"])
    def test_delete_refuses_an_id_that_names_no_certificate_name(self, certs, admin_api, name):
        """The certificates are stored under the names that upload and self-signed take
        (security.no-legacy-certificate-fixups, ADR-0207): the delete checks its id the same way,
        and a file of another name is no certificate of UNOS."""
        certs.dir.mkdir(parents=True)
        (certs.dir / f"{name}.crt").write_text("keep me")

        response = admin_api.delete(f"{API}/custom_{name}")

        assert (response.status_code, response.json()["detail"]) == (400, "Invalid certificate id")
        assert (certs.dir / f"{name}.crt").read_text() == "keep me"

    @pytest.mark.parametrize("common_name", ["..", "", "a/b", "My NAS", "*", "*.*.example.com"])
    def test_upload_refuses_a_common_name_that_is_not_a_name(self, certs, admin_api, monkeypatch,
                                                              common_name):
        monkeypatch.setattr("src.security.router.get_pem_certificate_info",
                            lambda pem: {"domain": common_name, "issuer": "x"})
        response = admin_api.post(f"{API}/upload", json={"certificate": "PEM", "private_key": "KEY"})

        assert response.status_code == 400
        assert _files_under(certs.root) == []

    @pytest.mark.parametrize("common_name", ["nas.local", "*.example.com", "10.0.0.2"])
    def test_upload_stores_under_the_common_name(self, certs, admin_api, monkeypatch, common_name):
        monkeypatch.setattr("src.security.router.get_pem_certificate_info",
                            lambda pem: {"domain": common_name, "issuer": "x"})
        response = admin_api.post(f"{API}/upload", json={
            "certificate": "PEM", "private_key": "KEY", "chain": "CHAIN",
        })

        assert response.status_code == 200, response.text
        assert response.json()["certificate_id"] == f"custom_{common_name}"
        assert _files_under(certs.root) == sorted(
            f"data/certs/{common_name}{suffix}" for suffix in (".chain.crt", ".crt", ".key")
        )


# ==================== Certificate name length (ADV-7) ====================

# Longest name a certificate is stored under: "<name>.chain.crt" is then
# NAME_MAX (255) bytes.
LONGEST_NAME = 245
# Longest subject CN (X.509): what openssl puts a self-signed domain into.
LONGEST_COMMON_NAME = 64


def _host_name(length: int) -> str:
    """A host name of ``length`` characters: labels of 62 characters, then the rest."""
    labels = []
    while length > 63:
        labels.append("a" * 62)
        length -= 63
    return ".".join([*labels, "b" * length])


def _upload_as(monkeypatch, admin_api, common_name: str):
    """Upload a certificate (with key and chain) whose subject CN is ``common_name``."""
    monkeypatch.setattr("src.security.router.get_pem_certificate_info",
                        lambda pem: {"domain": common_name, "issuer": "x"})
    return admin_api.post(f"{API}/upload", json={
        "certificate": "PEM", "private_key": "KEY", "chain": "CHAIN",
    })


class TestCertificateNameLength:
    """Each file of a certificate (<name>.crt, .key, .chain.crt) must fit in NAME_MAX bytes,
    and the domain of a self-signed certificate in its subject CN."""

    def test_host_name_helper(self):
        from src.security.schemas import is_dns_hostname

        for length in (1, 63, 64, 65, 243, 245, 246, 253):
            assert len(_host_name(length)) == length
            assert is_dns_hostname(_host_name(length))

    def test_self_signed_accepts_the_longest_common_name(self, certs, admin_api):
        name = _host_name(LONGEST_COMMON_NAME)
        response = admin_api.post(f"{API}/self-signed", json={"domain": name})

        assert response.status_code == 200, response.text
        assert response.json()["certificate_id"] == f"custom_{name}"
        [cmd] = certs.calls
        assert cmd[cmd.index("-subj") + 1] == f"/CN={name}"
        assert _files_under(certs.root) == [f"data/certs/{name}.crt", f"data/certs/{name}.key"]

    # Real openssl refuses a CN over 64 characters only after writing the key,
    # and names of 252 and more do not fit the key file name at all.
    @pytest.mark.parametrize("length", [LONGEST_COMMON_NAME + 1, LONGEST_NAME + 1, 253])
    def test_self_signed_refuses_longer_names_and_writes_nothing(self, certs, admin_api, length):
        response = admin_api.post(f"{API}/self-signed", json={"domain": _host_name(length)})

        assert response.status_code == 422, response.text
        assert f"at most {LONGEST_COMMON_NAME} characters" in response.text
        assert certs.calls == []
        assert _files_under(certs.root) == []

    @pytest.mark.parametrize("common_name", [
        pytest.param(_host_name(LONGEST_NAME), id="host-name"),
        pytest.param("*." + _host_name(LONGEST_NAME - 2), id="wildcard"),
    ])
    def test_upload_accepts_the_longest_name(self, certs, admin_api, monkeypatch, common_name):
        response = _upload_as(monkeypatch, admin_api, common_name)

        assert response.status_code == 200, response.text
        assert response.json()["domain"] == common_name
        files = _files_under(certs.root)
        assert files == sorted(
            f"data/certs/{common_name}{suffix}" for suffix in (".chain.crt", ".crt", ".key")
        )
        assert max(len(Path(f).name) for f in files) == 255

    @pytest.mark.parametrize("common_name", [
        pytest.param(_host_name(LONGEST_NAME + 1), id="host-name"),
        pytest.param("*." + _host_name(LONGEST_NAME - 1), id="wildcard"),
        pytest.param(_host_name(253), id="longest-host-name"),
    ])
    def test_upload_refuses_longer_names_and_writes_nothing(self, certs, admin_api, monkeypatch,
                                                             common_name):
        response = _upload_as(monkeypatch, admin_api, common_name)

        assert response.status_code == 400, response.text
        assert f"at most {LONGEST_NAME} characters" in response.json()["detail"]
        assert _files_under(certs.root) == []


# ==================== Private key file mode (ADV-6) ====================

class TestPrivateKeyMode:
    """Private key files are readable and writable by their owner only (0600)."""

    @pytest.fixture(autouse=True)
    def umask_022(self):
        # The usual umask, under which a plain write creates a file 0644.
        old = os.umask(0o022)
        try:
            yield
        finally:
            os.umask(old)

    def test_uploaded_key_file_is_0600(self, certs, admin_api, monkeypatch):
        assert _upload_as(monkeypatch, admin_api, "nas.local").status_code == 200

        key_file = certs.dir / "nas.local.key"
        assert key_file.read_text() == "KEY"
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

    @pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command")
    def test_self_signed_key_file_is_0600(self, upload_env, admin_api):
        """The real openssl creates the key file 0600 under the usual umask, as
        _write_private_key creates an uploaded one: UNOS writes no key of another mode that
        would need a fix-up (security.no-legacy-certificate-fixups, ADR-0207)."""
        response = admin_api.post(f"{API}/self-signed", json={"domain": "nas.local"})

        assert response.status_code == 200, response.text
        key_file = upload_env.certs / "nas.local.key"
        subprocess.run(["openssl", "pkey", "-in", str(key_file), "-noout"], check=True, timeout=30)
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

    def test_no_key_mode_is_fixed_up(self):
        """The fix-ups for the keys that older versions left 0644 are gone
        (security.no-legacy-certificate-fixups, ADR-0207)."""
        router = (Path(__file__).resolve().parents[1] / "src" / "security" / "router.py").read_text()
        assert "chmod" not in router
        assert "older version" not in router


# ==================== Uploads leave no temporary file (R-6) ====================

# A self-signed certificate for nas.local, valid from 2000 to 2100. Its key
# was not kept: the upload stores the key it is given without checking it.
TEST_CERTIFICATE = """\
-----BEGIN CERTIFICATE-----
MIIBXTCCAQSgAwIBAgIUEIPM0EdnLHHR2nXJTOGBOt2HaIMwCgYIKoZIzj0EAwIw
FDESMBAGA1UEAwwJbmFzLmxvY2FsMCAXDTAwMDEwMTAwMDAwMFoYDzIxMDAwMTAx
MDAwMDAwWjAUMRIwEAYDVQQDDAluYXMubG9jYWwwWTATBgcqhkjOPQIBBggqhkjO
PQMBBwNCAATpbgn0Kr6ADAqqq9N6WnNzOnRc67+lA4kn3vnL4GbTL1/sB1GMOWHM
ffgFp1zBd2oZcwpVvz6bINErzEPIKsHbozIwMDAdBgNVHQ4EFgQUiM3eBUv/Gooo
Lb/jjg44Jz4x/OUwDwYDVR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAgNHADBEAiB5
8872kuxklZZLG0VDJRGJNzhR91gvZAqmfE4dLAW8sQIgNrpFr2nyQfeHalZjZkUj
nPuzPwQ/XS0fSXPzk2aOjqg=
-----END CERTIFICATE-----
"""

NOT_A_CERTIFICATE = "-----BEGIN CERTIFICATE-----\nnot a certificate\n-----END CERTIFICATE-----\n"


@pytest.fixture
def upload_env(tmp_path, monkeypatch):
    """The certificate directories in a scratch tree, and an empty directory
    for the tempfile module. openssl is the real one."""
    certs_dir = tmp_path / "data" / "certs"
    monkeypatch.setattr("src.security.router.CERTS_DIR", str(certs_dir))
    monkeypatch.setattr("src.security.router.CADDY_CERTS_DIR", str(tmp_path / "caddy"))
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    return SimpleNamespace(certs=certs_dir, temp=temp_dir)


def _upload(admin_api, certificate: str, private_key: str = "KEY", chain: str | None = None):
    """POST an upload. json.dumps escapes a lone surrogate as JSON can (httpx would fail to encode it)."""
    body = {"certificate": certificate, "private_key": private_key}
    if chain is not None:
        body["chain"] = chain
    return admin_api.post(f"{API}/upload", content=json.dumps(body),
                          headers={"Content-Type": "application/json"})


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command")
class TestCertificateUploadLeavesNoTemporaryFile:
    """openssl reads an uploaded certificate on its standard input.

    The upload wrote it to a temporary file first, and anything that failed
    before the file was removed (a full /tmp, text that cannot be encoded)
    left it in /tmp, in RAM in the container, until a restart.
    """

    def test_valid_certificate(self, upload_env, admin_api):
        response = _upload(admin_api, TEST_CERTIFICATE)

        assert response.status_code == 200, response.text
        assert response.json() == {
            "status": "ok",
            "message": "Certificate uploaded successfully",
            "certificate_id": "custom_nas.local",
            "domain": "nas.local",
        }
        assert (upload_env.certs / "nas.local.crt").read_text() == TEST_CERTIFICATE
        assert (upload_env.certs / "nas.local.key").read_text() == "KEY"
        assert list(upload_env.temp.iterdir()) == []

        # Stored certificates are still read from their files.
        [cert] = admin_api.get(API).json()["certificates"]
        assert {key: cert[key] for key in ("id", "type", "domain", "issuer", "valid_until", "is_valid")} == {
            "id": "custom_nas.local",
            "type": "self_signed",
            "domain": "nas.local",
            "issuer": "nas.local",
            "valid_until": "Jan  1 00:00:00 2100 GMT",
            "is_valid": True,
        }
        assert cert["fingerprint"]

    def test_invalid_certificate(self, upload_env, admin_api):
        response = _upload(admin_api, NOT_A_CERTIFICATE)

        assert (response.status_code, response.json()) == (400, {"detail": "Invalid certificate format"})
        assert list(upload_env.certs.iterdir()) == []
        assert list(upload_env.temp.iterdir()) == []

    def test_full_temporary_directory(self, upload_env, admin_api, monkeypatch):
        """A full /tmp: a temporary file can be made, but not written."""
        make = tempfile.NamedTemporaryFile

        def full(*args, **kwargs):
            file = make(*args, **kwargs)

            def write(data):
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

            file.write = write
            return file

        monkeypatch.setattr(tempfile, "NamedTemporaryFile", full)
        response = _upload(admin_api, TEST_CERTIFICATE)

        assert response.status_code == 200, response.text
        assert list(upload_env.temp.iterdir()) == []

    def test_certificate_that_cannot_be_encoded(self, upload_env, admin_api):
        """A lone surrogate (JSON can carry one as an escape) answers 400, not 500."""
        response = _upload(admin_api, TEST_CERTIFICATE + "\ud800")

        assert (response.status_code, response.json()) == (400, {"detail": "Invalid certificate format"})
        assert list(upload_env.certs.iterdir()) == []
        assert list(upload_env.temp.iterdir()) == []

    @pytest.mark.parametrize("field,message", [
        ("private_key", "Invalid private key format"),
        ("chain", "Invalid certificate chain format"),
    ])
    def test_key_or_chain_that_cannot_be_encoded_changes_nothing(self, upload_env, admin_api,
                                                                  field, message):
        # They used to fail half-way: the certificate replaced, the old key emptied.
        upload_env.certs.mkdir(parents=True)
        old = {f"nas.local{suffix}": f"old {suffix}" for suffix in (".crt", ".key", ".chain.crt")}
        for name, text in old.items():
            (upload_env.certs / name).write_text(text)

        response = _upload(admin_api, TEST_CERTIFICATE, **{"private_key": "KEY", "chain": "CHAIN",
                                                            field: "x\ud800"})

        assert (response.status_code, response.json()) == (400, {"detail": message})
        assert {path.name: path.read_text() for path in upload_env.certs.iterdir()} == old
        assert list(upload_env.temp.iterdir()) == []

    def test_no_temporary_file_is_made(self, upload_env, admin_api, monkeypatch):
        def refused(*args, **kwargs):
            raise AssertionError("the upload made a temporary file")

        for name in ("NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile", "mkstemp",
                     "mkdtemp", "TemporaryDirectory"):
            monkeypatch.setattr(tempfile, name, refused)
        response = _upload(admin_api, TEST_CERTIFICATE)

        assert response.status_code == 200, response.text
        assert (upload_env.certs / "nas.local.crt").read_text() == TEST_CERTIFICATE
