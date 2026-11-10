"""JWT secret bootstrap: generated once, persisted 0600, never a public default."""
import logging
import os
import re
import stat
from pathlib import Path

import jwt
import pytest

from src.config import (
    INSECURE_JWT_SECRETS,
    InsecureConfigurationError,
    JWT_SECRET_FILENAME,
    RECOMMENDED_JWT_SECRET_LENGTH,
    Settings,
    get_settings,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
# Set by conftest before any test module is imported.
SUITE_SECRET = os.environ["UNOS_JWT_SECRET"]


@pytest.fixture
def no_env_secret(monkeypatch):
    """Simulate a deployment that does not set UNOS_JWT_SECRET."""
    monkeypatch.delenv("UNOS_JWT_SECRET", raising=False)


@pytest.fixture
def data_db(tmp_path):
    # The data directory does not exist yet, like a fresh volume subpath.
    return tmp_path / "data" / "unos.db"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class TestGeneratedSecret:
    def test_generated_on_first_start_with_mode_0600(self, no_env_secret, data_db):
        settings = Settings(db_path=str(data_db))

        secret_file = data_db.parent / JWT_SECRET_FILENAME
        assert secret_file.is_file()
        assert _mode(secret_file) == 0o600
        assert secret_file.read_text().strip() == settings.jwt_secret
        assert len(settings.jwt_secret) == 64
        int(settings.jwt_secret, 16)  # hex
        # No temporary files are left next to it.
        assert sorted(p.name for p in secret_file.parent.iterdir()) == [JWT_SECRET_FILENAME]

    def test_reused_across_restarts(self, no_env_secret, data_db):
        first = Settings(db_path=str(data_db)).jwt_secret
        second = Settings(db_path=str(data_db)).jwt_secret

        assert first == second

    def test_empty_env_value_means_generated(self, monkeypatch, data_db):
        # docker-compose passes UNOS_JWT_SECRET=${JWT_SECRET:-}, i.e. "" when unset.
        monkeypatch.setenv("UNOS_JWT_SECRET", "")
        settings = Settings(db_path=str(data_db))

        assert settings.jwt_secret == (data_db.parent / JWT_SECRET_FILENAME).read_text().strip()

    def test_existing_file_permissions_are_tightened(self, no_env_secret, data_db):
        secret_file = data_db.parent / JWT_SECRET_FILENAME
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text("a" * 64 + "\n")
        os.chmod(secret_file, 0o644)

        settings = Settings(db_path=str(data_db))

        assert settings.jwt_secret == "a" * 64
        assert _mode(secret_file) == 0o600

    def test_concurrent_creator_wins(self, no_env_secret, data_db, monkeypatch):
        """If another process creates the file first, its secret is used."""
        secret_file = data_db.parent / JWT_SECRET_FILENAME
        real_link = os.link

        def racing_link(src, dst, *args, **kwargs):
            Path(dst).write_text("b" * 64 + "\n")
            return real_link(src, dst, *args, **kwargs)  # raises FileExistsError

        monkeypatch.setattr("src.config.os.link", racing_link)
        settings = Settings(db_path=str(data_db))

        assert settings.jwt_secret == "b" * 64
        assert sorted(p.name for p in secret_file.parent.iterdir()) == [JWT_SECRET_FILENAME]

    def test_in_memory_database_uses_ephemeral_secret(self, no_env_secret, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        first = Settings(db_path=":memory:").jwt_secret
        second = Settings(db_path=":memory:").jwt_secret

        assert len(first) == 64 and first != second
        assert list(tmp_path.iterdir()) == []

    def test_secret_not_in_repr(self, no_env_secret, data_db):
        settings = Settings(db_path=str(data_db))

        assert settings.jwt_secret not in repr(settings)


class TestConfiguredSecret:
    def test_env_secret_overrides_file(self, monkeypatch, data_db):
        monkeypatch.setenv("UNOS_JWT_SECRET", "x" * 40)
        settings = Settings(db_path=str(data_db))

        assert settings.jwt_secret == "x" * 40
        assert not (data_db.parent / JWT_SECRET_FILENAME).exists()

    @pytest.mark.parametrize(
        "value",
        sorted(INSECURE_JWT_SECRETS) + ["CHANGE-ME-IN-PRODUCTION", "  changeme  "],
    )
    def test_known_placeholders_are_refused(self, monkeypatch, data_db, value):
        monkeypatch.setenv("UNOS_JWT_SECRET", value)

        with pytest.raises(InsecureConfigurationError, match="placeholder"):
            Settings(db_path=str(data_db))

    def test_all_required_placeholders_are_listed(self):
        assert {
            "change-me-in-production", "your-secret-key", "secret", "changeme", "change-me",
            # Published by the test setup: conftest/docs and the old test compose file.
            "test-secret-key", "test-secret-key-for-testing",
        } <= INSECURE_JWT_SECRETS

    @pytest.mark.parametrize("value", ["test-secret-key", "TEST-SECRET-KEY-FOR-TESTING "])
    def test_published_test_secrets_are_refused(self, monkeypatch, data_db, value):
        monkeypatch.setenv("UNOS_JWT_SECRET", value)

        with pytest.raises(InsecureConfigurationError, match="placeholder"):
            Settings(db_path=str(data_db))

    def test_suite_uses_a_random_secret(self):
        assert SUITE_SECRET.lower() not in INSECURE_JWT_SECRETS
        assert len(SUITE_SECRET) >= RECOMMENDED_JWT_SECRET_LENGTH
        assert get_settings().jwt_secret == SUITE_SECRET

    def test_placeholder_in_secret_file_is_refused(self, no_env_secret, data_db):
        secret_file = data_db.parent / JWT_SECRET_FILENAME
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text("change-me-in-production\n")

        with pytest.raises(InsecureConfigurationError):
            Settings(db_path=str(data_db))

    def test_empty_secret_file_is_refused(self, no_env_secret, data_db):
        secret_file = data_db.parent / JWT_SECRET_FILENAME
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text("\n")

        with pytest.raises(InsecureConfigurationError):
            Settings(db_path=str(data_db))

    def test_short_secret_is_accepted_with_warning(self, monkeypatch, data_db, caplog):
        monkeypatch.setenv("UNOS_JWT_SECRET", "short-local-key")
        with caplog.at_level(logging.WARNING, logger="src.config"):
            settings = Settings(db_path=str(data_db))

        assert settings.jwt_secret == "short-local-key"
        assert "only 15 characters" in caplog.text
        assert "short-local-key" not in caplog.text

    def test_unsupported_algorithm_is_refused(self, monkeypatch, data_db):
        monkeypatch.setenv("UNOS_JWT_SECRET", "x" * 40)
        monkeypatch.setenv("UNOS_JWT_ALGORITHM", "none")

        with pytest.raises(InsecureConfigurationError):
            Settings(db_path=str(data_db))


class TestTokensFollowCurrentSettings:
    @pytest.fixture
    def rotated_settings(self, monkeypatch):
        monkeypatch.setenv("UNOS_JWT_SECRET", "rotated-" + "r" * 40)
        get_settings.cache_clear()
        yield get_settings()
        # Rebuilt from the restored environment on next use.
        get_settings.cache_clear()

    def test_tokens_are_signed_with_the_current_secret(self, rotated_settings):
        from src.auth.utils import create_access_token, decode_access_token

        token = create_access_token({"sub": "alice"})

        assert jwt.decode(token, rotated_settings.jwt_secret, algorithms=["HS256"])["sub"] == "alice"
        assert decode_access_token(token)[0] == "alice"
        with pytest.raises(jwt.InvalidSignatureError):
            jwt.decode(token, SUITE_SECRET, algorithms=["HS256"])

    def test_token_forged_with_old_default_is_rejected(self, rotated_settings):
        from src.auth.utils import decode_access_token

        forged = jwt.encode({"sub": "admin", "exp": 4102444800}, "change-me-in-production", algorithm="HS256")

        with pytest.raises(jwt.InvalidSignatureError):
            decode_access_token(forged)

    def test_tokens_without_expiry_are_rejected(self, rotated_settings):
        from src.auth.utils import decode_access_token

        token = jwt.encode({"sub": "admin"}, rotated_settings.jwt_secret, algorithm="HS256")

        with pytest.raises(jwt.MissingRequiredClaimError):
            decode_access_token(token)


class TestDeploymentDefaults:
    def test_compose_has_no_public_default_secret(self):
        compose = (REPO_ROOT / "deploy" / "docker-compose.yml").read_text()

        assert "UNOS_JWT_SECRET=${JWT_SECRET:-}" in compose
        for placeholder in INSECURE_JWT_SECRETS:
            assert f":-{placeholder}}}" not in compose

    @pytest.mark.parametrize("path", [
        ".github/workflows/test.yml",
        "deploy/docker-compose.yml",
        "deploy/docker-compose.test.yml",
        "scripts/test-cycle.sh",
    ])
    def test_ci_and_test_stacks_set_no_published_secret(self, path):
        text = (REPO_ROOT / path).read_text()
        # JWT_SECRET=value, JWT_SECRET: value, "JWT_SECRET": "value", ${JWT_SECRET:-value}
        values = re.findall(r"JWT_SECRET[\"']?\s*[:=]\s*[\"']?([^\"'\s}#]*)", text)
        values += re.findall(r"\$\{(?:UNOS_)?JWT_SECRET:?-([^}]*)\}", text)
        for value in values:
            assert value.strip().lower() not in INSECURE_JWT_SECRETS, f"{path} sets {value!r}"
