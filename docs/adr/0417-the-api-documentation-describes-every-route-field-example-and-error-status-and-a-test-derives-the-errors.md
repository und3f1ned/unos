---
status: accepted
date: 2026-11-19
area: architecture
decided-by: agent
---

# ADR-0417: The API documentation describes every route, field, example and error status, and a test derives the errors from the code

## Context

The supervisor's OpenAPI schema (`app.openapi()`) had FastAPI's summaries made from function
names, no description of a field or a parameter, no example and no error status but FastAPI's
own 422, so a script author had to read the code. The feature `architecture.openapi-descriptions`
asks for every endpoint, field and error, with examples; a description that claims what the code
does not do is worse than none, and no route may change its behaviour, path or status for it.

## Decision

- **Routes.** Every route sets `summary=` (a short imperative phrase, such as "Create a volume";
  FastAPI's summary from the function's name does not count) and has a description: its
  docstring, cut at a `\f` line after which developer notes and ADR ids may stay, or
  `description=`. Every path and query parameter has a description (`Path(description=...)`,
  `Query(description=...)`). Texts are short and use the terms of GLOSSARY.md.
- **Models.** Every field of every model in the schema has `Field(description=...)`. Every model
  of a request body or of an answer, also as the item of a list or of a mapping, has an example
  in `model_config` (`json_schema_extra={"examples": [...]}`) that the model takes as JSON, with no
  key it lacks at any depth. Nested models need descriptions, not examples. A JSON body without a
  model (a list of paths) gets its example from `Body(examples=...)`. A model's docstring is its
  schema's description whole (only a route's is cut at `\f`): its ADR ids go into a comment.
- **Answers without a model.** A route that answered a plain dict keeps doing so: it gets no
  response model here, since a response model validates and filters every answer, a change of
  behaviour where the model and the code differ. It documents its answer with `answer(example,
  description)` of `supervisor/src/openapi.py`, and a file or a text with `file_answer(media
  type, description)` and the `response_class` that it returns anyway. Giving those routes
  response models is later work.
- **Examples** hold no secret, host name or address: addresses of the documentation ranges
  (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32, MAC 00:00:5e:00:53:xx), and
  10.0.0.0/24 and fd00::/64 where the code takes only a local network (NFS clients, SMB's
  interfaces); example.com and its kin for domains, URLs and e-mail, besides the public hosts
  that UNOS talks to; the host name `nas`; a secret field's value, a secret in a URL's query and
  the path of a chat service's webhook a placeholder with "example" in it, stars or groups of
  one repeated character.
- **Error statuses.** Every route lists in `responses=errors(...)` each error status it answers,
  with when, from its code; FastAPI's own 422 for a parameter or a body is the one status it
  need not list. Statuses that the code answers only through an unexpected exception (Starlette's
  plain-text 500) are not listed; a 500 or 503 that the code answers on purpose is. The shared
  texts of `supervisor/src/openapi.py`: `SIGNED_IN` (401 and 403 of every route that is not
  public: no or a bad credential, a session cookie from another page, a setup-only account, an
  API token whose scope does not reach the route), `ADMIN` (the same, and 403 for a regular
  user), `BODY` (413 of the request body limit, ADR-0062) and `RATE_LIMITED` (429, whose answer
  is slowapi's `{"error": ...}`); `src/main.py` gives `ADMIN` to every router of `_ADMIN_ONLY`.
  A route may replace a shared text with its own (the sign-in's 401). The errors' model is
  `ErrorDetail`, the `{"detail": ...}` of an HTTPException; a route that lists 422 itself (the
  uploads' form without a file) replaces FastAPI's, and its model `Unprocessable` takes the text
  and FastAPI's list both.
- **What the test demands.** `supervisor/tests/test_openapi.py` checks all of the above on
  `app.openapi()` and derives, for each route, the statuses it must list: every status of an
  HTTPException (FastAPI's or Starlette's) that is raised with a literal status in the route's
  function or a dependency, or, outside an except handler, in a function of `src` that they call
  or name, or in the `__init__` of a class of `src` that they make (also through a function that
  returns the HTTPException that is raised); 401 and 403 come that way from the credential's
  check, 403 from `require_admin`. What the body of a try raises or calls does not count when the
  try's handler takes an HTTPException without a bare `raise` (it becomes a warning or another
  status). 413 for a route with a request body; 429 for a route with `@limiter.limit`. A route
  with a path parameter lists 400 or 404 for a value that names nothing. Every route lists at
  least one error status; `NO_ERRORS` names the two that cannot fail (`GET /api/v1/health`,
  `GET /api/v1/auth/status`), `NOT_ANSWERED` and `PATH_WITHOUT_NOT_FOUND` the derived statuses
  that a route cannot answer, each with its reason, and a test fails for an exception that is no
  longer needed. Each listed status says when it is answered (not the bare reason phrase). The
  test's own `TestTheChecks` builds a small app of defects and shows that each check finds its
  defect.

## Consequences

- The derivation is a floor, not the whole list: statuses that a helper's except handler maps
  from whatever the called function raises (storage's and shares' `_call`, the apps' errors),
  and those of methods other than `__init__`, are listed by hand from what that function
  raises, and a review checks them.
- A new route or model fails `test_openapi.py` until it has its summary, descriptions, example
  and errors; the shared texts keep that short.
- The schema documents about 90 routes' answers by example only: a change of such an answer
  must change its example by hand, and no test compares them.
- `/docs` and `/openapi.json` stay where FastAPI serves them, on the supervisor's own port,
  which Caddy does not pass on (`webui/Caddyfile` passes only `/api/*` and `/ws/*`).

## Considered options

- Response models for every route: the schema would follow the answers, but every route that
  returns a dict would start to validate and filter its answer, a change of behaviour that this
  entry must not make.
- Requiring 500 on every route: true of any route for an unexpected exception, and so says
  nothing about one.
- Deriving statuses through every function a route reaches, except handlers included: the
  shared mappers would make every storage and share route list 404, 409 and 503 that most of
  them never answer.
