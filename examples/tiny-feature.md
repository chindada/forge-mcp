# Tiny feature — health endpoint

**What:** Add a `GET /healthz` endpoint that returns
`{"status": "healthy"}` with HTTP 200.

**Acceptance:**
- Endpoint registered on the existing FastAPI app under `scratch_app/api.py`.
- Returns JSON body `{"status": "healthy"}` with `Content-Type:
  application/json` and status `200`.
- A pytest case in `tests/test_healthz.py` constructs a TestClient, calls
  `GET /healthz`, and asserts the body and status.

**Constraints:**
- No new dependencies.
- Do not run `git commit` / `git add` / `git push` (Rule 11).
