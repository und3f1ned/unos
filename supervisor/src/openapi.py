"""What the API documentation says about errors and about answers without a model (ADR-0417).

app.openapi() documents every route: its summary and description, its parameters, the fields and
examples of its models and every error status it answers. A route lists its errors with
``responses=errors(...)``, from the shared texts below and texts of its own; main.py adds ADMIN to
every administrator-only router. A JSON answer without a response model gets its example from
``answer()``, a file or a text from ``file_answer()``. tests/test_openapi.py checks the whole schema.
"""

from typing import Any
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from .request_limits import MAX_REQUEST_BODY, MIB


class ErrorDetail(BaseModel):
    """An error: the reason why the request failed."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"detail": "Pool not found"}]})

    detail: str = Field(description="Why the request failed, in English.")


class Unprocessable(BaseModel):
    """A request that the route cannot take: a 422 that the route's code answers itself, beside
    FastAPI's own for a request that does not fit the API's types."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"detail": "No file was sent: send it as multipart/form-data in the field 'file'"},
        {"detail": [{"loc": ["query", "path"], "msg": "Field required", "type": "missing"}]},
    ]})

    detail: str | list[dict[str, Any]] = Field(
        description="Why: a text, or for a request that does not fit the API's types one entry per problem, "
                    "with its loc, msg and type (the values sent are not repeated)."
    )


class RateLimited(BaseModel):
    """A request over the rate limit of its route."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"error": "Rate limit exceeded: 5 per 1 minute"}]})

    error: str = Field(description="The limit that the request went over.")


_SCOPE = (
    "the session cookie came from another page than the web UI's, the account must set up "
    "two-factor authentication first, or the API token's scope does not reach this route"
)

# Every route but the public ones: verify_token of src/auth/utils.py.
SIGNED_IN: dict[int, str] = {
    401: "Not signed in: no session token or API token, or one that is invalid, expired, disabled or ended.",
    403: f"The credential does not reach this route: {_SCOPE}.",
}

# The administrator-only routes: require_admin, and every router of main.py's _ADMIN_ONLY.
ADMIN: dict[int, str] = {
    401: SIGNED_IN[401],
    403: f"The account is not an administrator, or {_SCOPE}.",
}

# Every route with a request body: RequestBodyLimitMiddleware of src/request_limits.py.
BODY: dict[int, str] = {413: f"The request body is larger than {MAX_REQUEST_BODY // MIB} MiB."}

# Every route with a rate limit (@limiter.limit).
RATE_LIMITED: dict[int, str] = {
    429: "Over the route's rate limit for the client's address: try again after a minute.",
}


def errors(*parts: Mapping[int, str]) -> dict[int | str, dict[str, Any]]:
    """The ``responses`` of a route's error statuses: each status with the text of when it is
    answered. A later part's text replaces an earlier one's for the same status. A route that lists
    422 replaces FastAPI's own: its text then covers both."""
    merged: dict[int, str] = {}
    for part in parts:
        merged.update(part)
    models: dict[int, type[BaseModel]] = {422: Unprocessable, 429: RateLimited}
    return {
        code: {"model": models.get(code, ErrorDetail), "description": text}
        for code, text in sorted(merged.items())
    }


def answer(example: Any, description: str, *, status: int = 200) -> dict[int | str, dict[str, Any]]:
    """The ``responses`` entry of a JSON answer that has no response model: what it holds and an
    example of it."""
    return {status: {"description": description, "content": {"application/json": {"example": example}}}}


def model_example(model: type[BaseModel]) -> Any:
    """The first example of the model's json_schema_extra, for a route's answer of the same shape."""
    extra = model.model_config.get("json_schema_extra")
    assert isinstance(extra, dict) and isinstance(extra["examples"], list)
    return extra["examples"][0]


def file_answer(media_type: str, description: str, *, status: int = 200) -> dict[int | str, dict[str, Any]]:
    """The ``responses`` entry of an answer that is a file or a text, not JSON. The route sets
    ``response_class=Response`` (or the class it returns), so that no JSON answer is documented."""
    schema = {"type": "string"} if media_type.startswith("text/") else {"type": "string", "format": "binary"}
    return {status: {"description": description, "content": {media_type: {"schema": schema}}}}
