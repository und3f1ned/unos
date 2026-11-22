"""The rate limiters of the API: each module's limiter counts per client address (ADR-0010), the
address that uvicorn takes from X-Forwarded-For only on connections from the web UI (ADR-0048).

slowapi's Limiter takes RATELIMIT_ENABLED and RATELIMIT_STORAGE_URL from the environment and from a
.env file in the working directory (starlette's Config). client_limiter gives the storage and sets
enabled after building the limiter, so that no setting of a NAS turns a limit off or moves its
counters out of the supervisor's memory (ADR-0450). Only the stand-in and the compose test stack's
app turn the sign-in limiter off, in code."""

from slowapi import Limiter
from slowapi.util import get_remote_address


def client_limiter() -> Limiter:
    """A limiter per client address, with its counters in the supervisor's memory."""
    limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
    limiter.enabled = True
    return limiter
