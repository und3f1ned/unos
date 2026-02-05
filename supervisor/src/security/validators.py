"""
Security validation utilities for UNOS.

This module provides centralized input validation functions to prevent:
- Command injection
- Path traversal
- Arbitrary file access
- Other injection attacks
"""

import re
import os
from pathlib import Path
from typing import Optional
from fastapi import HTTPException

from ..config import get_settings

settings = get_settings()

# Pre-compiled regex patterns for performance
HOSTNAME_PATTERN = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$')
POOL_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$')
VOLUME_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$')
SHARE_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_]{1,64}$')
DISK_NAME_PATTERN = re.compile(r'^[a-z]{2,4}[0-9]*$')  # sda, sdb1, nvme0n1, etc.
NVME_DISK_PATTERN = re.compile(r'^nvme[0-9]+n[0-9]+(p[0-9]+)?$')  # nvme0n1, nvme0n1p1
INTERFACE_NAME_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{0,14}$')  # eth0, enp3s0, etc.

# Reserved SMB share names that should not be used
RESERVED_SMB_NAMES = frozenset({
    'global', 'homes', 'printers', 'print$', 'ipc$', 'admin$', 'c$', 'd$'
})

# List of known valid timezones (subset - full list would be loaded from system)
# This is a fallback validation; primary validation uses timedatectl
TIMEZONE_PATTERN = re.compile(r'^[A-Za-z_]+(/[A-Za-z_]+)*$')


class ValidationError(Exception):
    """Custom validation error with user-friendly message."""
    def __init__(self, message: str, field: str = None):
        self.message = message
        self.field = field
        super().__init__(message)


def validate_hostname(hostname: str) -> str:
    """
    Validate a hostname according to RFC 1123.

    Args:
        hostname: The hostname to validate

    Returns:
        The validated hostname

    Raises:
        HTTPException: If the hostname is invalid
    """
    if not hostname:
        raise HTTPException(status_code=400, detail="Hostname cannot be empty")

    if len(hostname) > 63:
        raise HTTPException(status_code=400, detail="Hostname cannot exceed 63 characters")

    if not HOSTNAME_PATTERN.match(hostname):
        raise HTTPException(
            status_code=400,
            detail="Invalid hostname format. Must start and end with alphanumeric, may contain hyphens"
        )

    return hostname


def validate_timezone(timezone: str, valid_timezones: Optional[set] = None) -> str:
    """
    Validate a timezone string.

    Args:
        timezone: The timezone to validate (e.g., "America/New_York")
        valid_timezones: Optional set of valid timezone names to check against

    Returns:
        The validated timezone

    Raises:
        HTTPException: If the timezone is invalid
    """
    if not timezone:
        raise HTTPException(status_code=400, detail="Timezone cannot be empty")

    if len(timezone) > 64:
        raise HTTPException(status_code=400, detail="Timezone name too long")

    # Basic format validation
    if not TIMEZONE_PATTERN.match(timezone):
        raise HTTPException(status_code=400, detail="Invalid timezone format")

    # Check against valid timezones if provided
    if valid_timezones and timezone not in valid_timezones:
        raise HTTPException(status_code=400, detail="Unknown timezone")

    # Prevent any injection attempts
    if any(c in timezone for c in [';', '&', '|', '$', '`', '\n', '\r', '\x00']):
        raise HTTPException(status_code=400, detail="Invalid characters in timezone")

    return timezone


def validate_disk_name(name: str) -> str:
    """
    Validate a disk device name.

    Args:
        name: The disk name (e.g., "sda", "nvme0n1")

    Returns:
        The validated disk name

    Raises:
        HTTPException: If the name is invalid
    """
    if not name:
        raise HTTPException(status_code=400, detail="Disk name cannot be empty")

    if len(name) > 32:
        raise HTTPException(status_code=400, detail="Disk name too long")

    # Check for path traversal
    if '..' in name or '/' in name:
        raise HTTPException(status_code=400, detail="Invalid disk name")

    # Validate format
    if not (DISK_NAME_PATTERN.match(name) or NVME_DISK_PATTERN.match(name)):
        raise HTTPException(
            status_code=400,
            detail="Invalid disk name format. Expected format like 'sda', 'sdb1', or 'nvme0n1'"
        )

    return name


def validate_pool_name(name: str) -> str:
    """
    Validate a storage pool name.

    Args:
        name: The pool name

    Returns:
        The validated pool name

    Raises:
        HTTPException: If the name is invalid
    """
    if not name:
        raise HTTPException(status_code=400, detail="Pool name cannot be empty")

    if len(name) > 63:
        raise HTTPException(status_code=400, detail="Pool name cannot exceed 63 characters")

    if not POOL_NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=400,
            detail="Invalid pool name. Must start with alphanumeric, may contain letters, numbers, hyphens, and underscores"
        )

    # Check for path traversal
    if '..' in name or '/' in name:
        raise HTTPException(status_code=400, detail="Invalid pool name")

    return name


def validate_volume_name(name: str) -> str:
    """
    Validate a volume (subvolume) name.

    Args:
        name: The volume name

    Returns:
        The validated volume name

    Raises:
        HTTPException: If the name is invalid
    """
    if not name:
        raise HTTPException(status_code=400, detail="Volume name cannot be empty")

    if len(name) > 63:
        raise HTTPException(status_code=400, detail="Volume name cannot exceed 63 characters")

    if not VOLUME_NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=400,
            detail="Invalid volume name. Must start with alphanumeric, may contain letters, numbers, hyphens, and underscores"
        )

    # Check for path traversal
    if '..' in name or '/' in name:
        raise HTTPException(status_code=400, detail="Invalid volume name")

    return name


def validate_share_name(name: str) -> str:
    """
    Validate an SMB share name.

    Args:
        name: The share name

    Returns:
        The validated share name

    Raises:
        HTTPException: If the name is invalid
    """
    if not name:
        raise HTTPException(status_code=400, detail="Share name cannot be empty")

    if len(name) > 64:
        raise HTTPException(status_code=400, detail="Share name cannot exceed 64 characters")

    if not SHARE_NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=400,
            detail="Invalid share name. Must contain only alphanumeric characters and underscores"
        )

    # Check for reserved names
    if name.lower() in RESERVED_SMB_NAMES:
        raise HTTPException(status_code=400, detail=f"'{name}' is a reserved share name")

    return name


def validate_share_path(path: str) -> str:
    """
    Validate an SMB share path and ensure it's within allowed directories.

    Args:
        path: The share path

    Returns:
        The validated and resolved path

    Raises:
        HTTPException: If the path is invalid or outside allowed directories
    """
    if not path:
        raise HTTPException(status_code=400, detail="Share path cannot be empty")

    # Resolve the path to handle any symlinks or relative components
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid share path")

    resolved_str = str(resolved)

    # Ensure path is within pools directory
    pools_base = Path(settings.pools_base_path).resolve()

    try:
        resolved.relative_to(pools_base)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Share path must be within {settings.pools_base_path}"
        )

    # Additional safety checks
    if '..' in path:
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    return resolved_str


def validate_interface_name(name: str) -> str:
    """
    Validate a network interface name.

    Args:
        name: The interface name (e.g., "eth0", "enp3s0")

    Returns:
        The validated interface name

    Raises:
        HTTPException: If the name is invalid
    """
    if not name:
        raise HTTPException(status_code=400, detail="Interface name cannot be empty")

    if len(name) > 15:  # Linux IFNAMSIZ limit
        raise HTTPException(status_code=400, detail="Interface name too long")

    if not INTERFACE_NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=400,
            detail="Invalid interface name format"
        )

    # Prevent injection
    if any(c in name for c in [';', '&', '|', '$', '`', '\n', '\r', '\x00', ' ']):
        raise HTTPException(status_code=400, detail="Invalid characters in interface name")

    return name


def validate_device_path(device: str) -> str:
    """
    Validate a device path (e.g., /dev/sda).

    Args:
        device: The device path

    Returns:
        The validated device path

    Raises:
        HTTPException: If the path is invalid
    """
    if not device:
        raise HTTPException(status_code=400, detail="Device path cannot be empty")

    # Must start with /dev/
    if not device.startswith('/dev/'):
        raise HTTPException(status_code=400, detail="Device path must start with /dev/")

    # Extract the device name
    device_name = device[5:]  # Remove /dev/ prefix

    # Check for path traversal
    if '..' in device_name or device_name.startswith('/'):
        raise HTTPException(status_code=400, detail="Invalid device path")

    # Validate device name
    if not (DISK_NAME_PATTERN.match(device_name) or NVME_DISK_PATTERN.match(device_name)):
        raise HTTPException(status_code=400, detail="Invalid device name format")

    return device


def validate_device_list(devices: list[str]) -> list[str]:
    """
    Validate a list of device paths.

    Args:
        devices: List of device paths

    Returns:
        List of validated device paths

    Raises:
        HTTPException: If any device path is invalid
    """
    if not devices:
        raise HTTPException(status_code=400, detail="Device list cannot be empty")

    validated = []
    for device in devices:
        validated.append(validate_device_path(device))

    return validated


def safe_path_join(base: str, *paths: str) -> Path:
    """
    Safely join paths preventing directory traversal.

    Args:
        base: The base directory
        *paths: Path components to join

    Returns:
        The resolved path

    Raises:
        HTTPException: If the resulting path escapes the base directory
    """
    base_path = Path(base).resolve()

    # Join the paths
    result = base_path
    for p in paths:
        # Check each component for traversal
        if '..' in p or p.startswith('/'):
            raise HTTPException(status_code=400, detail="Invalid path component")
        result = result / p

    # Resolve and verify still within base
    resolved = result.resolve()

    try:
        resolved.relative_to(base_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal detected")

    return resolved


def sanitize_error_message(error: Exception) -> str:
    """
    Sanitize error messages to avoid information disclosure.

    Args:
        error: The exception

    Returns:
        A sanitized error message
    """
    error_str = str(error)

    # Remove sensitive information patterns
    sensitive_patterns = [
        (r'/home/[^/\s]+', '/home/***'),  # Home directories
        (r'/root[^/\s]*', '/***'),  # Root directory
        (r'password[=:]\s*\S+', 'password=***'),  # Passwords
        (r'secret[=:]\s*\S+', 'secret=***'),  # Secrets
        (r'token[=:]\s*\S+', 'token=***'),  # Tokens
        (r'key[=:]\s*\S+', 'key=***'),  # Keys
    ]

    for pattern, replacement in sensitive_patterns:
        error_str = re.sub(pattern, replacement, error_str, flags=re.IGNORECASE)

    # Truncate long error messages
    if len(error_str) > 256:
        error_str = error_str[:256] + '...'

    return error_str
