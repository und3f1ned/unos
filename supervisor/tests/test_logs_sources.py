"""GET /api/v1/logs/sources on a host without systemctl.

The journal and the kernel log are always offered; a missing systemctl
(e.g. the deviceless test image, supervisor/Dockerfile.test) only means that
the docker and caddy sources cannot be checked, not that no source exists.
"""

import asyncio
import subprocess
from unittest.mock import patch

from src.logs.router import get_log_sources


def _no_systemctl(cmd, *args, **kwargs):
    if cmd and cmd[0] == "systemctl":
        raise FileNotFoundError(2, "No such file or directory", "systemctl")
    return subprocess.CompletedProcess(cmd, 0, "", "")


def test_sources_without_systemctl():
    with patch("src.logs.router.subprocess.run", side_effect=_no_systemctl):
        result = asyncio.run(get_log_sources(_={}))
    available = {s.name: s.available for s in result["sources"]}
    assert available["journal"] is True
    assert available["dmesg"] is True
    assert available["docker"] is False
