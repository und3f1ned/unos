"""The API documentation, app.openapi(), describes every route, its fields, its examples and its
errors (ADR-0417).

Each check is a function that answers the problems of an app; the tests run it on the supervisor
and expect none, and on a small app of defects (``TestTheChecks``) and expect each one found.

- Every route has a summary of its own (``summary=``, not FastAPI's one from the function's name)
  and a description (its docstring, or ``description=``); every parameter has a description.
- Every field of every model of the schema has a description. Every model of a request body or of
  an answer, also as the item of a list or of a mapping, has an example, which the model takes; a
  JSON answer without a model has an example of its own (``answer()`` of src/openapi.py).
- No example holds an address, a host name or a secret: only the documentation ranges and domains.
- Every route lists the error statuses it answers beyond FastAPI's own 422, each with when; at
  least the ones that ``required_statuses`` derives from the route.
"""
from __future__ import annotations

import ast
import builtins
import http
import inspect
import ipaddress
import json
import re
import textwrap
import types
from typing import Any, get_args, get_origin

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from slowapi import Limiter
from starlette.exceptions import HTTPException as StarletteHTTPException
from slowapi.util import get_remote_address

from src.main import app
from src.openapi import ADMIN, BODY, answer, errors

try:
    from fastapi.routing import iter_route_contexts
except ImportError:  # older FastAPI flattened included routers into app.routes
    iter_route_contexts = None

# FastAPI's own schemas of its 422.
FASTAPI_SCHEMAS = {"HTTPValidationError", "ValidationError"}

# The routes that answer no error at all, with the reason. Every other route lists at least one.
NO_ERRORS = {
    ("GET", "/api/v1/health"): "public, and answers ok while the supervisor serves requests",
    ("GET", "/api/v1/auth/status"): "public, and only counts the users",
}

# A status that required_statuses derives but that the route cannot answer, with the reason:
# {(method, path): {status: reason}}.
NOT_ANSWERED: dict[tuple[str, str], dict[int, str]] = {
    ("POST", "/api/v1/shares/smb"): {
        404: "_create_share_locked reads the share back with _share_info under smb_lock, right after its commit",
    },
    ("POST", "/api/v1/shares/smb/bulk-delete"): {
        404: "delete_shares reports a share that is not there as not_found in its results; it re-raises only "
             "the other statuses of _load_share",
    },
    ("POST", "/api/v1/files/upload"): {
        413: "main.py's path_limits give the upload no limit, and _FileUpload sets no max_bytes",
    },
}

# A route with a path parameter whose code answers neither 400 nor 404 for a value that names
# nothing, with what it answers instead.
PATH_WITHOUT_NOT_FOUND: dict[tuple[str, str], str] = {
    ("DELETE", "/api/v1/scheduler/tasks/{task_id}"): "answers deleted for an id that names no task",
    ("GET", "/api/v1/scheduler/runs/{task_id}"): "answers the runs of the id, none for one that names no task",
}

# The texts that FastAPI writes when a route gives none.
DEFAULT_DESCRIPTIONS = {"", "Successful Response", "Additional Response"}

PACKAGE = "src"


# ---------------------------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------------------------


def http_routes(application: FastAPI) -> list[tuple[str, str, Any]]:
    """(method, path, route) of every HTTP operation, router prefixes, dependencies and responses
    applied, the path as the schema writes it ({device}, not {device:path}) (FastAPI 0.162 keeps included routers as they are: iter_route_contexts)."""
    if iter_route_contexts is None:
        contexts = [(route, route) for route in application.routes]
    else:
        contexts = [(ctx.original_route, ctx) for ctx in iter_route_contexts(application.routes)]
    return [
        (method, effective.path_format, effective)
        for original, effective in contexts
        if isinstance(original, APIRoute)
        for method in sorted(effective.methods)
    ]


def _operation(spec: dict, method: str, path: str) -> dict:
    return spec["paths"][path][method.lower()]


# ---------------------------------------------------------------------------------------------
# The error statuses a route answers, as far as its code shows them
# ---------------------------------------------------------------------------------------------

_STATUS_NAME = re.compile(r"HTTP_(\d{3})_")


def _status(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    if isinstance(node, ast.Attribute):
        match = _STATUS_NAME.match(node.attr)
        if match:
            return int(match.group(1))
    return None


def _resolve(node: ast.AST, names: dict) -> Any:
    if isinstance(node, ast.Name):
        return names.get(node.id, getattr(builtins, node.id, None))
    if isinstance(node, ast.Attribute):
        base = _resolve(node.value, names)
        if isinstance(base, (types.ModuleType, type)):
            return getattr(base, node.attr, None)
    return None


class _Deriver:
    """Reads the error statuses of a route from its code: a literal status of an HTTPException
    that is raised.

    The route's function and its dependencies count with every raise; a function of the package
    that they call or name (a callback of asyncio.to_thread too) counts with the raises outside its
    except handlers: a helper's handler maps whatever the function it was given raises (storage's
    and shares' ``_call``), which says nothing about this route. A raise of a function's result
    counts with the HTTPException that the function returns (``_unauthorized``), and the making of a
    class of the package with its ``__init__`` (not its other methods). What a try's body
    raises or calls does not count when a handler of that try takes an HTTPException (by name, as
    Exception or bare) without a bare ``raise``: the handler turns it into something else, such as
    a warning of the answer, and its own raises count instead."""

    def __init__(self, package: str) -> None:
        self.package = package
        self.memo: dict[tuple[Any, bool], set[int]] = {}

    def ours(self, obj: Any) -> bool:
        module = getattr(obj, "__module__", None) or ""
        in_package = module == self.package or module.startswith(self.package + ".")
        return (inspect.isfunction(obj) or inspect.isclass(obj)) and in_package

    @staticmethod
    def parse(obj: Any) -> ast.AST | None:
        try:
            return ast.parse(textwrap.dedent(inspect.getsource(obj)))
        except (OSError, TypeError, SyntaxError):
            return None

    def exception_statuses(self, call: ast.Call, names: dict, stack: tuple) -> set[int]:
        target = _resolve(call.func, names)
        if inspect.isclass(target) and issubclass(target, StarletteHTTPException):
            given = [kw.value for kw in call.keywords if kw.arg == "status_code"] + call.args[:1]
            found = {code for code in map(_status, given) if code}
            if not found and target not in (HTTPException, StarletteHTTPException):
                found = self.super_statuses(target)
            return found
        if inspect.isfunction(target) and self.ours(target) and target not in stack:
            tree = self.parse(target)
            found = set()
            for node in ast.walk(tree) if tree else ():
                if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
                    found |= self.exception_statuses(node.value, target.__globals__, stack + (target,))
            return found
        return set()

    def super_statuses(self, cls: type) -> set[int]:
        """The literal status that a subclass of HTTPException gives ``super().__init__``."""
        tree = self.parse(cls)
        found = set()
        for node in ast.walk(tree) if tree else ():
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "__init__"
                and isinstance(node.func.value, ast.Call)
                and getattr(node.func.value.func, "id", None) == "super"
                and node.args
            ):
                code = _status(node.args[0])
                if code:
                    found.add(code)
        return found

    def statuses(self, function: Any, *, own: bool, stack: tuple = ()) -> set[int]:
        function = inspect.unwrap(function)
        key = (function, own)
        if key in self.memo:
            return self.memo[key]
        if function in stack or not inspect.isfunction(function):
            return set()
        tree = self.parse(function)
        if tree is None:
            return set()
        names = function.__globals__
        handled: set[int] = set()  # a helper's except handlers
        shielded: set[int] = set()  # the bodies of tries whose handler keeps an HTTPException
        for node in ast.walk(tree):
            if not own and isinstance(node, ast.ExceptHandler):
                handled |= {id(inner) for inner in ast.walk(node)}
            if isinstance(node, ast.Try) and self.shields(node, names):
                shielded |= {id(inner) for statement in node.body for inner in ast.walk(statement)}
        found: set[int] = set()
        for node in ast.walk(tree):
            if id(node) in shielded:
                continue
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and id(node) not in handled:
                found |= self.exception_statuses(node.exc, names, stack + (function,))
            elif isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
                target = _resolve(node, names)
                if inspect.isclass(target) and self.ours(target) and not issubclass(target, StarletteHTTPException):
                    target = vars(target).get("__init__")  # a class of the package that is made: its __init__
                if inspect.isfunction(target) and self.ours(target) and target is not function:
                    found |= self.statuses(target, own=False, stack=stack + (function,))
        self.memo[key] = found
        return found

    @staticmethod
    def shields(node: ast.Try, names: dict) -> bool:
        """Whether the first handler of the try that takes an HTTPException keeps it from the answer:
        it has no bare ``raise``."""
        for handler in node.handlers:
            caught = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            if any(
                kind is None or (inspect.isclass(target := _resolve(kind, names)) and issubclass(HTTPException, target))
                for kind in caught
            ):
                return not any(isinstance(inner, ast.Raise) and inner.exc is None for inner in ast.walk(handler))
        return False

    @staticmethod
    def rate_limited(endpoint: Any) -> bool:
        """Whether a slowapi limiter's ``limit`` decorates the endpoint."""
        function = inspect.unwrap(endpoint)
        tree = _Deriver.parse(function)
        if tree is None:
            return False
        definition = tree.body[0]
        for decorator in getattr(definition, "decorator_list", []):
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                owner = _resolve(decorator.func.value, function.__globals__)
                if decorator.func.attr == "limit" and isinstance(owner, Limiter):
                    return True
        return False


def _dependency_calls(dependant: Any) -> list[Any]:
    calls, stack = [], list(dependant.dependencies)
    while stack:
        current = stack.pop()
        if current.call is not None:
            calls.append(current.call)
        stack.extend(current.dependencies)
    return calls


def has_body(route: Any) -> bool:
    return route.body_field is not None or "requestBody" in (route.openapi_extra or {})


def required_statuses(route: Any, deriver: _Deriver) -> set[int]:
    """The error statuses that the route must list: every status its code raises (see _Deriver),
    which brings 401 and 403 from the credential's check of every route that is not public and
    403 from require_admin; 413 with a request body (RequestBodyLimitMiddleware); 429 with a rate
    limit."""
    found = set(deriver.statuses(route.endpoint, own=True))
    for call in _dependency_calls(route.dependant):
        found |= deriver.statuses(call, own=True)
    if has_body(route):
        found.add(413)
    if deriver.rate_limited(route.endpoint):
        found.add(429)
    return {code for code in found if code >= 400}


# ---------------------------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------------------------


def summary_problems(application: FastAPI) -> list[str]:
    problems = []
    for method, path, route in http_routes(application):
        if not (route.summary or "").strip():
            problems.append(f"{method} {path}: no summary= of its own")
        if not (route.description or "").strip():
            problems.append(f"{method} {path}: no description (a docstring or description=)")
    return problems


def parameter_problems(spec: dict) -> list[str]:
    problems = []
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            for parameter in operation.get("parameters", []):
                if not (parameter.get("description") or "").strip():
                    problems.append(f"{method.upper()} {path}: the {parameter['in']} parameter "
                                    f"{parameter['name']} has no description")
    return problems


def field_problems(spec: dict) -> list[str]:
    problems = []
    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        if name in FASTAPI_SCHEMAS:
            continue
        for field, field_schema in schema.get("properties", {}).items():
            if not (field_schema.get("description") or "").strip():
                problems.append(f"{name}.{field}: no description")
    return problems


def _model_name(schema: dict | None) -> str | None:
    """The component that a body or an answer is, or whose items or values it holds."""
    if not schema:
        return None
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    for inner in (schema.get("items"), schema.get("additionalProperties"), *schema.get("anyOf", [])):
        if isinstance(inner, dict) and "$ref" in inner:
            return inner["$ref"].rsplit("/", 1)[-1]
    return None


def _has_example(media: dict) -> bool:
    return any(key in where for where in (media, media.get("schema") or {}) for key in ("example", "examples"))


def example_problems(spec: dict) -> list[str]:
    """Every model of a body or of an answer has an example; a JSON body or answer without a model
    has one of its own, an answer without a model says what it holds."""
    schemas = spec.get("components", {}).get("schemas", {})
    problems, models = [], set()
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            where = f"{method.upper()} {path}"
            for media_type, media in operation.get("requestBody", {}).get("content", {}).items():
                name = _model_name(media.get("schema"))
                if name:
                    models.add(name)
                elif media_type == "application/json" and not _has_example(media):
                    problems.append(f"{where}: the body has neither a model nor an example")
            for code, response in operation["responses"].items():
                if not code.startswith("2"):
                    continue
                for media_type, media in response.get("content", {}).items():
                    name = _model_name(media.get("schema"))
                    if name:
                        models.add(name)
                        continue
                    if (response.get("description") or "") in DEFAULT_DESCRIPTIONS:
                        problems.append(f"{where}: the {code} answer without a model does not say what it holds")
                    if media_type == "application/json" and not _has_example(media):
                        problems.append(f"{where}: the {code} answer has neither a model nor an example")
    for name in sorted(models):
        if not schemas.get(name, {}).get("examples"):
            problems.append(f"{name}: a model of a request or an answer without an example")
    return problems


def _models(annotation: Any, found: dict[str, type[BaseModel]]) -> None:
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        key = f"{annotation.__module__}.{annotation.__qualname__}"
        if key in found:
            return
        found[key] = annotation
        for field in annotation.model_fields.values():
            _models(field.annotation, found)
        return
    if get_origin(annotation) is not None or get_args(annotation):
        for argument in get_args(annotation):
            _models(argument, found)


def route_models(application: FastAPI) -> list[type[BaseModel]]:
    """The models of the routes' bodies and answers and the models in their fields."""
    found: dict[str, type[BaseModel]] = {}
    for _, _, route in http_routes(application):
        for parameter in route.dependant.body_params:
            _models(parameter.field_info.annotation, found)
        _models(route.response_model, found)
        for response in (route.responses or {}).values():
            _models(response.get("model"), found)
    return list(found.values())


def _unknown_keys(example: Any, taken: Any, where: str = "") -> list[str]:
    """The keys of an example, at any depth, that the model dropped when it took the example."""
    if isinstance(example, dict) and isinstance(taken, dict):
        return [
            unknown
            for key, value in example.items()
            for unknown in ([f"{where}{key}"] if key not in taken else _unknown_keys(value, taken[key], f"{where}{key}."))
        ]
    if isinstance(example, list) and isinstance(taken, list) and len(example) == len(taken):
        return [unknown for index, (value, kept) in enumerate(zip(example, taken))
                for unknown in _unknown_keys(value, kept, f"{where}{index}.")]
    return []


def model_example_problems(models: list[type[BaseModel]]) -> list[str]:
    """Each example of a model is a JSON value that the model takes, with no key, at any depth, that
    it does not have."""
    problems = []
    for model in models:
        extra = model.model_config.get("json_schema_extra")
        examples = extra.get("examples", []) if isinstance(extra, dict) else []
        for number, example in enumerate(examples, start=1):
            where = f"{model.__module__}.{model.__qualname__}, example {number}"
            try:
                taken = model.model_validate_json(json.dumps(example))
            except Exception as exc:  # noqa: BLE001 - every refusal is the problem
                problems.append(f"{where}: the model refuses it: {str(exc).splitlines()[0]}")
                continue
            dumped = taken.model_dump(mode="json")
            dumped_by_alias = taken.model_dump(mode="json", by_alias=True)
            for name, field in model.model_fields.items():  # a field that is never answered, such as a password
                if field.exclude and isinstance(example, dict):
                    for key in {name, field.alias or name} & set(example):
                        dumped[key] = dumped_by_alias[key] = example[key]
            unknown = sorted(set(_unknown_keys(example, dumped)) & set(_unknown_keys(example, dumped_by_alias)))
            if unknown:
                problems.append(f"{where}: keys the model does not have: {unknown}")
    return problems


# --- what an example may hold ----------------------------------------------------------------

# The documentation ranges (RFC 5737, RFC 3849), and one private network of each family for the
# fields that the code takes only from a local network (NFS clients, SMB's interfaces).
DOCUMENTATION_NETWORKS = [
    ipaddress.ip_network(network)
    for network in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32",
                    "10.0.0.0/24", "fd00::/64",
                    # The tunnel network of remote access by default: UNOS's own (ADR-0420).
                    "10.99.0.0/24")
]
DOCUMENTATION_DOMAINS = ("example.com", "example.net", "example.org", "example")
# A local name of an app is <label>-<host name>.local (ADR-0420); .local has no documentation domain.
DOCUMENTATION_HOST_NAMES = {"jellyfin-nas.local"}
# Public hosts that UNOS itself talks to: a URL of theirs in an example names no one's NAS. A URL
# of the chat services carries its secret in the path, which must then be a placeholder.
PUBLIC_HOSTS = {"github.com", "api.github.com", "ghcr.io", "docker.io", "lscr.io", "host.docker.internal"}
SECRET_PATH_HOSTS = {"hooks.slack.com", "discord.com", "discordapp.com", "api.telegram.org"}
# RFC 7042's documentation block of MAC addresses.
DOCUMENTATION_MAC = "00:00:5e:00:53:"
SECRET_KEY = re.compile(
    r"(?i)(password|passphrase|secret|private_key|api_key|session_key|access_token|bot_token|^token$|_token$"
    r"|^codes$|recovery_codes)"
)
_IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?:/\d{1,2})?(?!\w|\.\d)")
# A host name outside a URL or an e-mail address: labels that end in a top-level domain of homes,
# companies and LANs.
_HOST_NAME = re.compile(
    r"(?i)(?<![\w.@/-])((?:[a-z0-9-]+\.)+(?:com|net|org|io|de|ru|uk|fr|eu|nl|ch|at|it|es|pl|lan|local|home"
    r"|box|internal|arpa|cloud|dev|localdomain))(?![\w-])"
)
_QUERY_SECRET = re.compile(r"(?i)[?&](secret|token|key|password|passwd|pass)=([^&#\s]*)")
_IPV6 = re.compile(r"(?<![\w:])([0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7})(?:%\w+)?(?:/\d{1,3})?(?![\w:])")
_MAC = re.compile(r"(?i)(?<![\w:])((?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2})(?![\w:])")
_EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
# otpauth:// (a TOTP secret's URI) names an account, not a host.
_URL_HOST = re.compile(r"(?i)\b(?!otpauth:)[a-z][a-z0-9+.-]*://(?:[^/@\s]*@)?(\[[^\]]+\]|[^/:?#\s]+)")
_JWT = re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}")
# An iSCSI name (RFC 3720): its naming authority is a domain, reversed. LIO's own authority names
# the targets of UNOS (ADR-0418), as a public host names no one's NAS.
_IQN = re.compile(r"(?i)(?<![\w.])iqn\.\d{4}-\d{2}\.([a-z0-9-]+(?:\.[a-z0-9-]+)*)(?::[\w.:-]*)?")
IQN_AUTHORITIES = {"linux-iscsi.org"}


def _documentation_address(text: str) -> bool:
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return True  # not an address
    if address.is_unspecified or address.is_loopback:
        return True
    if address.version == 4:
        try:  # a netmask
            ipaddress.ip_network(f"0.0.0.0/{text}")
            return True
        except ValueError:
            pass
    if address.version == 6 and address.is_link_local and int(address) & ((1 << 64) - 1) < 1 << 16:
        return True  # fe80::1, not one made from a MAC address
    return any(address in network for network in DOCUMENTATION_NETWORKS)


def _documentation_host(host: str) -> bool:
    host = host.strip("[]").lower()
    if host in PUBLIC_HOSTS or host in DOCUMENTATION_HOST_NAMES or host == "localhost":
        return True
    if any(host == domain or host.endswith("." + domain) for domain in DOCUMENTATION_DOMAINS):
        return True
    return _documentation_address(host) and (_IPV4.fullmatch(host) is not None or ":" in host)


def text_problems(text: str) -> list[str]:
    problems = []
    for match in _IPV4.finditer(text):
        if not _documentation_address(match.group(1)):
            problems.append(f"the address {match.group(1)} is outside the documentation ranges")
    for match in _IPV6.finditer(text):
        candidate = match.group(1)
        if candidate.count(":") >= 2 and not _MAC.fullmatch(candidate) and not _documentation_address(candidate):
            problems.append(f"the address {candidate} is outside 2001:db8::/32")
    for match in _MAC.finditer(text):
        if not match.group(1).lower().replace("-", ":").startswith(DOCUMENTATION_MAC):
            problems.append(f"the MAC address {match.group(1)} is outside {DOCUMENTATION_MAC}00/24")
    for match in _EMAIL.finditer(text):
        if not _documentation_host(match.group(1)):
            problems.append(f"the e-mail domain {match.group(1)} is not a documentation domain")
    for match in _URL_HOST.finditer(text):
        host = match.group(1).strip("[]").lower()
        if host in SECRET_PATH_HOSTS:
            if not _secret_placeholder(text[match.end():].split()[0] if text[match.end():].split() else ""):
                problems.append(f"the URL of {host} carries a secret that is not a placeholder")
        elif not _documentation_host(host):
            problems.append(f"the host {match.group(1)} is not a documentation domain")
    for match in _IQN.finditer(text):
        authority = ".".join(reversed(match.group(1).lower().split(".")))
        if not any(authority == a or authority.endswith("." + a) for a in IQN_AUTHORITIES) \
                and not _documentation_host(authority):
            problems.append(f"the iSCSI name's naming authority {authority} is not a documentation domain")
    for match in _HOST_NAME.finditer(_IQN.sub(" ", _URL_HOST.sub(" ", _EMAIL.sub(" ", text)))):
        if not _documentation_host(match.group(1)) and match.group(1).lower() not in SECRET_PATH_HOSTS:
            problems.append(f"the host name {match.group(1)} is not a documentation domain")
    for match in _QUERY_SECRET.finditer(text):
        if not _secret_placeholder(match.group(2)):
            problems.append(f"the URL's {match.group(1)}= is not a placeholder")
    if _JWT.search(text):
        problems.append("a JSON Web Token")
    return problems


def _secret_placeholder(value: str) -> bool:
    """A value that stands for a secret: with "example" in it, or stars, or groups of one repeated
    character (1111-1111-1111)."""
    if not value or "example" in value.lower() or set(value) <= set("*xX0-./"):
        return True
    return all(len(set(group)) <= 1 for group in re.split(r"[-./\s]", value))


def value_problems(value: Any, key: str = "") -> list[str]:
    """The problems of an example: addresses, host names and secrets in its strings."""
    if isinstance(value, dict):
        return [problem for name, inner in value.items() for problem in value_problems(inner, str(name))]
    if isinstance(value, list):
        return [problem for inner in value for problem in value_problems(inner, key)]
    if isinstance(value, str):
        problems = text_problems(value)
        if SECRET_KEY.search(key) and not _secret_placeholder(value):
            problems.append(f"the secret {key} is not a placeholder (one with 'example' in it, or stars)")
        return problems
    return []


def spec_examples(node: Any, parent: str = "") -> list[Any]:
    """Every example of the schema: of a model, a field, a parameter or a media type."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if parent != "properties" and key == "example":
                found.append(value)
            elif parent != "properties" and key == "examples":
                if isinstance(value, list):
                    found.extend(value)
                elif isinstance(value, dict):  # a media type's: {name: {"value": ...}}
                    found.extend(entry.get("value") for entry in value.values() if isinstance(entry, dict))
            else:
                found.extend(spec_examples(value, key))
    elif isinstance(node, list):
        for value in node:
            found.extend(spec_examples(value, parent))
    return found


def example_content_problems(spec: dict) -> list[str]:
    return sorted({problem for example in spec_examples(spec) for problem in value_problems(example)})


# --- error statuses ----------------------------------------------------------------------------


def error_problems(application: FastAPI, spec: dict, *, package: str = PACKAGE,
                   no_errors: dict = NO_ERRORS, not_answered: dict = NOT_ANSWERED,
                   path_without_not_found: dict = PATH_WITHOUT_NOT_FOUND) -> list[str]:
    deriver = _Deriver(package)
    problems = []
    for method, path, route in http_routes(application):
        where = f"{method} {path}"
        listed = {int(code) for code in (route.responses or {}) if str(code).isdigit() and int(code) >= 400}
        exempt = not_answered.get((method, path), {})
        missing = required_statuses(route, deriver) - listed - set(exempt)
        if missing:
            problems.append(f"{where}: does not list {sorted(missing)}, which its code answers")
        if not listed and (method, path) not in no_errors:
            problems.append(f"{where}: lists no error status beyond FastAPI's own 422")
        if "{" in path and not listed & {400, 404} and (method, path) not in path_without_not_found:
            problems.append(f"{where}: lists neither 400 nor 404 for a path value that names nothing")
        responses = _operation(spec, method, path)["responses"]
        for code in sorted(listed):
            text = (responses.get(str(code), {}).get("description") or "").strip()
            if text in DEFAULT_DESCRIPTIONS or text == http.HTTPStatus(code).phrase:
                problems.append(f"{where}: the {code} says not when it is answered")
    return problems


# ---------------------------------------------------------------------------------------------
# The supervisor
# ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spec() -> dict:
    return app.openapi()


def _report(problems: list[str]) -> str:
    return f"{len(problems)} problems (ADR-0417):\n" + "\n".join(problems)


class TestTheSupervisor:
    def test_every_route_has_a_summary_and_a_description(self):
        problems = summary_problems(app)
        assert not problems, _report(problems)

    def test_every_parameter_has_a_description(self, spec):
        problems = parameter_problems(spec)
        assert not problems, _report(problems)

    def test_every_field_of_every_model_has_a_description(self, spec):
        problems = field_problems(spec)
        assert not problems, _report(problems)

    def test_every_request_and_answer_has_an_example(self, spec):
        problems = example_problems(spec)
        assert not problems, _report(problems)

    def test_the_models_take_their_examples(self):
        models = route_models(app)
        assert len(models) > 100  # the walk reaches the models of the routes
        problems = model_example_problems(models)
        assert not problems, _report(problems)

    def test_no_example_holds_an_address_a_host_name_or_a_secret(self, spec):
        assert spec_examples(spec)
        problems = example_content_problems(spec)
        assert not problems, _report(problems)

    def test_every_route_lists_the_error_statuses_it_answers(self, spec):
        problems = error_problems(app, spec)
        assert not problems, _report(problems)

    def test_every_exception_is_still_needed(self):
        """Each exception names a route that exists and still needs it: a route of NO_ERRORS lists
        nothing, a status of NOT_ANSWERED is derived and not listed, a route of
        PATH_WITHOUT_NOT_FOUND lists neither 400 nor 404."""
        routes = {(method, path): route for method, path, route in http_routes(app)}
        assert set(NO_ERRORS) | set(NOT_ANSWERED) | set(PATH_WITHOUT_NOT_FOUND) <= set(routes)
        deriver = _Deriver(PACKAGE)

        def listed(key):
            return {int(code) for code in routes[key].responses or {} if str(code).isdigit() and int(code) >= 400}

        for key in NO_ERRORS:
            assert not listed(key), key
        for key, statuses in NOT_ANSWERED.items():
            assert set(statuses) <= required_statuses(routes[key], deriver) - listed(key), key
        for key in PATH_WITHOUT_NOT_FOUND:
            assert not listed(key) & {400, 404}, key

    def test_the_administrator_routes_list_the_administrator_errors(self, spec):
        """main.py gives every administrator-only router ADMIN's texts."""
        responses = _operation(spec, "GET", "/api/v1/storage/pools")["responses"]
        assert responses["401"]["description"] == ADMIN[401]
        assert responses["403"]["description"] == ADMIN[403]


# ---------------------------------------------------------------------------------------------
# The checks find each defect (a small app of its own, whose package is this module's)
# ---------------------------------------------------------------------------------------------

_limiter = Limiter(key_func=get_remote_address)


class _Undescribed(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"name": "data"}]})

    name: str


class _WithoutExample(BaseModel):
    name: str = Field(description="The name.")


class _Described(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"name": "data", "count": "many"}]})

    name: str = Field(description="The name.")
    count: int = Field(0, description="How many.")


class _WithAddress(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"url": "https://nas.home.lan:8443/x"}]})

    url: str = Field(description="Where.")


def _raises_conflict() -> None:
    raise HTTPException(status.HTTP_409_CONFLICT, "busy")


def _maps(function) -> None:
    try:
        function()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


def _unknown() -> HTTPException:
    return HTTPException(410, "gone")


def _warns() -> list[str]:
    """Turns the helper's 409 into a warning; the bare raise of the other passes its 409 on."""
    warnings = []
    try:
        _raises_conflict()
    except HTTPException as exc:
        warnings.append(exc.detail)
    try:
        _raises_conflict()
    except HTTPException:
        raise
    return warnings


def _defects_app() -> FastAPI:
    defects = FastAPI()

    @defects.get("/no-summary")
    async def no_summary() -> _Described:
        """Described, without a summary."""

    @defects.get("/no-description", summary="Without a description")
    async def no_description() -> _Described:
        pass

    @defects.get("/listed", summary="Lists its errors", responses=errors({404: "No such thing."}))
    async def listed(name: str) -> _Described:
        """A parameter without a description."""
        raise HTTPException(404, "no")

    @defects.post("/no-example", summary="A body without an example", responses=errors(BODY))
    async def no_example(body: _WithoutExample) -> _Undescribed:
        """A body model without an example, an answer with a field without a description."""

    @defects.get("/no-errors", summary="Lists nothing")
    async def no_errors() -> _Described:
        """Lists no error."""

    @defects.get("/derived", summary="Raises", responses=errors({500: "Never."}))
    async def derived() -> _WithAddress:
        """Raises 400 itself, 409 through a helper, 410 through a factory; a mapper's 404 does not
        count."""
        if derived:
            raise HTTPException(status_code=400, detail="bad")
        _raises_conflict()
        _maps(_raises_conflict)
        raise _unknown()

    @defects.post("/body", summary="A body", responses=errors({500: "Never."}))
    async def body(value: _Described) -> _Described:
        """A body: 413."""

    @defects.post("/limited", summary="Limited", responses=errors(BODY))
    @_limiter.limit("1/minute")
    async def limited(request: Request, value: _Described) -> _Described:
        """A rate limit: 429."""

    @defects.get("/things/{name}", summary="A path", responses=errors({500: "Never."}))
    async def thing(name: str) -> _Described:
        """A path value that names nothing."""

    @defects.post("/names", summary="A list", responses=errors(BODY))
    async def names(names: list[str]) -> _Described:
        """A body of names without an example."""

    @defects.get("/plain", summary="Plain answer", responses=errors({409: ""}))
    async def plain():
        """An answer without a model, and an error without a text."""
        return {"ok": True}

    @defects.get("/documented", summary="Answer", responses={**answer({"ok": True}, "Whether it went."),
                                                            **errors({409: "When busy."})})
    async def documented():
        """A documented answer without a model."""
        return {"ok": True}

    @defects.get("/caught", summary="Caught", responses=errors({500: "Never."}))
    async def caught() -> _Described:
        """A 409 turned into a warning; a 422 of its own handler."""
        try:
            _raises_conflict()
        except Exception:
            raise HTTPException(422, "refused")
        _warns()

    @defects.get("/dependency", summary="A dependency", responses=errors({500: "Never."}))
    async def dependency(user=Depends(_raises_conflict)) -> _Described:
        """Its dependency raises 409."""

    return defects


@pytest.fixture(scope="module")
def defects():
    application = _defects_app()
    return application, application.openapi()


class TestTheChecks:
    def test_a_route_without_a_summary_or_a_description(self, defects):
        problems = summary_problems(defects[0])
        assert "GET /no-summary: no summary= of its own" in problems
        assert "GET /no-description: no description (a docstring or description=)" in problems
        assert not [p for p in problems if "/listed" in p]

    def test_a_parameter_without_a_description(self, defects):
        assert parameter_problems(defects[1]) == [
            "GET /listed: the query parameter name has no description",
            "GET /things/{name}: the path parameter name has no description",
        ]

    def test_a_field_without_a_description(self, defects):
        problems = field_problems(defects[1])
        assert [p for p in problems if p.startswith("_Undescribed.")] == ["_Undescribed.name: no description"]
        assert not [p for p in problems if p.startswith("_Described.")]

    def test_a_model_without_an_example_and_an_answer_without_one(self, defects):
        problems = example_problems(defects[1])
        assert "_WithoutExample: a model of a request or an answer without an example" in problems
        assert "GET /plain: the 200 answer has neither a model nor an example" in problems
        assert "GET /plain: the 200 answer without a model does not say what it holds" in problems
        assert "POST /names: the body has neither a model nor an example" in problems
        assert not [p for p in problems if "/documented" in p or p.startswith("_Described")]

    def test_an_example_that_its_model_refuses(self):
        problems = model_example_problems([_Described, _Undescribed])
        assert len(problems) == 1 and problems[0].startswith(f"{__name__}._Described, example 1: the model refuses it")

    def test_an_example_with_a_key_its_model_lacks(self):
        class Typo(BaseModel):
            model_config = ConfigDict(json_schema_extra={"examples": [{"nmae": "data"}]})
            name: str = Field("", description="The name.")

        assert model_example_problems([Typo])[0].endswith("keys the model does not have: ['nmae']")

    def test_an_example_with_an_address_a_host_name_or_a_secret(self, defects):
        assert example_content_problems(defects[1]) == ["the host nas.home.lan is not a documentation domain"]
        assert value_problems({"gateway": "192.168.1.1", "dns": "2a00:1450::1", "mac": "a8:5e:45:01:02:03",
                               "mail": "me@mail.ru", "password": "hunter2", "token": "eyJhbGciOiJI.eyJzdWIiOiJh.x"}) == [
            "the address 192.168.1.1 is outside the documentation ranges",
            "the address 2a00:1450::1 is outside 2001:db8::/32",
            "the MAC address a8:5e:45:01:02:03 is outside 00:00:5e:00:53:00/24",
            "the e-mail domain mail.ru is not a documentation domain",
            "the secret password is not a placeholder (one with 'example' in it, or stars)",
            "a JSON Web Token",
            "the secret token is not a placeholder (one with 'example' in it, or stars)",
        ]
        assert value_problems({"text": "Reached at 192.168.1.20.", "host": "Mounted from johns-nas.fritz.box",
                               "bot_token": "123456:AAE-abc", "codes": ["A1B2-C3D4"],
                               "slack": "https://hooks.slack.com/services/T0/B0/abcdefgh",
                               "otp": "otpauth://totp/UNOS:alice?secret=JBSWY3DPEHPK3PXP&issuer=UNOS"}) == [
            "the address 192.168.1.20 is outside the documentation ranges",
            "the host name johns-nas.fritz.box is not a documentation domain",
            "the secret bot_token is not a placeholder (one with 'example' in it, or stars)",
            "the secret codes is not a placeholder (one with 'example' in it, or stars)",
            "the URL of hooks.slack.com carries a secret that is not a placeholder",
            "the URL's secret= is not a placeholder",
        ]
        assert value_problems({"initiator": "iqn.1993-08.org.debian:01:5f3c0a1b2c3d"}) == [
            "the iSCSI name's naming authority debian.org is not a documentation domain",
        ]
        assert value_problems({"target": "iqn.2003-01.org.linux-iscsi.unos:backup",
                               "initiator": "iqn.2026-11.com.example:client-1"}) == []
        assert value_problems({"address": "192.0.2.10/24", "netmask": "255.255.255.0", "v6": "2001:db8::10",
                               "lan": "10.0.0.20", "ula": "fd00::20", "image": "lscr.io/linuxserver/x:1",
                               "file": "backup_2026-11-18.tar.gz", "gateway": "host.docker.internal",
                               "slack": "https://hooks.slack.com/services/EXAMPLE/EXAMPLE/EXAMPLE",
                               "link": "fe80::1", "mac": "00:00:5e:00:53:01", "mail": "admin@example.com",
                               "url": "https://nas.example.com/x", "password": "example-password",
                               "totp": "otpauth://totp/UNOS:alice?secret=EXAMPLE&issuer=UNOS",
                               "time": "12:30:45", "version": "1.2.3"}) == []

    def test_a_route_that_lists_no_error_or_not_those_it_answers(self, defects):
        problems = error_problems(defects[0], defects[1], package=__name__, no_errors={})
        assert "GET /no-errors: lists no error status beyond FastAPI's own 422" in problems
        assert "GET /derived: does not list [400, 409, 410], which its code answers" in problems
        assert "POST /body: does not list [413], which its code answers" in problems
        assert "POST /limited: does not list [429], which its code answers" in problems
        assert "GET /things/{name}: lists neither 400 nor 404 for a path value that names nothing" in problems
        assert "GET /plain: the 409 says not when it is answered" in problems
        assert not [p for p in problems if p.startswith(("GET /listed", "POST /no-example", "GET /documented"))]

    def test_the_exceptions_silence_a_derived_status(self, defects):
        problems = error_problems(defects[0], defects[1], package=__name__, no_errors={("GET", "/no-errors"): "-"},
                                  not_answered={("GET", "/derived"): {400: "-", 409: "-", 410: "-"}},
                                  path_without_not_found={("GET", "/things/{name}"): "-"})
        assert not [p for p in problems if p.startswith(("GET /no-errors", "GET /derived", "GET /things"))]

    def test_a_dependency_and_a_rate_limit_count(self, defects):
        problems = error_problems(defects[0], defects[1], package=__name__)
        assert "GET /dependency: does not list [409], which its code answers" in problems

    def test_a_status_that_a_handler_keeps_from_the_answer_does_not_count(self, defects):
        problems = error_problems(defects[0], defects[1], package=__name__)
        assert "GET /caught: does not list [409, 422], which its code answers" in problems
        deriver = _Deriver(__name__)
        assert deriver.statuses(_warns, own=False) == {409}  # the second try's bare raise
