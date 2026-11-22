"""The app that the compose test stack's supervisor runs (supervisor/Dockerfile.test copies this file
to /app/stack_app.py and runs ``uvicorn stack_app:app``): the app of src.main, with the sign-in limit
off (ADR-0450): the limiter of the sign-in and of the other routes that check a password (the
first-run setup, turning on one's own SMB access, adding and removing a security key).

Playwright reaches the stack's Caddy through a port that Docker publishes, so Caddy forwards every
spec of a run from the network's gateway, and the supervisor counts the whole run as one client of
its 5 sign-ins a minute (ADR-0010). The stand-in turns the same limiter off
(scripts/standin/run_supervisor.py). No setting does that on a NAS: supervisor/Dockerfile copies
src/ only and runs src.main:app (supervisor/tests/test_authz.py, TestTestStackSignInLimit)."""

# src.main first: it sets up the logging before any other module of the package is imported (ADR-0398).
from src.main import app
from src.auth.limits import limiter

limiter.enabled = False

__all__ = ["app"]
