import logging
import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Name of the generated secret inside the data directory (next to the DB).
JWT_SECRET_FILENAME = "jwt_secret"
# Shorter secrets still work but are logged.
RECOMMENDED_JWT_SECRET_LENGTH = 32
# Secrets this project has published (compose files, docs, examples, tests)
# and common placeholders. Anyone can forge an administrator token with them,
# so the supervisor refuses to start instead of running with one. Compared
# case-insensitively.
INSECURE_JWT_SECRETS = frozenset(
    {
        "change-me-in-production",  # former docker-compose.yml and Settings default
        "your-secret-key",  # former CLAUDE.md example
        "test-secret-key",  # former unit-test conftest, docs/TESTING.md
        "test-secret-key-for-testing",  # former deploy/docker-compose.test.yml
        "secret",
        "changeme",
        "change-me",
        "change_me",
        "password",
        "admin",
    }
)
SUPPORTED_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})


class InsecureConfigurationError(RuntimeError):
    """The configuration would let anyone forge credentials; refuse to start."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UNOS_")

    # Database
    db_path: str = "/data/unos.db"

    # JWT. Leave UNOS_JWT_SECRET unset or empty to use <data dir>/jwt_secret,
    # which is generated on first start (see resolve_jwt_secret).
    jwt_secret: str = Field(default="", repr=False)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # Paths
    smb_conf_path: str = "/etc/samba/smb.conf"
    pools_base_path: str = "/mnt/pools"
    nfs_exports_path: str = "/etc/exports"

    # Docker
    docker_socket: str = "/var/run/docker.sock"
    # The web UI's container, whose Caddy loads the served certificate again (ADR-0402).
    webui_container: str = "unos-webui"

    # App catalog
    app_catalog_path: str = "/data/app-catalog.json"

    # Security - CORS
    # Comma-separated list of allowed origins. Empty means allow same-origin only.
    # For local network NAS, typically left empty (API accessed via Caddy proxy)
    cors_allowed_origins: str = ""

    # Debug mode - enables more permissive CORS for development
    debug_mode: bool = False

    @model_validator(mode="after")
    def _finalize_jwt_settings(self) -> "Settings":
        if self.jwt_algorithm not in SUPPORTED_JWT_ALGORITHMS:
            raise InsecureConfigurationError(
                f"UNOS_JWT_ALGORITHM must be one of {sorted(SUPPORTED_JWT_ALGORITHMS)}"
            )
        self.jwt_secret = resolve_jwt_secret(self.jwt_secret, self.db_path)
        return self


def resolve_jwt_secret(configured: str, db_path: str) -> str:
    """Return the JWT signing secret, failing closed on known placeholders.

    * ``UNOS_JWT_SECRET`` set: used as is, unless it is a published placeholder.
    * unset or empty with an in-memory database (tests): a random per-process
      secret.
    * unset or empty otherwise: ``<directory of db_path>/jwt_secret``, created
      with a random 256-bit value (mode 0600) on first start. It lives in the
      data volume, so it survives restarts and image rebuilds.
    """
    configured = configured.strip()
    if configured:
        _check_secret(
            configured,
            source="UNOS_JWT_SECRET",
            remedy="Unset it to have a random secret generated and stored in the data "
            "directory, or set it to a long random value (e.g. openssl rand -hex 32).",
        )
        return configured
    if db_path.strip() in ("", ":memory:"):
        return secrets.token_hex(32)
    return load_or_create_secret_file(Path(db_path).parent / JWT_SECRET_FILENAME)


def load_or_create_secret_file(path: Path) -> str:
    """Read the secret stored at ``path``, creating it atomically if missing."""
    try:
        return _read_secret_file(path)
    except FileNotFoundError:
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), 0o600)
            fh.write(secret + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            # link() never replaces an existing file: if another process
            # created the secret first, use theirs so both sign alike.
            os.link(tmp, path)
        except FileExistsError:
            return _read_secret_file(path)
        except OSError:
            # Filesystem without hard links: still atomic, just last-writer-wins.
            os.replace(tmp, path)
        _fsync_directory(path.parent)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass

    logger.warning("Generated a new JWT signing secret in %s", path)
    return secret


def _read_secret_file(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise InsecureConfigurationError(
            f"JWT secret file {path} is empty. Delete it to have a new secret generated."
        )
    _check_secret(value, source=str(path), remedy="Delete the file to have a new random secret generated.")
    _restrict_permissions(path)
    return value


def _check_secret(value: str, *, source: str, remedy: str) -> None:
    if value.lower() in INSECURE_JWT_SECRETS:
        raise InsecureConfigurationError(
            f"JWT secret from {source} is a published placeholder value; anyone could "
            f"forge administrator tokens with it. {remedy}"
        )
    if len(value) < RECOMMENDED_JWT_SECRET_LENGTH:
        logger.warning(
            "JWT secret from %s is only %d characters long; use at least %d random characters.",
            source,
            len(value),
            RECOMMENDED_JWT_SECRET_LENGTH,
        )


def _restrict_permissions(path: Path) -> None:
    try:
        if path.stat().st_mode & 0o077:
            os.chmod(path, 0o600)
            logger.warning("Restricted permissions of %s to 0600", path)
    except OSError as exc:
        logger.warning("Could not restrict permissions of %s: %s", path, exc)


def _fsync_directory(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@lru_cache
def get_settings() -> Settings:
    return Settings()
