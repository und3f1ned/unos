from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    # Database
    db_path: str = "/data/unos.db"

    # JWT
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # Paths
    smb_conf_path: str = "/etc/samba/smb.conf"
    pools_base_path: str = "/mnt/pools"
    nfs_exports_path: str = "/etc/exports"

    # Docker
    docker_socket: str = "/var/run/docker.sock"

    # App catalog
    app_catalog_path: str = "/data/app-catalog.json"

    # Security - CORS
    # Comma-separated list of allowed origins. Empty means allow same-origin only.
    # For local network NAS, typically left empty (API accessed via Caddy proxy)
    cors_allowed_origins: str = ""

    # Debug mode - enables more permissive CORS for development
    debug_mode: bool = False

    class Config:
        env_prefix = "UNOS_"


@lru_cache
def get_settings() -> Settings:
    return Settings()
