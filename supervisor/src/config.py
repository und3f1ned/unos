from pydantic_settings import BaseSettings
from functools import lru_cache


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

    class Config:
        env_prefix = "UNOS_"


@lru_cache
def get_settings() -> Settings:
    return Settings()
