"""SSH for the off-device copy over SFTP (ADR-0422): UNOS's key pair, the pinned host key and the
command that restic's ``-o sftp.command`` runs.

restic reaches an SFTP server only through the program of ``sftp.command``, which it starts itself
and splits at spaces. That program is the host's ssh (openssh-client, an optional host tool): the
supervisor's image has none. ``host.program_on_host`` runs it in the host's mount and network
namespaces, and the files that it reads, the private key and the known host key, are files of a
folder of the container's TMPDIR (the tmpfs) that ``host.container_path`` names through
/proc/<pid>/root, made for one restic command and removed after it. ``env -i`` starts ssh without
restic's environment, which holds the repository's password. The argv is fixed: no
configuration file (``-F /dev/null``: neither ~/.ssh/config nor /etc/ssh/ssh_config), public keys
only and never a password or a prompt, no agent, forwarding or local command, the known host key
under HostKeyAlias only, StrictHostKeyChecking yes but at the first test of a server (accept-new,
into an empty file, which then holds the key to pin). Every part of it is a word that restic's
splitting keeps whole, which ``command`` checks.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .. import host

SSH = "/usr/bin/ssh"
# ssh starts with an empty environment: restic's holds the repository's password.
ENV = "/usr/bin/env"
# The name under which the known_hosts file of each command holds the server's key, whatever its
# host and port are.
HOST_KEY_ALIAS = "unos-copy-target"
KEY_COMMENT = "unos-off-device-copy"
# The host key types that a pin takes, with the host key algorithms that ssh then offers for it.
HOST_KEY_ALGORITHMS = {
    "ssh-ed25519": "ssh-ed25519",
    "ecdsa-sha2-nistp256": "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384": "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521": "ecdsa-sha2-nistp521",
    "ssh-rsa": "rsa-sha2-512,rsa-sha2-256",
}
# Seconds: the connection's timeout, and the keepalives that end ssh within a minute of a server
# that stopped answering (restic's own timeout ends restic, whose end closes ssh's input).
CONNECT_TIMEOUT = 10
ALIVE_INTERVAL = 15
ALIVE_COUNT = 4
# What restic's splitting of sftp.command keeps as one word: no space, quote or backslash.
_WORD = re.compile(r"[A-Za-z0-9_./:=,@+-]+")
_BASE64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_FINGERPRINT = re.compile(r"SHA256:[A-Za-z0-9+/]{43}=?")


def new_private_key() -> str:
    """A new Ed25519 private key in OpenSSH's format, unencrypted: the secrets file keeps it, mode 0600."""
    return Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()
    ).decode()


def public_key(private: str) -> str | None:
    """The public key of PRIVATE as an authorized_keys line, with UNOS's comment; None for a key that
    cannot be read."""
    try:
        key = serialization.load_ssh_private_key(private.encode(), password=None)
        line = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    except (ValueError, TypeError, UnicodeError):
        return None
    return f"{line.decode()} {KEY_COMMENT}"


def host_key(value: object) -> str | None:
    """VALUE as a host key to pin, "<type> <base64>", whose key blob names the same type; None
    otherwise."""
    if not isinstance(value, str):
        return None
    parts = value.split(" ")
    if len(parts) != 2 or parts[0] not in HOST_KEY_ALGORITHMS or not _BASE64.fullmatch(parts[1]):
        return None
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError):
        return None
    size = int.from_bytes(blob[:4], "big")
    if len(blob) < 4 + size or blob[4:4 + size] != parts[0].encode():
        return None
    return value


def fingerprint(key: str) -> str:
    """The fingerprint of the host key KEY as ``ssh-keygen -l`` prints it: SHA256: and the base64 of
    the digest of its blob, without padding."""
    blob = base64.b64decode(key.split(" ")[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def fingerprint_text(value: object) -> str | None:
    """VALUE as a typed SHA256 fingerprint, without the padding; None when it is not one."""
    if not isinstance(value, str) or not _FINGERPRINT.fullmatch(value.strip()):
        return None
    return value.strip().rstrip("=")


def known_host(text: str) -> str | None:
    """The host key that ssh wrote under HOST_KEY_ALIAS into a known_hosts file, or None."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == HOST_KEY_ALIAS:
            return host_key(f"{parts[1]} {parts[2]}")
    return None


def known_hosts_line(key: str) -> str:
    return f"{HOST_KEY_ALIAS} {key}\n"


def command(*, server: str, port: int, user: str, identity: str, known_hosts: str,
            pinned: str | None) -> list[str]:
    """The argv of the host's ssh that opens the SFTP subsystem of USER at SERVER:PORT with the key
    of the file IDENTITY, the host key in the file KNOWN_HOSTS: PINNED, which ssh holds to
    (StrictHostKeyChecking yes, its algorithms only), or, at a server's first test, none: then ssh
    writes the key it meets there (accept-new). IDENTITY and KNOWN_HOSTS are paths of this
    container. ValueError for a part that restic's splitting would not keep whole: the values are
    checked before, and a stored file that was edited by hand is refused there too."""
    options = [
        "BatchMode=yes",
        f"StrictHostKeyChecking={'yes' if pinned else 'accept-new'}",
        f"UserKnownHostsFile={host.container_path(known_hosts)}",
        "GlobalKnownHostsFile=/dev/null",
        f"HostKeyAlias={HOST_KEY_ALIAS}",
        *([f"HostKeyAlgorithms={HOST_KEY_ALGORITHMS[pinned.split(' ')[0]]}"] if pinned else []),
        "UpdateHostKeys=no",
        "CheckHostIP=no",
        f"IdentityFile={host.container_path(identity)}",
        "IdentitiesOnly=yes",
        "IdentityAgent=none",
        "PreferredAuthentications=publickey",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "ForwardAgent=no",
        "ForwardX11=no",
        "ClearAllForwardings=yes",
        "PermitLocalCommand=no",
        "ControlMaster=no",
        "ControlPath=none",
        f"ConnectTimeout={CONNECT_TIMEOUT}",
        f"ServerAliveInterval={ALIVE_INTERVAL}",
        f"ServerAliveCountMax={ALIVE_COUNT}",
        "LogLevel=ERROR",
    ]
    argv = [ENV, "-i", SSH, "-F", "/dev/null"]
    for item in options:
        argv += ["-o", item]
    argv += ["-p", str(port), "-l", user, "-s", "--", server, "sftp"]
    argv = host.program_on_host(argv)
    if not all(_WORD.fullmatch(part) for part in argv) or server.startswith("-") or user.startswith("-"):
        raise ValueError("a part of the ssh command is not a plain word")
    return argv
