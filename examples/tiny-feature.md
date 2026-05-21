# Feature design: `GET /healthz` for `scratch_app`

A brainstorming-style design for a single HTTP health probe inside
the (currently empty) `examples/scratch-app/` workspace, with the
minimum scaffold needed for the endpoint and its pytest case to
exist and run.

This document is the single source of truth for the feature: it
states the contract, walks the alternatives considered, and pins the
chosen shape with rationale. It also doubles as a pedagogical
example of what a brainstorming-style design looks like for a
feature this small.

---

## 1. Context & purpose

`scratch_app` is a sandbox FastAPI service used to demonstrate
forge-mcp's loop on a feature too small to hide behind. Operators
(humans, container orchestrators, uptime monitors) need a single
machine-readable signal that the process is alive and serving HTTP.
The conventional shape for that signal in Python/FastAPI deployments
is `GET /healthz` returning JSON `{"status": "healthy"}` with HTTP 200.

The work captured here is one endpoint plus its test, with whatever
package scaffold the endpoint requires.

---

## 2. Goals / Non-goals

**Goals**

- One `GET /healthz` endpoint that returns `{"status": "healthy"}`
  with `Content-Type: application/json` and HTTP 200.
- A pytest case in `tests/test_healthz.py` that asserts both the
  body and the status code via FastAPI's `TestClient`.
- The smallest scaffold inside `examples/scratch-app/` that allows
  the endpoint and its test to actually run.

**Non-goals (explicit)**

- **Readiness probe (`/readyz`) or startup probe.** No dependencies
  exist in `scratch_app` to probe yet.
- **Dependency health checks.** No DB, cache, or downstream service.
- **Authentication, rate limiting, or caching headers** on the
  probe. Health endpoints are conventionally unauthenticated and
  uncached.
- **Detailed status payloads** (version, uptime, dependency map).
  The §4 contract pins a single-key shape; growing it is future
  work.
- **CI / Docker / deployment manifests.** Out of scope; the design
  stops at "endpoint exists and its test passes locally."

**Constraints (binding on the implementer)**

- **No git mutations inside the target directory.** The implementer
  must not run `git add` / `git commit` / `git push` inside the
  workspace produced by this design. All artifacts land on disk
  only; commit decisions belong to the operator.

---

## 3. Scaffold scope

`examples/scratch-app/` is empty save for `.gitkeep`. The directory
uses a hyphen (filesystem-friendly), the Python package nested inside
uses an underscore (PEP 8 module naming). Final layout this design
owns:

```
examples/scratch-app/
├── pyproject.toml
├── scratch_app/
│   ├── __init__.py
│   └── api.py          # FastAPI app instance + /healthz handler
└── tests/
    └── test_healthz.py
```

Why the split: the directory on disk is `scratch-app/` (filesystem
convention used elsewhere in `examples/`), and `scratch_app/` is a
top-level Python package within that directory. Keeping the package
identifier and the directory name distinct is intentional — code
imports `scratch_app`, filesystem traversal sees `scratch-app/`.

`pyproject.toml` declares `scratch_app` as the importable package and
pins exactly three runtime/test dependencies (§9).

---

## 4. Contract

The wire-level contract this design owns:

| field | value |
|---|---|
| method | `GET` |
| path | `/healthz` |
| status | `200` |
| `Content-Type` | `application/json` |
| body | `{"status": "healthy"}` |

The test in `tests/test_healthz.py` constructs a `TestClient`, calls
`GET /healthz`, and asserts the body and status. The body assertion
is the load-bearing one — it pins the literal wire shape future
code must not silently change.

No request body, no query parameters, no path parameters, no headers
required on the request.

---

## 5. Approaches considered

### A. Minimal literal dict — *chosen*

```python
@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "healthy"}
```

FastAPI serializes the `dict` to JSON, sets `Content-Type:
application/json`, and returns 200 by default — all three contract
fields satisfied without extra annotation. Sync `def` (not `async`)
is correct: there is no I/O to await, and FastAPI offloads sync
handlers to a threadpool, so no event-loop blocking.

**Why chosen:** smallest possible diff, exact match to the §4
contract's literal body shape, no new types introduced.

### B. Pydantic `response_model`

```python
from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["healthy"]


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    return HealthResponse(status="healthy")
```

Adds an OpenAPI-documented response schema and Pydantic-level
validation of the outgoing body. **Cost:** an extra model class, a
`Literal` import, and a layer of indirection this design doesn't
require.

**Why rejected:** YAGNI for a single-shape, single-status probe.
Nothing in this design promises the body will grow, and the test
already pins the shape. Reconsider only when a second status value
appears (e.g. `"degraded"`).

### C. Liveness + readiness split (Kubernetes-style)

`/healthz` for liveness; `/readyz` for readiness (would probe DB,
cache, etc.). Industry-standard for production deployments.

**Why rejected:** this design scopes exactly one endpoint, and
`scratch_app` has no dependencies for `/readyz` to probe. Adding a
second endpoint and a second test would be feature creep. The
readiness split is the obvious extension when the first real
dependency lands.

### Layout sub-decision: package vs flat module

A flat `scratch_app.py` next to `pyproject.toml` would also work for
a one-file app, but the §3 layout puts the handler at
`scratch_app/api.py` — a submodule path. That requires a package
directory, not a flat module. Choosing the package layout up front
leaves room for additional submodules (config, models, routers)
without a restructure on the next change.

---

## 6. Chosen design

`scratch_app/api.py` instantiates a module-level `FastAPI` app and
registers the handler:

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "healthy"}
```

**Notes**

- `app` is module-level so `tests/test_healthz.py` can import it as
  `from scratch_app.api import app`.
- Sync `def` is deliberate (see §5A); switching to `async def`
  later is a one-character change if I/O appears.
- No `response_model`, no `status_code=200` decoration — both are
  FastAPI defaults for the returned shape and HTTP success.
- `__init__.py` stays empty; no re-exports needed for a single
  submodule the test imports directly.

---

## 7. Testing strategy

`tests/test_healthz.py`:

```python
from fastapi.testclient import TestClient

from scratch_app.api import app

client = TestClient(app)


def test_healthz_returns_healthy():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
    assert response.headers["content-type"] == "application/json"
```

Three assertions, in order: status, body, content-type. The
content-type assertion pins the third field of the §4 contract at
the test boundary rather than leaving it implicit and trusting
FastAPI's default serialization to never regress.

Run from `examples/scratch-app/` (the directory containing
`pyproject.toml`); invoking pytest from any other CWD produces an
import error, not a test failure, because `uv` resolves the wrong
`pyproject.toml` from the parent. The `test` group must be installed
before pytest can resolve `httpx`. The canonical invocation uses
`uv`; any PEP 735-aware alternative (`pip>=24.1`, etc.) that
produces an equivalent installed venv is acceptable:

```sh
uv sync --group test
uv run pytest
```

One file, one test function, no fixtures, no conftest.

---

## 8. Risks & open questions

- **Body schema drift.** If a future change adds a second key
  (e.g. `version`), the literal `==` assertion in §7 will fail.
  That's intentional — the failure forces the contract change to
  be conscious. Mitigation if it becomes inconvenient: split the
  assertion into per-key checks.
- **Health-check log noise.** Once a process is being polled every
  few seconds by an orchestrator, INFO-level access logs for
  `/healthz` flood the log stream. Not a problem today (no logging
  configured), but worth flagging the day Uvicorn access logging
  goes on.
- **Path collision.** No other route uses `/healthz`; this design
  reserves it. If a router later mounts under `/`, audit for
  collision before merging.

---

## 9. Dependency budget and `pyproject.toml` shape

The design intentionally caps the dependency surface at the minimum
needed to host the endpoint and run its test. Version floors are
chosen to lock in the TestClient → httpx switch (FastAPI 0.87+) and
keep behavior reproducible on a fresh install. Runtime and test
dependencies are split — only `fastapi` is needed to serve traffic;
`pytest` and `httpx` are test-only and live under PEP 735
`[dependency-groups].test` (the uv-idiomatic home for dev-only
tooling, distinct from `[project.optional-dependencies]` which is
reserved for downstream consumer extras):

- **Runtime** — `fastapi>=0.111` (the framework hosting the
  endpoint).
- **Test** — `pytest>=8` (the runner) and `httpx>=0.27` (required
  transitively by `fastapi.testclient.TestClient`).

`pydantic` and `anyio` land in the tree as transitive `fastapi`
dependencies; nothing in this design references them directly (see
§5B for why Pydantic stays at arm's length). No async I/O library,
no logging framework, no settings library, no Pydantic extras —
none of which the chosen shape (§6) needs.

The full `pyproject.toml` shape is pinned so the implementation has
nothing to invent:

```toml
[project]
name = "scratch_app"
version = "0.0.1"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.111",
]

[dependency-groups]
test = [
    "pytest>=8",
    "httpx>=0.27",
]

[tool.pytest.ini_options]
pythonpath = ["."]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["scratch_app"]
```

Two lines are load-bearing and must not be omitted:

- `[tool.pytest.ini_options].pythonpath = ["."]` — without it,
  `from scratch_app.api import app` fails on a fresh `pytest`
  invocation when the package is not installed (project root not on
  `sys.path`).
- `[tool.hatch.build.targets.wheel].packages = ["scratch_app"]` —
  hatchling's flat-layout auto-discovery is not reliably documented
  in context7; naming the package explicitly keeps
  `pip install -e .` / `uv sync` portable across hatchling versions.

Future dependency additions are a deliberate design choice and
belong in a follow-up design round, not slipped in alongside an
unrelated change.

---

## 10. Out of scope / deferred

Explicitly deferred to future design rounds, not silently dropped:

- Readiness probe `/readyz` (see §5C). **Trigger:** first real
  dependency lands in `scratch_app`.
- Pydantic `response_model` on `/healthz` (see §5B). **Trigger:** a
  second status value (e.g. `"degraded"`) becomes meaningful.
- Detailed payload (version, uptime, build SHA, dependency map).
  **Trigger:** an operator asks for more than alive/not-alive.
- Auth, rate limiting, caching headers. **Trigger:** the endpoint
  is exposed outside the local sandbox.
- CI configuration, container manifests, deployment wiring.
  **Trigger:** the scratch app graduates beyond `examples/`.

---

## 11. References

- FastAPI — minimal GET endpoint pattern (sync `def`, dict return):
  <https://fastapi.tiangolo.com/tutorial/first-steps/>
- FastAPI — `TestClient` testing pattern:
  <https://fastapi.tiangolo.com/tutorial/testing/>
- FastAPI — `@app.get` decorator reference (`response_model`,
  `status_code` defaults):
  <https://fastapi.tiangolo.com/reference/fastapi/>
