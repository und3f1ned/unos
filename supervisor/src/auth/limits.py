"""The rate limiter of the auth routes: 5 sign-ins a minute per client (ADR-0010), and the routes
that check a password, in src/auth/router.py and src/auth/security_keys.py. One object, so that
the stand-in turns it off and the tests reset it in one place (``router.limiter``)."""

from ..rate_limits import client_limiter

limiter = client_limiter()
