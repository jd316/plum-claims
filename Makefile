# Plum Claims — root control surface. Thin wrappers around the real tools; run `make help`.
# Linux/macOS have make preinstalled; on Windows use WSL.

PROJECT := plumclaims
COMPOSE := docker compose -p $(PROJECT)
PROD    := $(COMPOSE) -f docker-compose.yml -f docker-compose.prod.yml
TLS     := $(PROD) -f docker-compose.tls.yml
PY      := backend/.venv/bin/python
PIP     := backend/.venv/bin/pip

.DEFAULT_GOAL := help

# ---- Setup ----------------------------------------------------------------
setup: ## Create the backend venv + install backend (.[dev]) and frontend deps
	python3 -m venv backend/.venv
	$(PIP) install --upgrade pip
	$(PIP) install -r backend/requirements.lock
	$(PIP) install -e "backend/.[dev]" ruff pyright
	npm --prefix frontend ci

migrate: ## Apply DB migrations (alembic upgrade head)
	cd backend && .venv/bin/alembic upgrade head

# ---- Quality (mirrors CI) -------------------------------------------------
test: ## Backend deterministic suite (pytest -m "not live")
	cd backend && .venv/bin/python -m pytest -m "not live" -q

test-live: ## Backend live-Gemini suite (needs GEMINI_API_KEY + Postgres)
	cd backend && .venv/bin/python -m pytest -m live -q

lint: ## Ruff lint (backend)
	cd backend && .venv/bin/ruff check .

types: ## Pyright type-check (backend)
	cd backend && .venv/bin/pyright --pythonpath .venv/bin/python

web-check: ## Frontend type-check + lint + production build
	npm --prefix frontend run build
	npm --prefix frontend run lint

check: lint types test web-check ## Run everything CI runs (pre-push gate)

# ---- Local stack (docker) -------------------------------------------------
dev: ## Bring up the stack in OPEN/dev mode (no auth) on http://localhost
	$(COMPOSE) up --build -d

prod: ## Bring up the stack in TRUE-PROD mode (auth + PHI encryption); reads root .env
	$(PROD) up -d --build

tls: ## Prod + HTTPS via Caddy (requires DOMAIN=your.fqdn)
	@test -n "$(DOMAIN)" || { echo "Set DOMAIN=your.fqdn (e.g. make tls DOMAIN=claims.example.com)"; exit 1; }
	$(TLS) up -d --build

ps: ## Show stack status
	$(COMPOSE) ps

logs: ## Tail stack logs (override svc: make logs svc=backend)
	$(COMPOSE) logs -f $(svc)

down: ## Stop the stack (keeps volumes/data)
	$(COMPOSE) down

# ---- Local dev outside docker ---------------------------------------------
db: ## Start just Postgres (for host-side backend dev)
	$(COMPOSE) up -d db

api: ## Run the backend with reload on :8000 (needs `make db` + venv)
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

web: ## Run the Vite dev server (proxies /api -> :8000)
	npm --prefix frontend run dev

# ---- Eval -----------------------------------------------------------------
eval: ## Run the 12 test cases through the live pipeline -> docs/eval_report.md
	cd backend && .venv/bin/python -c "from app.evalrunner.runner import run_all, to_markdown; open('../docs/eval_report.md','w').write(to_markdown(run_all()))"
	@echo "Wrote docs/eval_report.md"

# ---- Demo data ------------------------------------------------------------
seed-demo: ## Persist the 12 cases into the running stack (populates member + Ops views)
	$(COMPOSE) exec -T backend python scripts/seed_demo_data.py --clear

clear-demo: ## Wipe the seeded demo claims + audit log from the running stack
	$(COMPOSE) exec -T backend python scripts/seed_demo_data.py --clear-only

# ---- Clean ----------------------------------------------------------------
clean: ## Stop stack + REMOVE volumes, venv, node_modules, caches (destructive)
	$(COMPOSE) down -v || true
	rm -rf backend/.venv frontend/node_modules backend/.pytest_cache
	find backend -type d -name __pycache__ -prune -exec rm -rf {} +

help: ## Show this help
	@awk 'BEGIN{FS=":.*## "} /^[a-zA-Z0-9_-]+:.*## /{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: setup migrate test test-live lint types web-check check dev prod tls ps logs down db api web eval clean seed-demo clear-demo help
