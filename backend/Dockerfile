FROM python:3.12-slim
WORKDIR /srv
# Reproducible builds: install the fully-pinned dependency closure first (this layer is
# cached until requirements.lock changes), then install the app itself with --no-deps so
# pip never re-resolves to newer versions. Regenerate the lock with:
#   docker build -f - . <<'EOF' ... pip freeze  (see README "Reproducible builds")
COPY backend/pyproject.toml backend/requirements.lock ./backend/
RUN pip install --no-cache-dir -r backend/requirements.lock
COPY backend ./backend
COPY policy_terms.json test_cases.json ./
RUN pip install --no-cache-dir -e ./backend --no-deps
# Run as a non-root user. Create the storage dir and hand /srv to the user BEFORE
# the named volume mounts onto /srv/backend/storage, so the volume inherits this
# ownership on first creation and the app (uid 10001) can write uploads/fixtures.
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /srv/backend/storage \
 && chown -R appuser:appuser /srv
WORKDIR /srv/backend
ENV POLICY_PATH=/srv/policy_terms.json TEST_CASES_PATH=/srv/test_cases.json STORAGE_DIR=/srv/backend/storage
USER appuser
# Liveness probe baked into the image (the slim base has no curl, so use Python's
# stdlib). Hits the always-200 /api/health endpoint; the orchestrator can use this
# even outside docker-compose. Readiness (DB reachable) is the separate /api/ready.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=2).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
