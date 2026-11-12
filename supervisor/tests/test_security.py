import asyncio
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
        """The status names what browsers get: Caddy's local CA, whatever is stored (ADR-0356)."""
        from src.security.schemas import Certificate, CertificateStatus, CertificateType

        assert CertificateStatus().model_dump(mode="json") == {"served_by": "caddy_local_ca"}
        stored = Certificate(id="custom_nas.local", type=CertificateType.CUSTOM, domain="nas.local")
        assert stored.served is False
        assert "auto_renew" not in stored.model_dump()

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

    def test_nothing_reads_caddys_certificates(self):
        """caddy-data is mounted only into unos-webui (deploy/docker-compose.yml): the supervisor
        has no directory of Caddy's certificates to read, and lists none (ADR-0356)."""
        from src.security import router

        assert not hasattr(router, "CADDY_CERTS_DIR")
        assert not hasattr(router, "get_caddy_certificates")


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

        assert [t.value for t in CertificateType] == ["self_signed", "custom"]


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


# What the fake openssl of ``certs`` prints: a new key, then its certificate. The key's first line
# is split: the secret scan of test_repo_tooling.py takes it for a real one.
GENERATED_KEY = "-----BEGIN PRIV" + "ATE KEY-----\nKEY\n-----END PRIVATE KEY-----\n"
GENERATED_CERTIFICATE = "-----BEGIN CERTIFICATE-----\nCERT\n-----END CERTIFICATE-----\n"


@pytest.fixture
def certs(tmp_path, monkeypatch):
    """CERTS_DIR in a scratch tree; openssl replaced by a fake that prints a key and a certificate."""
    certs_dir = tmp_path / "data" / "certs"
    monkeypatch.setattr("src.security.router.CERTS_DIR", str(certs_dir))
    calls = []

    def fake_openssl(cmd, **kwargs):
        # Like the real one with -keyout -: the key on its standard output, then the certificate,
        # there too, or in the file that -out names, through a symbolic link at its path.
        assert cmd[:2] == ["openssl", "req"], cmd
        calls.append(cmd)
        assert cmd[cmd.index("-keyout") + 1] == "-", cmd
        if "-out" in cmd:
            Path(cmd[cmd.index("-out") + 1]).write_text(GENERATED_CERTIFICATE)
            return subprocess.CompletedProcess(cmd, 0, GENERATED_KEY, "")
        return subprocess.CompletedProcess(cmd, 0, GENERATED_KEY + GENERATED_CERTIFICATE, "")

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
        assert cmd[cmd.index("-keyout") + 1] == "-"
        # openssl prints the certificate too: the supervisor writes both files (ADR-0042).
        assert "-out" not in cmd
        assert cmd[cmd.index("-subj") + 1] == "/CN=nas.local"
        assert cmd[cmd.index("-addext") + 1] == "subjectAltName=DNS:nas.local"
        assert _files_under(certs.root) == ["data/certs/nas.local.crt", "data/certs/nas.local.key"]
        assert (certs.dir / "nas.local.crt").read_text() == GENERATED_CERTIFICATE
        assert (certs.dir / "nas.local.key").read_text() == GENERATED_KEY

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
                                           "*.example.com.chain.pem"]
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
        for suffix in (".crt", ".key", ".chain.pem"):
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

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "Invalid certificate id"
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
            f"data/certs/{common_name}{suffix}" for suffix in (".chain.pem", ".crt", ".key")
        )


# ==================== Certificate name length (ADV-7) ====================

# Longest name a certificate is stored under: "<name>.chain.pem" is then
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
    """Each file of a certificate (<name>.crt, .key, .chain.pem) must fit in NAME_MAX bytes,
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

    # Real openssl refuses a CN over 64 characters only after making the key,
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
            f"data/certs/{common_name}{suffix}" for suffix in (".chain.pem", ".crt", ".key")
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

    def test_a_new_upload_replaces_the_whole_key_file(self, certs, admin_api, monkeypatch):
        # A renewal under the same name, with a shorter key: nothing of the old one stays.
        certs.dir.mkdir(parents=True)
        (certs.dir / "nas.local.key").write_text("A LONGER FIRST KEY")
        (certs.dir / "nas.local.key").chmod(0o600)

        assert _upload_as(monkeypatch, admin_api, "nas.local").status_code == 200

        assert (certs.dir / "nas.local.key").read_text() == "KEY"
        assert stat.S_IMODE((certs.dir / "nas.local.key").stat().st_mode) == 0o600

    def test_uploaded_key_file_is_0600(self, certs, admin_api, monkeypatch):
        assert _upload_as(monkeypatch, admin_api, "nas.local").status_code == 200

        key_file = certs.dir / "nas.local.key"
        assert key_file.read_text() == "KEY"
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

    @pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command")
    def test_self_signed_key_file_is_0600(self, upload_env, admin_api):
        """The real openssl prints the new key, and _write_private_key stores it as it stores an
        uploaded one: 0600 under the usual umask, whatever mode openssl would give a file of its
        own (LibreSSL's are 0644). UNOS writes no key of another mode that would need a fix-up
        (security.no-legacy-certificate-fixups, ADR-0207)."""
        response = admin_api.post(f"{API}/self-signed", json={"domain": "nas.local"})

        assert response.status_code == 200, response.text
        key_file = upload_env.certs / "nas.local.key"
        subprocess.run(["openssl", "pkey", "-in", str(key_file), "-noout"], check=True, timeout=30)
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

    def test_no_key_mode_is_fixed_up(self):
        """The fix-ups for the keys that older versions left 0644 are gone
        (security.no-legacy-certificate-fixups, ADR-0207)."""
        import inspect

        from src.security import router

        for function in (router._write_private_key, router.generate_self_signed_certificate,
                         router.upload_certificate, router.delete_certificate, router.get_custom_certificates):
            source = inspect.getsource(function)
            assert "chmod" not in source and "older version" not in source, function.__name__


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
        # A refused upload makes nothing, not even the directory of the certificates (ADR-0214).
        assert not upload_env.certs.exists()
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
        # A refused upload makes nothing, not even the directory of the certificates (ADR-0214).
        assert not upload_env.certs.exists()
        assert list(upload_env.temp.iterdir()) == []

    @pytest.mark.parametrize("field,message", [
        ("private_key", "Invalid private key format"),
        ("chain", "Invalid certificate chain format"),
    ])
    def test_key_or_chain_that_cannot_be_encoded_changes_nothing(self, upload_env, admin_api,
                                                                  field, message):
        # They used to fail half-way: the certificate replaced, the old key emptied.
        upload_env.certs.mkdir(parents=True)
        old = {f"nas.local{suffix}": f"old {suffix}" for suffix in (".crt", ".key", ".chain.pem")}
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


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command")
class TestStoredCertificates:
    """What a failed generation keeps, and what the listing shows and the delete takes, with the
    real openssl."""

    def test_a_failed_self_signed_certificate_leaves_the_stored_key(self, upload_env, admin_api):
        # openssl refuses a validity whose end it cannot write (after the year 9999) only after it
        # has made the new key, which it wrote over the stored key when it wrote the key file
        # itself. (A country of three letters, which it refuses the same way, is refused before
        # openssl runs: TestSelfSignedSubjectValidation.)
        assert _upload(admin_api, TEST_CERTIFICATE).status_code == 200

        response = admin_api.post(f"{API}/self-signed", json={"domain": "nas.local",
                                                              "validity_days": 99999999})

        assert response.status_code == 500, response.text
        assert (upload_env.certs / "nas.local.key").read_text() == "KEY"
        assert (upload_env.certs / "nas.local.crt").read_text() == TEST_CERTIFICATE

    def test_every_listed_certificate_can_be_deleted(self, upload_env, admin_api):
        """A chain file is no certificate of its own, nor is a file under a name that UNOS does not
        store (security.no-legacy-certificate-fixups, ADR-0207): the listing shows the
        certificates whose ids the delete takes, and only those."""
        assert admin_api.post(f"{API}/self-signed", json={"domain": "fd00::1"}).status_code == 200
        ipv6 = (upload_env.certs / "fd00::1.crt").read_text()
        assert _upload(admin_api, ipv6, chain=TEST_CERTIFICATE).status_code == 200
        assert _upload(admin_api, TEST_CERTIFICATE, chain=TEST_CERTIFICATE).status_code == 200
        (upload_env.certs / "My NAS.crt").write_text(TEST_CERTIFICATE)

        listed = [cert["id"] for cert in admin_api.get(API).json()["certificates"]]

        assert sorted(listed) == ["custom_fd00::1", "custom_nas.local"]
        for cert_id in listed:
            assert admin_api.delete(f"{API}/{cert_id}").status_code == 200, cert_id
        assert sorted(path.name for path in upload_env.certs.iterdir()) == ["My NAS.crt"]


# ==================== What browsers get (security.custom-certificates, ADR-0356) ====================

@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command")
class TestCertificateStatusNamesWhatIsServed:
    """Caddy serves only its local CA: nothing hands stored certificates to the web UI's
    container (deploy/docker-compose.yml mounts caddy-data only there). The status says so, and a
    stored certificate is listed as stored, not served (ADR-0356)."""

    def test_without_a_stored_certificate(self, upload_env, admin_api):
        assert admin_api.get(f"{API}/status").json() == {"served_by": "caddy_local_ca"}
        assert admin_api.get(API).json() == {"certificates": [], "served_by": "caddy_local_ca"}

    def test_a_stored_certificate_is_not_named_as_served(self, upload_env, admin_api):
        # A valid certificate, which the status used to name as the active one.
        assert _upload(admin_api, TEST_CERTIFICATE).status_code == 200

        assert admin_api.get(f"{API}/status").json() == {"served_by": "caddy_local_ca"}
        listing = admin_api.get(API).json()
        assert listing["served_by"] == "caddy_local_ca"
        [cert] = listing["certificates"]
        assert (cert["id"], cert["is_valid"], cert["served"]) == ("custom_nas.local", True, False)
        assert set(cert) == {"id", "type", "domain", "issuer", "valid_from", "valid_until",
                             "is_valid", "served", "fingerprint"}


def _on_event_loop() -> bool:
    """Whether the caller runs on an event loop (a worker thread of asyncio.to_thread has none)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class TestOpensslRunsOffTheEventLoop:
    """openssl runs in a worker thread, with a timeout (ADR-0021)."""

    @pytest.fixture
    def openssl(self, certs, monkeypatch):
        """A fake openssl that records where it ran and with which timeout."""
        runs = []

        def fake(cmd, **kwargs):
            runs.append((cmd[1], _on_event_loop(), kwargs.get("timeout")))
            if cmd[1] == "req":
                return subprocess.CompletedProcess(cmd, 0, GENERATED_KEY + GENERATED_CERTIFICATE, "")
            return subprocess.CompletedProcess(cmd, 0, "subject=CN = nas.local\nissuer=CN = nas.local\n", "")

        monkeypatch.setattr(subprocess, "run", fake)
        return runs

    def test_generation_upload_and_listing(self, certs, openssl, admin_api):
        assert admin_api.post(f"{API}/self-signed", json={"domain": "nas.local"}).status_code == 200
        assert admin_api.post(f"{API}/upload", json={"certificate": "PEM", "private_key": "KEY"}).status_code == 200
        assert [c["id"] for c in admin_api.get(API).json()["certificates"]] == ["custom_nas.local"]

        assert {command for command, _, _ in openssl} == {"req", "x509"}
        assert [(command, on_loop) for command, on_loop, _ in openssl if on_loop] == []
        assert {(command, timeout) for command, _, timeout in openssl} == {("req", 60), ("x509", 10)}

    @pytest.mark.parametrize("error,message", [
        (subprocess.TimeoutExpired(["openssl"], 60), "openssl did not finish within 60 seconds"),
        (FileNotFoundError(errno.ENOENT, "No such file or directory", "openssl"),
         "[Errno 2] No such file or directory: 'openssl'"),
    ], ids=["timeout", "raises"])
    def test_a_generation_whose_openssl_fails_changes_nothing(self, certs, admin_api, monkeypatch,
                                                              error, message):
        certs.dir.mkdir(parents=True)
        old = {"nas.local.crt": "old certificate", "nas.local.key": "old key"}
        for name, text in old.items():
            (certs.dir / name).write_text(text)

        def fail(cmd, **kwargs):
            raise error

        monkeypatch.setattr(subprocess, "run", fail)
        response = admin_api.post(f"{API}/self-signed", json={"domain": "nas.local"})

        assert (response.status_code, response.json()) == (
            500, {"detail": f"Failed to generate certificate: {message}"})
        assert {path.name: path.read_text() for path in certs.dir.iterdir()} == old

    def test_an_upload_whose_openssl_times_out_is_no_invalid_certificate(self, certs, admin_api,
                                                                         monkeypatch):
        def slow(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        monkeypatch.setattr(subprocess, "run", slow)
        response = admin_api.post(f"{API}/upload", json={"certificate": "PEM", "private_key": "KEY"})

        assert (response.status_code, response.json()) == (
            500, {"detail": "Failed to upload certificate: openssl did not finish within 10 seconds"})
        assert _files_under(certs.root) == []

    def test_a_listing_whose_openssl_times_out_says_so(self, certs, admin_api, monkeypatch):
        # Not an empty list: the stored certificate is there, its details are not known.
        certs.dir.mkdir(parents=True)
        (certs.dir / "nas.local.crt").write_text("CERT")

        def slow(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        monkeypatch.setattr(subprocess, "run", slow)
        response = admin_api.get(API)

        assert (response.status_code, response.json()) == (
            500, {"detail": "Failed to read the stored certificates: openssl did not finish within 10 seconds"})


class TestCertificateFilesFollowNoLink:
    """A symbolic link at a certificate's file path is not followed: the write fails and the
    link's target stays as it was (ADR-0042)."""

    @pytest.fixture
    def victim(self, certs):
        victim = certs.root / "victim"
        victim.write_text("keep me")
        certs.dir.mkdir(parents=True)
        return victim

    @pytest.mark.parametrize("suffix", [".crt", ".key", ".chain.pem"])
    def test_upload(self, certs, admin_api, monkeypatch, victim, suffix):
        (certs.dir / f"nas.local{suffix}").symlink_to(victim)

        response = _upload_as(monkeypatch, admin_api, "nas.local")

        assert response.status_code == 500, response.text
        assert response.json()["detail"].startswith("Failed to upload certificate: ")
        assert victim.read_text() == "keep me"
        assert (certs.dir / f"nas.local{suffix}").is_symlink()

    @pytest.mark.parametrize("suffix", [".crt", ".key"])
    def test_self_signed(self, certs, admin_api, victim, suffix):
        (certs.dir / f"nas.local{suffix}").symlink_to(victim)

        response = admin_api.post(f"{API}/self-signed", json={"domain": "nas.local"})

        assert response.status_code == 500, response.text
        assert response.json()["detail"].startswith("Failed to generate certificate: ")
        assert victim.read_text() == "keep me"
        assert (certs.dir / f"nas.local{suffix}").is_symlink()


class TestSelfSignedSubjectValidation:
    """Organization and country of a self-signed certificate are checked before openssl runs,
    which refuses a longer organization or another country only after it has made the key."""

    @pytest.mark.parametrize("field,value", [
        ("organization", "x" * 65),
        ("organization", "ä" * 65),
        ("country", "DEU"),
        ("country", "D"),
        ("country", "12"),
        ("country", "D1"),
        ("country", ""),
        ("country", "DE\n"),
        ("country", "ÄÖ"),
    ])
    def test_refused_with_422_before_openssl(self, certs, admin_api, field, value):
        response = admin_api.post(f"{API}/self-signed", json={"domain": "nas.local", field: value})

        assert response.status_code == 422, response.text
        assert certs.calls == []
        assert _files_under(certs.root) == []

    @pytest.mark.parametrize("organization,country", [
        ("x" * 64, "DE"), ("Home/Lab", "de"), (None, None), ("", None),
    ])
    def test_accepted(self, certs, admin_api, organization, country):
        response = admin_api.post(f"{API}/self-signed", json={
            "domain": "nas.local", "organization": organization, "country": country,
        })

        assert response.status_code == 200, response.text
        [cmd] = certs.calls
        subject = "/CN=nas.local"
        if organization:
            subject += "/O=" + organization.replace("/", "\\/")
        if country:
            subject += f"/C={country}"
        assert cmd[cmd.index("-subj") + 1] == subject

    @pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command")
    def test_an_organization_of_64_letters_beyond_ascii(self, upload_env, admin_api):
        """openssl counts characters as the check does only with -utf8: without it, every byte
        of UTF-8 counted, and 64 such letters failed after the key was made."""
        organization = "ä" * 64
        response = admin_api.post(f"{API}/self-signed", json={
            "domain": "nas.local", "organization": organization, "country": "DE",
        })

        assert response.status_code == 200, response.text
        subject = subprocess.run(
            ["openssl", "x509", "-in", str(upload_env.certs / "nas.local.crt"), "-noout",
             "-subject", "-nameopt", "RFC2253,-esc_msb"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout
        assert f"O={organization}" in subject


class TestCertificateErrorAnswers:
    """The answers of the certificate API's error paths."""

    def test_a_delete_of_a_certificate_that_is_not_stored_here(self, certs, admin_api):
        response = admin_api.delete(f"{API}/caddy_unos")

        assert (response.status_code, response.json()) == (
            400, {"detail": "Can only delete custom certificates"})

    def test_an_upload_that_cannot_be_written(self, certs, admin_api, monkeypatch):
        def full(path, key):
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

        monkeypatch.setattr("src.security.router._write_private_key", full)
        response = _upload_as(monkeypatch, admin_api, "nas.local")

        assert (response.status_code, response.json()) == (
            500, {"detail": f"Failed to upload certificate: [Errno {errno.ENOSPC}] {os.strerror(errno.ENOSPC)}"})

    def test_a_delete_that_cannot_remove_the_files(self, certs, admin_api, monkeypatch):
        certs.dir.mkdir(parents=True)
        (certs.dir / "nas.local.crt").write_text("CERT")

        def refused(self, missing_ok=False):
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES))

        monkeypatch.setattr(Path, "unlink", refused)
        response = admin_api.delete(f"{API}/custom_nas.local")

        assert (response.status_code, response.json()) == (
            500, {"detail": f"Failed to delete certificate: [Errno {errno.EACCES}] {os.strerror(errno.EACCES)}"})
        assert (certs.dir / "nas.local.crt").read_text() == "CERT"
