# Plum Claims Processing System

[![Demo video](https://img.shields.io/badge/demo-walk--through-FF0000?logo=youtube&logoColor=white)](https://youtu.be/UPxC4o24bQs)
[![Eval](https://img.shields.io/badge/eval-12%2F12%20passing-2ea44f)](docs/eval_report.md)
&nbsp;
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C)
![Gemini](https://img.shields.io/badge/Gemini-8E75B2?logo=googlegemini&logoColor=white)
![React](https://img.shields.io/badge/React-61DAFB?logo=react&logoColor=black)
![Postgres](https://img.shields.io/badge/Postgres-4169E1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)

An AI adjudicator for employee health-insurance claims. A member submits a treatment category, a
claimed amount, and supporting documents (bills, prescriptions, lab reports); the system reads
them with vision, applies the member's policy, and returns one of **APPROVED / PARTIAL / REJECTED
/ MANUAL_REVIEW** — with the approved amount, ranked reason codes, a confidence score, and a fully
reconstructable trace, through a React UI for submission and decision review.

**Design principle: the LLM proposes, deterministic code decides.** Gemini classifies, extracts
source-bound fields, maps free text to policy concepts, and judges; every rule, every rupee, and
every verdict is plain deterministic Python. The same claim always yields the same decision and the
same trace — which is what makes it auditable.

## Contents

- [Architecture](#architecture) · [Highlights](#highlights)
- [Quickstart](#quickstart-docker) · [Deployment](#deployment) · [CI](#ci)
- [Local development](#local-development-without-docker) · [Configuration](#configuration)
- [Authentication and RBAC](#authentication-and-rbac) · [Testing](#testing) · [Eval](#eval)
- [Project structure](#project-structure) · [Deliverables](#deliverables)

## Architecture

```mermaid
flowchart TD
    M["Member"] -->|"submit claim + documents"| FE
    O["Operator"] -->|"review · dashboard · policy studio"| FE
    FE["React SPA (nginx)"] -->|"REST /api — JWT + RBAC"| API["FastAPI — app/api routers"]
    API -->|"run_claim()"| INT

    subgraph PIPE["LangGraph pipeline — LLM proposes, deterministic code decides"]
        direction TB
        INT["intake"] -->|"ok"| EXT["extract_doc (fan-out: per document)"]
        INT -. "submission problem" .-> EXP["explain"]
        EXT --> DG["docgate"]
        DG -->|"docs valid"| SM["semantic_map"]
        DG -. "doc problem — early exit" .-> EXP
        SM --> SUP["supervisor (safe rule routing)"]
        SUP -->|"fan-out: per rule"| RC["rule_check: waiting · exclusion · pre-auth · limits · fraud"]
        RC --> FIN["financial: network discount, then co-pay"]
        FIN --> DEC["decide: aggregate verdict + confidence"]
        DEC --> VER["verifier: LLM judge"]
        VER --> EXP
    end

    EXP -->|"decision + trace + confidence"| API
    EXT -. "Gemini Flash" .-> GEM[["Gemini — vision + LLM"]]
    SM -. "Gemini Flash" .-> GEM
    VER -. "Gemini Pro" .-> GEM

    API --> PG[("PostgreSQL: claims · audit_log · users · policy_versions")]
    API --> RDS[("Redis + Celery worker (async)")]
    API --> OBJ[("object store: local / MinIO")]

    classDef llm fill:#ffe1e8,stroke:#e0476a,color:#3a0d1c;
    classDef det fill:#e4f0ff,stroke:#3a7bd5,color:#0a2540;
    classDef ext fill:#fff4d6,stroke:#d9a400,color:#3a2c00;
    class EXT,SM,VER llm
    class INT,DG,SUP,RC,FIN,DEC,EXP det
    class GEM,PG,RDS,OBJ ext
```

**Reading it:** pink = LLM (Gemini) steps that *propose* facts; blue = deterministic Python that *decides*; yellow = infra/external. The two dotted **early-exit** edges stop a claim before any decision when intake or documents fail; the two **fan-outs** run extraction per-document and rules per-rule. A clean claim emits ~14 ordered trace steps. Full detail in [`docs/architecture.md`](docs/architecture.md).

## Highlights
These go past the brief toward a production tool (see [`docs/product_roadmap.md`](docs/product_roadmap.md)):
- **Real-time "shift-left" document verification** — the submission form shows per-category
  drop-zones ("Upload Prescription", "Upload Hospital Bill") and classifies each file *as you
  upload it*, so a wrong or blurry document is caught **before** submit, not after.
- **Ops document viewer** — a split-screen review: the uploaded source documents (zoom/switch)
  on the left, the machine's decision + trace on the right.
- **Decision replay** — re-runs the verdict from the stored extracted facts with **no LLM**, in
  the UI, proving the determinism guarantee ("same facts → same decision").
- **Cost & latency observability** — per-claim token usage, an estimated ₹ cost, and total
  latency are captured per step and surfaced in the trace and UI. Optional LangSmith tracing
  (env-gated) adds the technical execution tree.
- **Asynchronous claim processing (credible scale)** — production runs claims off the request
  thread on a **Celery worker pool backed by self-hosted Redis**: `POST /api/claims/async`
  enqueues and returns a `job_id` instantly; the UI polls `GET /api/jobs/{job_id}` until
  `completed`. The synchronous `POST /api/claims` is unchanged and the UI stays on it by default;
  if the broker is down, the async endpoint **gracefully falls back to synchronous processing** so
  it always works. The Compose stack adds `redis` + `worker` services.

---

## Quickstart (Docker)

Postgres is included in the Compose stack; the only thing you must provide is a Gemini API key.

```bash
cp .env.example .env          # then edit .env and set GEMINI_API_KEY=<your key>
docker compose up --build     # open http://localhost
```

### Common tasks (`make`)

A root `Makefile` wraps the full lifecycle — run `make help` to list everything (Linux/macOS;
on Windows use WSL):

| Command | What it does |
|---------|--------------|
| `make setup` | Create the backend venv + install backend (`.[dev]`) and frontend deps |
| `make dev` | Bring the stack up in open/dev mode on http://localhost |
| `make prod` | Bring the stack up in **true-prod** mode (auth + PHI encryption; reads root `.env`) |
| `make ps` / `make logs` / `make down` | Stack status / tail logs / stop |
| `make check` | Everything CI runs — ruff + pyright + backend tests + frontend build/lint |
| `make test` / `make test-live` | Backend deterministic suite / live-Gemini suite |
| `make eval` | Run the 12 test cases through the live pipeline → `docs/eval_report.md` |
| `make migrate` | Apply DB migrations (`alembic upgrade head`) |
| `make tls DOMAIN=…` | Prod + HTTPS via Caddy |

Open **http://localhost** — nginx serves the React app and reverse-proxies `/api` to the
backend. The vision pipeline is fully live, so the host needs network access to the Gemini API.

> Docker Compose auto-reads the root `.env` for `${GEMINI_API_KEY}` interpolation, so no manual
> `export` step is needed. (For non-Docker local dev the backend reads `backend/.env` instead.)

---

## Deployment

A from-scratch single-VM setup is documented in [`docs/DEPLOY.md`](docs/DEPLOY.md).

### CI

GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) gates every push/PR:
backend (ruff · pyright · pytest), frontend (tsc · eslint · vite build), and a Trivy vulnerability
scan. Docs-only changes are skipped via `paths-ignore`.

> The original pipeline also **auto-deployed** green `main` to AWS EC2 — build images → push to
> GHCR → deploy over **OIDC + SSM** (no static keys), with healthcheck-gated rollback via
> [`scripts/deploy.sh`](scripts/deploy.sh). That deploy job was retired when the demo instance was
> decommissioned; it lives in git history if you want to wire up your own.

### Single-VM deploy

> **Full step-by-step single-VM prod deploy:** [`docs/DEPLOY.md`](docs/DEPLOY.md) (provision → secrets →
> `.env` → one command → optional HTTPS). The summary below is the short version.

Deployment is the same command on any Docker host (e.g. an EC2 instance):
`docker compose up --build`. Production mode (auth + PHI encryption + the Operator|Member
login toggle) is driven entirely by the root `.env` — the compose file forwards `APP_ENV`,
`AUTH_ENABLED`, `JWT_SECRET`, `PHI_ENCRYPTION_*`, `SHOW_ROLE_HELP`, and the seed passwords with
dev-safe defaults, so the **same one command** runs dev (no `.env` vars) or prod (vars set). The security group / firewall should expose **only port 80**
(the frontend; nginx reverse-proxies `/api` to the backend). The internal services —
**Postgres, Redis, and (optional) MinIO — are published only on `127.0.0.1`** (loopback),
so they are reachable for local dev on the host but are NOT exposed on `0.0.0.0` to the
network; keep ports 5432 / 6379 / 9000 / 9001 closed in the firewall regardless. The
backend has a healthcheck and the frontend waits for it to be healthy, so nginx does not
502 during boot. Set `GEMINI_API_KEY` in the root `.env` on the host.

**Single-VM operational guardrails (built into the Compose file):**

- **Non-root containers** — the backend runs as `appuser` (uid 10001); the frontend is the
  official **unprivileged** nginx (uid 101, listening on 8080, published as host `:80`).
- **Log rotation** — every service uses the `json-file` driver capped at `10m × 3` files, so
  container logs can't fill the VM disk on a long-running host.
- **Resource limits** — each service has a `mem_limit`/`cpus` cap so no single container can
  starve the host. The defaults (backend/worker 1g, db 512m, redis 256m, frontend 128m) are
  conservative; tune them to your VM.
- **Auto-restart + healthchecks** on all long-running services (`restart: unless-stopped`).

### HTTPS (optional overlay)

For a public URL, terminate TLS with the bundled Caddy overlay — it auto-provisions and renews
a Let's Encrypt certificate for your domain (no manual certbot):

```bash
DOMAIN=claims.example.com \
  docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build
```

`$DOMAIN` must resolve (A/AAAA) to the VM and ports **80 + 443** must be open (80 is used for
the ACME challenge). Caddy proxies to the frontend over the internal network, so with the
overlay the frontend no longer publishes its own host port.

### Production hardening

These are deliberate **off-by-default** toggles; turn them on for a real deployment:

- `POSTGRES_PASSWORD=<strong value>` — the DB password defaults to `plum` for local use;
  set a strong one for any deploy. The backend/worker `DATABASE_URL` is composed from
  `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`, so the change propagates automatically.
- `AUTH_ENABLED=true` + a strong `JWT_SECRET` — enables JWT auth + member/ops RBAC.
- `OPS_DEFAULT_PASSWORD` / `MEMBER_DEFAULT_PASSWORD` — the seeded account passwords. Their
  defaults are documented in this repo, so **you must set strong ones for any auth-on deploy**
  (otherwise anyone reading the source could log in).
- `PHI_ENCRYPTION_KEY=<strong key>` (with `PHI_ENCRYPTION_ENABLED=true`) — at-rest PHI encryption.
- `APP_ENV=production` — makes insecure defaults a **hard boot refusal** rather than a warning:
  with auth on, a dev `JWT_SECRET` or the documented default ops/member passwords; with
  encryption on, an empty `PHI_ENCRYPTION_KEY`. The server won't start until they're real values.
- Restrict CORS — the app currently allows all origins (`*`); pin it to your frontend origin.
- Add rate limiting in front of the API (e.g. at nginx) for the public endpoints.

### Reproducible builds

The backend image installs a fully-pinned dependency closure from
[`backend/requirements.lock`](backend/requirements.lock) (89 packages, resolved against
`python:3.12-slim`), then installs the app itself with `--no-deps` — so an image built today
and one built months from now resolve to the **same** versions. The lock is committed; the
loose `>=` ranges in `pyproject.toml` remain the human-facing declaration. To refresh it after
changing dependencies, install the project in a clean `python:3.12-slim` and
`pip freeze --exclude-editable > backend/requirements.lock`.

**Deployment:** ran in true-prod on a single AWS VM (JWT auth + RBAC + at-rest PHI encryption,
HTTPS via Caddy + Let's Encrypt). The demo instance has since been decommissioned — reproduce
with `make tls DOMAIN=your.fqdn` or the steps above.

---

## Local development (without Docker)

**Backend** (Python 3.12):

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp ../.env.example .env        # set GEMINI_API_KEY=...
docker compose up -d db        # bring up just Postgres (from the repo root)
.venv/bin/uvicorn app.main:app --port 8000
```

Postgres is published on **localhost:5432** by the committed Compose file, which matches the
default `DATABASE_URL`.

**Frontend** (Node):

```bash
cd frontend
npm install
npm run dev                     # Vite dev server; proxies /api → http://localhost:8000
```

Open the Vite URL it prints (default http://localhost:5173). The backend must be on port 8000
for the dev proxy.

---

## Configuration

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `GEMINI_API_KEY` | **yes** | — | Google Gemini API key (vision extraction, semantic map, verifier) |
| `GEMINI_MODEL` | no | `gemini-flash-latest` | Extraction + semantic-map model (→ 3.5-flash; free tier) |
| `GEMINI_PRO_MODEL` | no | `gemini-pro-latest` | LLM-as-judge verifier + self-correction (→ 3.1-pro-preview; **paid**). Set to a Flash model to stay free. |
| `DATABASE_URL` | no | `postgresql+psycopg://plum:plum@localhost:5432/claims` | Postgres connection (Compose overrides to host `db`) |
| `POLICY_PATH` | no | `policy_terms.json` | Policy config (resolved against repo root) |
| `TEST_CASES_PATH` | no | `test_cases.json` | The 12 eval cases |
| `STORAGE_DIR` | no | `storage` | On-disk uploads / rendered fixtures / eval output |
| `DEGRADATION_PENALTY` | no | `0.20` | Per-failed-component confidence penalty |
| `REDIS_URL` | no | `redis://localhost:6379/0` | Celery broker + result backend (async processing) |
| `APP_ENV` | no | `development` | `production` makes insecure security defaults a hard boot refusal (see Production hardening) |
| `AUTH_ENABLED` | no | `false` | Enable self-issued JWT auth + RBAC (see below). **OFF by default.** |
| `JWT_SECRET` | no | `dev-insecure-change-me` | HS256 signing secret. **Override in production.** |
| `JWT_ALGORITHM` | no | `HS256` | JWT signing algorithm |
| `JWT_EXPIRE_MINUTES` | no | `720` | Access-token lifetime (minutes) |
| `OPS_DEFAULT_PASSWORD` | no | `ops-dev-password` | Seed password for the `ops` account |
| `MEMBER_DEFAULT_PASSWORD` | no | `member-dev-password` | Seed password for each member account |
| `OBJECT_STORE` | no | `local` | `local` (on-disk under `STORAGE_DIR`) or `minio` (self-hosted S3-compatible) |
| `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` / `MINIO_BUCKET` | no | `localhost:9000` / `minioadmin` / `minioadmin` / `claims` | MinIO connection (only when `OBJECT_STORE=minio`) |
| `PHI_ENCRYPTION_ENABLED` | no | `false` | Transparent at-rest PHI encryption of claim JSONB. **OFF by default.** |
| `PHI_ENCRYPTION_KEY` | no | — | Fernet key for PHI encryption (a weak dev key is derived from `JWT_SECRET` if empty) |
| `CONFIDENCE_CALIBRATION_ENABLED` | no | `false` | Map composite confidence through a fitted calibrator |
| `RESILIENCE_ENABLED` | no | `true` | Circuit breaker / model fallback / concurrency cap (engages only on failure) |
| `ADAPTIVE_ROUTING_ENABLED` | no | `true` | Supervisor fans out only to applicable rule agents (skips provably-PASS rules) |

> **MinIO is optional.** `OBJECT_STORE=local` (the default) needs no MinIO service. To use it,
> start the optional MinIO container with `docker compose --profile minio up` and set
> `OBJECT_STORE=minio` (+ the `MINIO_*` vars) on the backend/worker.

---

## Authentication and RBAC

The backend ships self-issued JWT auth with member-vs-ops role-based access control. It is
**gated OFF by `AUTH_ENABLED=false` (the default)**: with auth off, every endpoint, the 12/12
eval, the UI, and the existing API/documents tests behave exactly as before — no token is
required and no users table is needed (the RBAC dependencies are permissive no-ops that return
a synthetic `system`/ops principal).

**When `AUTH_ENABLED=true`:**

- `POST /api/auth/login` (`{username, password}`) → `{access_token, token_type, role, member_id}`.
- `GET /api/auth/me` returns the principal carried by the bearer token.
- Submit / list / read require a valid bearer token. A **member** is scoped to claims where
  `claims.member_id == their member_id` (the list is filtered; foreign detail/documents → 403).
  **Ops** can read all claims, list members, and run/inspect evals (`/api/eval/*` is ops-only).

**Seeded dev accounts** (idempotent `seed_users()` runs best-effort at startup): one `ops`
account (`ops` / `ops-dev-password`) and one **member** account per policy member with the
username equal to the member id (`EMP001`, `EMP002`, …, `DEP001`, `DEP002`) and password
`member-dev-password`. Change these via the env vars above; never use the defaults in production.

Crypto: passwords are hashed with `bcrypt` (used directly), tokens are signed with HS256 via
`PyJWT`. No external IdP. Apply the additive users table with `alembic upgrade head`
(migration `0003_users`).

---

## Testing

**All tests are live — no mocks, stubs, or recorded responses anywhere.** Deterministic
components are exercised as pure functions with real inputs; LLM-touching components and the
full 12-case pipeline run against the live Gemini API. Tests are split by the `live` marker.

```bash
cd backend
.venv/bin/pytest -m "not live"   # fast deterministic suite — 397 tests (Postgres up for the DB-backed ones)
.venv/bin/pytest -m live         # live-Gemini suite — 31 tests (needs GEMINI_API_KEY + network + Postgres up)
.venv/bin/pytest                 # everything — 428 tests
```

The deterministic suite (300+ passing) covers PolicyEngine, each adjudication rule,
FinancialCalculator (discount→copay order), the identity matcher, confidence, DocGate,
DecisionAggregator, eval matching, and the renderer. The live suite covers extraction,
semantic map, verifier, the full graph end to end, and the API. LLM-touching tests assert on
**invariants** (schema validity, load-bearing field ranges, decision correctness), not exact
strings, so they are robust to vision's natural variation.

---

## Eval

The eval runs all 12 cases from `test_cases.json` through the **real** pipeline (live vision):
each case is rendered into real fixture documents, processed end to end, and matched against
its expected outcome.

- **UI:** open the **Eval** page and click **"Run all 12 cases (live)"** — expected-vs-actual
  table with per-case trace drill-down.
- **API:** `POST /api/eval/run` — returns the per-case results and writes the report.
- **Report:** [`docs/eval_report.md`](docs/eval_report.md) (currently **12/12 MATCH**).

### Beyond the 12 cases — additional evals

The 12 provided cases use synthetic fixtures, so we also evaluate against **real-world data and
edge behaviour** (not required by the brief):

- **Extraction robustness on REAL public documents** — runs the production vision extractor over
  real datasets (e.g. CORD v2 receipts) scored against ground truth.
  [`docs/extraction_robustness_report.md`](docs/extraction_robustness_report.md). Datasets are
  fetched reproducibly via [`backend/scripts/download_eval_datasets.py`](backend/scripts/download_eval_datasets.py)
  (not committed — license-safe, see [`docs/eval_datasets_attribution.md`](docs/eval_datasets_attribution.md)).
- **Decision-quality eval** over synthetic adversarial claims —
  [`docs/decision_eval_report.md`](docs/decision_eval_report.md).
- **Member-message quality** (specificity/actionability of user-facing errors) —
  [`docs/message_quality_report.md`](docs/message_quality_report.md).

---

## Project structure

```
backend/app/
  main.py            FastAPI app: middleware, lifespan + router wiring
  api/               REST routers: auth, intake, claims_read, eval, explain, ops_actions, policy, assistant
  config.py          settings (env, model names, asset-path resolution)
  graph/             LangGraph pipeline: state.py, nodes.py, build.py
  agents/            LLM agents: extraction.py, semantic_map.py, verifier.py
  rules/             deterministic: policy_engine* , docgate, waiting_period,
                     coverage_exclusion, pre_auth, limits, fraud, financial, aggregator, base
  services/          gemini client, identity (fuzzy match), confidence, persistence, policy_engine
  models/schemas.py  Pydantic data models
  fixtures/          synthetic-document renderer + loader (real PDF/JPEG from test_cases.json)
  evalrunner/        runner + expected-vs-actual matching
backend/tests/       live + deterministic test suites
frontend/src/
  pages/             Login, Submit, Claims, Claim (review), Ops dashboard, Worklist, Fraud, Policy studio, Eval
  components/         VerdictCard, FinancialTable, TraceTimeline, ConfidenceBar, FileDrop
docs/                architecture.md, contracts.md, eval_report.md
docker-compose.yml   6 services: db (Postgres), redis, backend, worker (Celery),
                     frontend (nginx), minio (optional, --profile minio)
```

---

## Deliverables

| Deliverable | Link |
|---|---|
| Demo video (narrated walk-through) | <https://youtu.be/UPxC4o24bQs> |
| Eval report (12 / 12 passing) | [`docs/eval_report.md`](docs/eval_report.md) |
| Architecture document | [`docs/architecture.md`](docs/architecture.md) |
| Component contracts | [`docs/contracts.md`](docs/contracts.md) |
| Assignment brief (reference) | [`assignment.md`](assignment.md) |
