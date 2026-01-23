from pydantic import BaseModel


class DiskInfo(BaseModel):
    name: str
    path: str
    model: str | None = None
    serial: str | None = None
    size: int
    rotational: bool
    mountpoint: str | None = None
    type: str


class SmartData(BaseModel):
    device: str
    healthy: bool
    temperature: int | None = None
    power_on_hours: int | None = None
    reallocated_sectors: int | None = None
    raw_data: dict | None = None


class PoolCreate(BaseModel):
    name: str
    devices: list[str]
    raid_level: str = "single"  # single, raid0, raid1, raid5, raid6, raid10


class PoolInfo(BaseModel):
    name: str
    uuid: str | None = None
    devices: list[str]
    total_size: int
    used_size: int
    mountpoint: str | None = None
    raid_level: str | None = None


class VolumeCreate(BaseModel):
    pool_name: str
    name: str
    quota: int | None = None  # bytes, None = no quota


class VolumeInfo(BaseModel):
    name: str
    pool: str
    path: str
    used: int | None = None
    quota: int | None = None
